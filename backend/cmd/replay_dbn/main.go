// Package main runs the FlowGreeks DBN file replay binary.
//
// Reads pre-pulled Databento DBN files from disk, decodes them via dbn-go,
// normalizes via the same converters used by cmd/ingest, and publishes
// onto NATS using the canonical ticks.* subject hierarchy. cmd/compute
// consumes the resulting tick stream and populates dealer_state_1s rows
// for the day, allowing the existing backtest API to run against real
// historical state without an active Databento Live connection.
//
// Per-day directory layout (input):
//
//	data/databento/<YYYY-MM-DD>/
//	  OPRA_PILLAR/  (definition, tcbbo, trades, statistics) × {SPX, NDX}
//	  GLBX_MDP3/    (mbp-1, trades) × {ES, NQ}
//
// Phase 1 drains every definition file into the shared instrument
// registry without publishing. Phase 2 opens every streaming file
// simultaneously and merges them by ts_event using a min-heap, popping
// the earliest record, converting + publishing, then advancing the
// source it came from.
package main

import (
	"bufio"
	"container/heap"
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	dbn "github.com/NimbleMarkets/dbn-go"
	"github.com/klauspost/compress/zstd"

	"flowgreeks/internal/bus"
	"flowgreeks/internal/feed"
	"flowgreeks/internal/feed/databento"
	"flowgreeks/internal/logger"
)

const (
	serviceName   = "replay_dbn"
	progressEvery = 100_000
	gapCap        = 5 * time.Second // collapse big idle gaps when paced
	readBufSize   = 1 << 20         // 1 MiB bufio reader on top of zstd
)

// streamSchemas is the set of file prefixes consumed in the streaming
// merge pass. Files with other prefixes (definition) are handled
// separately or ignored.
var streamSchemas = []string{"tcbbo", "mbp-1", "trades", "statistics"}

func main() {
	var (
		dirFlag      = flag.String("dir", "", "trading-day directory under data/databento (e.g. data/databento/2026-02-02)")
		natsURL      = flag.String("nats-url", "nats://localhost:4222", "NATS connection URL")
		speed        = flag.Float64("speed", 0, "playback speed multiplier; 0 = unpaced (default), 1 = realtime, 10 = 10x")
		symbolFilter = flag.String("symbol-filter", "both", "symbol filter: spx | ndx | both")
		logFormat    = flag.String("log-format", "text", "log format: text or json")
		logLevel     = flag.String("log-level", "info", "log level: debug, info, warn, error")
	)
	flag.Parse()

	if strings.TrimSpace(*dirFlag) == "" {
		fmt.Fprintln(os.Stderr, "replay_dbn: -dir is required")
		os.Exit(2)
	}
	allowed, err := parseSymbolFilter(*symbolFilter)
	if err != nil {
		fmt.Fprintln(os.Stderr, err.Error())
		os.Exit(2)
	}

	log := logger.New(*logFormat, *logLevel).With("service", serviceName)

	rootCtx, cancel := context.WithCancel(context.Background())
	defer cancel()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	go func() {
		sig := <-stop
		log.Info("shutdown signal received", "signal", sig.String())
		cancel()
	}()

	pub, err := bus.NewPublisher(rootCtx, *natsURL)
	if err != nil {
		log.Error("nats publisher init failed", "err", err)
		os.Exit(1)
	}
	defer func() {
		if err := pub.Close(); err != nil {
			log.Warn("publisher close", "err", err)
		}
	}()
	log.Info("nats publisher connected", "url", *natsURL)

	registry := databento.NewRegistry()

	defs, err := discoverFiles(*dirFlag, []string{"definition"})
	if err != nil {
		log.Error("walk dir failed", "dir", *dirFlag, "err", err)
		os.Exit(1)
	}
	log.Info("draining definition files", "count", len(defs))
	for _, p := range defs {
		n, err := drainDefinitions(p, registry)
		if err != nil {
			log.Warn("definition drain failed", "path", p, "err", err)
			continue
		}
		log.Info("definition drained", "path", filepath.Base(p), "instruments", n, "registry_size", registry.Size())
	}
	log.Info("registry populated", "instruments", registry.Size())

	streamPaths, err := discoverFiles(*dirFlag, streamSchemas)
	if err != nil {
		log.Error("walk dir failed", "dir", *dirFlag, "err", err)
		os.Exit(1)
	}
	if len(streamPaths) == 0 {
		log.Warn("no streaming files found", "dir", *dirFlag)
		return
	}

	sources := make([]*source, 0, len(streamPaths))
	for _, p := range streamPaths {
		s, err := openSource(p, registry)
		if err != nil {
			log.Warn("open source failed", "path", p, "err", err)
			continue
		}
		if !advance(s) {
			s.close()
			log.Info("source empty", "path", filepath.Base(p))
			continue
		}
		sources = append(sources, s)
		log.Info("source opened",
			"path", filepath.Base(p),
			"first_ts", time.Unix(0, int64(s.nextTick.TsEvent)).UTC().Format(time.RFC3339Nano))
	}
	if len(sources) == 0 {
		log.Warn("no readable sources after open")
		return
	}

	runMerge(rootCtx, log, pub, sources, allowed, *speed)
}

// runMerge drives the heap-merge loop: pop earliest → publish → advance.
// Pacing is applied based on the gap between successive ts_event values
// when speed > 0; otherwise records flow as fast as NATS will accept.
func runMerge(
	ctx context.Context,
	log interface{ Info(string, ...any); Warn(string, ...any); Error(string, ...any) },
	pub *bus.Publisher,
	sources []*source,
	allowed map[feed.Symbol]bool,
	speed float64,
) {
	h := sourceHeap(sources)
	heap.Init(&h)

	var (
		records   uint64
		published uint64
		pubErrs   uint64
		filtered  uint64
		started   = time.Now()
		lastEvent uint64
		paced     = speed > 0
	)
	defer func() {
		log.Info("replay complete",
			"records", records,
			"published", published,
			"filtered", filtered,
			"publish_errors", pubErrs,
			"elapsed", time.Since(started).Round(time.Millisecond))
	}()

	for h.Len() > 0 {
		if ctx.Err() != nil {
			return
		}
		s := heap.Pop(&h).(*source)
		t := s.nextTick

		if paced && lastEvent != 0 && t.TsEvent > lastEvent {
			gap := time.Duration(t.TsEvent - lastEvent)
			sleep := time.Duration(float64(gap) / speed)
			if sleep > gapCap {
				sleep = gapCap
			}
			if sleep > 0 {
				select {
				case <-time.After(sleep):
				case <-ctx.Done():
					return
				}
			}
		}
		lastEvent = t.TsEvent

		records++
		if !allowed[t.Symbol] {
			filtered++
		} else if err := pub.Publish(ctx, t); err != nil {
			pubErrs++
			if pubErrs < 10 || pubErrs%1000 == 0 {
				log.Warn("nats publish failed", "err", err, "symbol", t.Symbol, "tick_type", t.TickType)
			}
		} else {
			published++
		}

		if records%progressEvery == 0 {
			elapsed := time.Since(started).Round(time.Millisecond)
			rate := uint64(float64(records) / elapsed.Seconds())
			log.Info("progress",
				"elapsed", elapsed,
				"records", records,
				"published", published,
				"filtered", filtered,
				"publish_errors", pubErrs,
				"rate_per_sec", rate,
				"open_sources", h.Len()+1)
		}

		if advance(s) {
			heap.Push(&h, s)
		} else {
			s.close()
		}
	}
}

// ─── source + advance ─────────────────────────────────────────────────

// source wraps a single .dbn.zst file: opened handle, decompressor,
// scanner, capture visitor, and the next publishable tick already
// peeked. Heap ordering uses nextTick.TsEvent.
type source struct {
	path     string
	file     *os.File
	zr       *zstd.Decoder
	scanner  *dbn.DbnScanner
	visitor  *captureVisitor
	nextTick feed.Tick
}

// openSource opens path, attaches a zstd decoder + buffered reader +
// dbn scanner, and prepares a capture visitor bound to the shared
// registry. Caller must invoke close() when the source is exhausted.
func openSource(path string, registry *databento.Registry) (*source, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open %s: %w", path, err)
	}
	zr, err := zstd.NewReader(f)
	if err != nil {
		_ = f.Close()
		return nil, fmt.Errorf("zstd %s: %w", path, err)
	}
	br := bufio.NewReaderSize(zr, readBufSize)
	return &source{
		path:    path,
		file:    f,
		zr:      zr,
		scanner: dbn.NewDbnScanner(br),
		visitor: &captureVisitor{registry: registry},
	}, nil
}

// close releases OS handles. Idempotent.
func (s *source) close() {
	if s.zr != nil {
		s.zr.Close()
		s.zr = nil
	}
	if s.file != nil {
		_ = s.file.Close()
		s.file = nil
	}
}

// advance reads forward in the source until either the visitor produces
// a publishable Tick (definition / mapping records are absorbed into the
// registry; statistics + status records are dropped) or the scanner is
// exhausted. Returns true if s.nextTick is now valid.
func advance(s *source) bool {
	v := s.visitor
	for s.scanner.Next() {
		v.ok = false
		_ = s.scanner.Visit(v) // visitor errors are non-fatal; bad record is dropped
		if v.ok {
			s.nextTick = v.out
			return true
		}
	}
	return false
}

// ─── visitor ──────────────────────────────────────────────────────────

// captureVisitor implements dbn.Visitor by routing definition + mapping
// records into the shared registry and capturing the latest converted
// market-data tick into out / ok. Non-relevant record types fall
// through to NullVisitor (no-op).
type captureVisitor struct {
	dbn.NullVisitor
	registry *databento.Registry
	out      feed.Tick
	ok       bool
}

func (v *captureVisitor) OnInstrumentDefMsg(r *dbn.InstrumentDefMsg) error {
	v.registry.Resolve(r.Header.InstrumentID, databento.DbnFixedString(r.RawSymbol[:]))
	return nil
}

func (v *captureVisitor) OnSymbolMappingMsg(r *dbn.SymbolMappingMsg) error {
	v.registry.Resolve(r.Header.InstrumentID, r.StypeOutSymbol)
	return nil
}

func (v *captureVisitor) OnCmbp1(r *dbn.Cmbp1Msg) error {
	if v.registry.ConvertCmbp1(&v.out, r, uint64(time.Now().UnixNano())) {
		v.ok = true
	}
	return nil
}

func (v *captureVisitor) OnMbp1(r *dbn.Mbp1Msg) error {
	if v.registry.ConvertMbp1(&v.out, r, uint64(time.Now().UnixNano())) {
		v.ok = true
	}
	return nil
}

func (v *captureVisitor) OnMbp0(r *dbn.Mbp0Msg) error {
	if v.registry.ConvertTrade(&v.out, r, uint64(time.Now().UnixNano())) {
		v.ok = true
	}
	return nil
}
