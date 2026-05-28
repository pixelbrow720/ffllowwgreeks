// Package main runs the FlowGreeks replay binary.
//
// Reads historical ticks from TimescaleDB and re-emits them onto the
// same NATS subjects as live ingest. This lets compute + api consume
// historical sessions without needing the live vendor gateway.
//
// Usage:
//
//	./replay --symbol spx --date 2026-05-21 --speed 4
//	./replay --symbol ndx --start 2026-05-21T13:30:00Z --end 2026-05-21T14:30:00Z --speed 1
//
// Speed: 0 = unpaced (as fast as possible), 1 = real-time, N = N× faster.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"flowgreeks/internal/bus"
	"flowgreeks/internal/config"
	"flowgreeks/internal/feed"
	"flowgreeks/internal/logger"
	"flowgreeks/internal/replay"

	"github.com/jackc/pgx/v5/pgxpool"
)

const serviceName = "replay"

func main() {
	var (
		symStr   = flag.String("symbol", "spx", "symbol to replay (spx|ndx)")
		dateStr  = flag.String("date", "", "single-day shortcut: YYYY-MM-DD (UTC). Replays 13:30→20:15 UTC (RTH).")
		startStr = flag.String("start", "", "start timestamp RFC3339 (overrides --date)")
		endStr   = flag.String("end", "", "end timestamp RFC3339 (overrides --date)")
		speed    = flag.Float64("speed", 4.0, "replay speed multiplier (0 = unpaced)")
	)
	flag.Parse()

	cfg, err := config.Load()
	if err != nil {
		_, _ = os.Stderr.WriteString("config load failed: " + err.Error() + "\n")
		os.Exit(1)
	}

	log := logger.New(cfg.Log.Format, cfg.Log.Level).With(
		"service", serviceName,
		"env", cfg.AppEnv,
	)

	sym := feed.ParseSymbol(strings.ToUpper(*symStr))
	if sym == feed.SymbolUnknown {
		log.Error("unknown symbol", "symbol", *symStr)
		os.Exit(1)
	}

	rng, err := resolveRange(sym, *dateStr, *startStr, *endStr)
	if err != nil {
		log.Error("invalid range", "err", err)
		os.Exit(1)
	}

	rootCtx, cancel := context.WithCancel(context.Background())
	defer cancel()

	pool, err := pgxpool.New(rootCtx, cfg.Postgres.DSN())
	if err != nil {
		log.Error("postgres connect failed", "err", err)
		os.Exit(1)
	}
	defer pool.Close()

	pub, err := bus.NewPublisher(rootCtx, cfg.NATS.URL)
	if err != nil {
		log.Error("nats publisher init failed", "err", err)
		os.Exit(1)
	}
	defer func() { _ = pub.Close() }()

	reader := replay.NewReader(pool)
	runner := replay.NewRunner(pub, log, *speed)

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-stop
		log.Info("shutdown signal received")
		cancel()
	}()

	log.Info("replay starting",
		"range", replay.FormatRange(rng),
		"speed", *speed,
		"nats", cfg.NATS.URL,
	)

	ticks, errs := reader.Stream(rootCtx, rng)
	go func() {
		for err := range errs {
			log.Error("reader error", "err", err)
			cancel()
		}
	}()

	if err := runner.Run(rootCtx, ticks); err != nil && err != context.Canceled {
		log.Error("runner exited with error", "err", err)
		os.Exit(1)
	}
	log.Info("replay stopped")
}

func resolveRange(sym feed.Symbol, dateStr, startStr, endStr string) (replay.Range, error) {
	if startStr != "" && endStr != "" {
		s, err := time.Parse(time.RFC3339, startStr)
		if err != nil {
			return replay.Range{}, fmt.Errorf("parse start: %w", err)
		}
		e, err := time.Parse(time.RFC3339, endStr)
		if err != nil {
			return replay.Range{}, fmt.Errorf("parse end: %w", err)
		}
		if !e.After(s) {
			return replay.Range{}, fmt.Errorf("end must be after start")
		}
		return replay.Range{Symbol: sym, Start: s, End: e}, nil
	}
	if dateStr == "" {
		return replay.Range{}, fmt.Errorf("either --date or both --start/--end required")
	}
	d, err := time.Parse("2006-01-02", dateStr)
	if err != nil {
		return replay.Range{}, fmt.Errorf("parse date: %w", err)
	}
	// Default RTH window in UTC during DST: 13:30 → 20:15. Replay session
	// boundary; M8 calendar service can refine for non-DST and half-days.
	start := time.Date(d.Year(), d.Month(), d.Day(), 13, 30, 0, 0, time.UTC)
	end := time.Date(d.Year(), d.Month(), d.Day(), 20, 15, 0, 0, time.UTC)
	return replay.Range{Symbol: sym, Start: start, End: end}, nil
}
