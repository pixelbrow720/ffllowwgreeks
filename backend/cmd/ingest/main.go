// Package main runs the FlowGreeks ingest binary.
//
// Responsibilities:
//   - Connect to Databento Live (OPRA.PILLAR + GLBX.MDP3) via dbn-go
//   - Subscribe to SPX/SPXW + NDX/NDXP options, plus ES/NQ futures
//   - For each normalized Tick: fan out to NATS (hot path) + archive writer (cold path)
//   - Expose Prometheus /metrics on a side port
//   - Graceful shutdown on SIGINT/SIGTERM
package main

import (
	"context"
	"errors"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"flowgreeks/internal/bus"
	"flowgreeks/internal/config"
	"flowgreeks/internal/feed"
	"flowgreeks/internal/feed/databento"
	"flowgreeks/internal/logger"
	"flowgreeks/internal/store"

	"github.com/prometheus/client_golang/prometheus/promhttp"
)

const (
	serviceName = "ingest"
	metricsAddr = ":9091" // separate from api :8080
)

func main() {
	cfg, err := config.Load()
	if err != nil {
		_, _ = os.Stderr.WriteString("config load failed: " + err.Error() + "\n")
		os.Exit(1)
	}
	if cfg.Databento.APIKey == "" {
		_, _ = os.Stderr.WriteString("DATABENTO_API_KEY is required\n")
		os.Exit(1)
	}

	log := logger.New(cfg.Log.Format, cfg.Log.Level).With(
		"service", serviceName,
		"env", cfg.AppEnv,
	)

	rootCtx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// ─── Wire publisher (NATS) ───
	pub, err := bus.NewPublisher(rootCtx, cfg.NATS.URL)
	if err != nil {
		log.Error("nats publisher init failed", "err", err)
		os.Exit(1)
	}
	defer func() {
		if err := pub.Close(); err != nil {
			log.Warn("publisher close", "err", err)
		}
	}()
	log.Info("nats publisher connected", "url", cfg.NATS.URL)

	// ─── Wire archive writer (TimescaleDB) ───
	archive, err := store.NewArchiveWriter(rootCtx, cfg.Postgres.DSN(), store.WriterOpts{})
	if err != nil {
		log.Error("archive writer init failed", "err", err)
		os.Exit(1)
	}
	defer func() {
		if err := archive.Close(); err != nil {
			log.Warn("archive close", "err", err)
		}
	}()

	archiveDone := make(chan error, 1)
	go func() {
		archiveDone <- archive.Run(rootCtx)
	}()
	log.Info("archive writer running")

	// ─── Wire feed (Databento) ───
	client, err := databento.New(databento.Config{
		APIKey:     cfg.Databento.APIKey,
		BufferSize: 8192,
		Diagnostic: os.Getenv("FG_DIAG") != "",
	})
	if err != nil {
		log.Error("databento client init failed", "err", err)
		os.Exit(1)
	}

	if err := client.Connect(rootCtx); err != nil {
		log.Error("databento connect failed", "err", err)
		os.Exit(1)
	}

	subs := []feed.Subscription{
		// OPRA.PILLAR consolidated schemas. definition + statistics are
		// required because the live gateway does not broadcast
		// SymbolMappingMsg for parent subscriptions — the adapter
		// bootstraps via Historical and definition keeps the registry
		// fresh as new strikes list intraday. statistics carries OI +
		// cumulative volume snapshots used by the dealer-position
		// estimator.
		{Dataset: feed.DatasetOPRAPillar, Schema: feed.SchemaDefinition, Symbol: feed.SymbolSPX},
		{Dataset: feed.DatasetOPRAPillar, Schema: feed.SchemaCMBP1, Symbol: feed.SymbolSPX},
		{Dataset: feed.DatasetOPRAPillar, Schema: feed.SchemaTrades, Symbol: feed.SymbolSPX},
		{Dataset: feed.DatasetOPRAPillar, Schema: feed.SchemaStatistics, Symbol: feed.SymbolSPX},
		{Dataset: feed.DatasetOPRAPillar, Schema: feed.SchemaDefinition, Symbol: feed.SymbolNDX},
		{Dataset: feed.DatasetOPRAPillar, Schema: feed.SchemaCMBP1, Symbol: feed.SymbolNDX},
		{Dataset: feed.DatasetOPRAPillar, Schema: feed.SchemaTrades, Symbol: feed.SymbolNDX},
		{Dataset: feed.DatasetOPRAPillar, Schema: feed.SchemaStatistics, Symbol: feed.SymbolNDX},
		// GLBX.MDP3 single-venue schemas for ES/NQ futures (basis tracking)
		{Dataset: feed.DatasetCMEGlobex, Schema: feed.SchemaMBP1, Symbol: feed.SymbolSPX},
		{Dataset: feed.DatasetCMEGlobex, Schema: feed.SchemaMBP1, Symbol: feed.SymbolNDX},
	}
	if err := client.Subscribe(rootCtx, subs); err != nil {
		log.Error("databento subscribe failed", "err", err)
		os.Exit(1)
	}

	if err := client.Start(rootCtx); err != nil {
		log.Error("databento start failed", "err", err)
		os.Exit(1)
	}
	log.Info("databento streaming", "subscriptions", len(subs))

	// ─── Metrics endpoint ───
	metricsSrv := &http.Server{
		Addr:              metricsAddr,
		Handler:           promhttp.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
	}
	go func() {
		log.Info("metrics listening", "addr", metricsAddr)
		if err := metricsSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("metrics server crashed", "err", err)
		}
	}()

	// ─── Hot path: Databento ticks → NATS + archive ───
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		runDispatch(rootCtx, log, client, pub, archive)
	}()

	// ─── Wait for signal ───
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)

	select {
	case sig := <-stop:
		log.Info("shutdown signal received", "signal", sig.String())
	case err := <-archiveDone:
		log.Error("archive writer exited unexpectedly", "err", err)
	}

	// ─── Graceful shutdown ───
	cancel()
	if err := client.Stop(); err != nil {
		log.Warn("databento stop", "err", err)
	}

	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer shutdownCancel()
	if err := metricsSrv.Shutdown(shutdownCtx); err != nil {
		log.Warn("metrics shutdown", "err", err)
	}

	wg.Wait()
	log.Info("ingest stopped")
}

// runDispatch is the hot path: read normalized ticks from the feed and fan
// them out to NATS (live consumers) and the archive writer (durable storage).
//
// NATS publish is synchronous but fast (sub-ms over loopback). Archive
// Write is non-blocking — drops on overflow with a counter increment.
func runDispatch(
	ctx context.Context,
	log interface{ Warn(string, ...any); Info(string, ...any); Error(string, ...any) },
	client *databento.Client,
	pub *bus.Publisher,
	archive *store.ArchiveWriter,
) {
	ticks := client.Ticks()
	errs := client.Errors()

	var (
		published uint64
		archived  uint64
		dropped   uint64
		pubErr    uint64
		lastLog   = time.Now()
	)
	logEvery := 10 * time.Second

	for {
		select {
		case <-ctx.Done():
			log.Info("dispatch stopping",
				"published", published, "archived", archived, "dropped", dropped, "publish_errors", pubErr)
			return

		case t, ok := <-ticks:
			if !ok {
				log.Info("ticks channel closed",
					"published", published, "archived", archived, "dropped", dropped)
				return
			}

			ttLabel := tickTypeLabel(t.IsFuture(), uint8(t.TickType))

			if err := pub.Publish(ctx, t); err != nil {
				pubErr++
				publishErrorsTotal.Inc()
				if pubErr < 10 || pubErr%1000 == 0 {
					log.Warn("nats publish failed", "err", err, "tick_type", t.TickType)
				}
			} else {
				published++
				publishedTotal.WithLabelValues(ttLabel).Inc()
			}

			if err := archive.Write(t); err != nil {
				dropped++
				if dropped < 10 || dropped%10000 == 0 {
					log.Warn("archive buffer full or closed", "err", err)
				}
			} else {
				archived++
			}

			if time.Since(lastLog) >= logEvery {
				log.Info("dispatch heartbeat",
					"published", published, "archived", archived,
					"dropped", dropped, "publish_errors", pubErr)
				lastLog = time.Now()
			}

		case err, ok := <-errs:
			if !ok {
				continue
			}
			feedErrorsTotal.Inc()
			log.Warn("feed non-fatal error", "err", err)
		}
	}
}
