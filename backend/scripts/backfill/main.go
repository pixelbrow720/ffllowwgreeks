// Historical backfill skeleton for FlowGreeks.
//
// Pulls a date range of OPRA.PILLAR + GLBX.MDP3 ticks from the
// Databento Historical API, normalises them through the same
// feed.Tick converters compute uses, and writes them via
// store.ArchiveWriter into the same `ticks` hypertable the live
// ingest binary populates.
//
// Status (2026-05-26): SKELETON — Databento account is currently
// locked, so the actual GetRange + DBN scanner loop is gated behind
// the --execute flag. Without --execute, this binary parses flags,
// validates the target window, prints the planned actions, and
// exits 0. Once the account is unlocked, flip --execute true to
// run the real backfill.
//
// Usage:
//
//   # plan only (default; safe with locked account)
//   go run ./scripts/backfill -from 2026-05-01 -to 2026-05-02
//
//   # actually run (after Databento unlock)
//   go run ./scripts/backfill -from 2026-05-01 -to 2026-05-02 -execute
//
// Idempotency: relies on UNIQUE(ts, symbol, expiry, strike, side,
// tick_type, instrument_id) at the table level. Until that index
// ships (M6 launch hardening), running twice over the same window
// will produce duplicate rows.

package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"flowgreeks/internal/config"
	"flowgreeks/internal/feed"
	"flowgreeks/internal/store"
)

const (
	maxWindowDays      = 7
	weekendIsoSaturday = time.Saturday
	weekendIsoSunday   = time.Sunday
)

func main() {
	from := flag.String("from", "", "inclusive start date YYYY-MM-DD (UTC)")
	to := flag.String("to", "", "exclusive end date YYYY-MM-DD (UTC)")
	dataset := flag.String("dataset", "OPRA.PILLAR", "Databento dataset (OPRA.PILLAR or GLBX.MDP3)")
	symbols := flag.String("symbols", "SPX.OPT,NDX.OPT", "comma-separated parent symbols")
	execute := flag.Bool("execute", false, "actually run; default plans only")
	flag.Parse()

	if *from == "" || *to == "" {
		log.Fatal("--from and --to are required (YYYY-MM-DD)")
	}
	startTs, err := time.Parse("2006-01-02", *from)
	if err != nil {
		log.Fatalf("--from: %v", err)
	}
	endTs, err := time.Parse("2006-01-02", *to)
	if err != nil {
		log.Fatalf("--to: %v", err)
	}
	if !endTs.After(startTs) {
		log.Fatal("--to must be after --from")
	}
	if endTs.Sub(startTs) > maxWindowDays*24*time.Hour {
		log.Fatalf("window > %dd; chunk into smaller runs", maxWindowDays)
	}

	cfg, err := config.Load()
	if err != nil {
		log.Fatalf("config: %v", err)
	}
	if cfg.Databento.APIKey == "" {
		log.Fatal("DATABENTO_API_KEY is required (set in .env)")
	}

	parents := splitCSV(*symbols)
	tradingDays := tradingDays(startTs, endTs)

	fmt.Println("─── backfill plan ─────────────────────────────────")
	fmt.Printf("dataset           %s\n", *dataset)
	fmt.Printf("symbols           %v\n", parents)
	fmt.Printf("window            [%s, %s)\n",
		startTs.Format("2006-01-02"), endTs.Format("2006-01-02"))
	fmt.Printf("trading days      %d (skipping weekends)\n", len(tradingDays))
	fmt.Printf("postgres          %s@%s:%d/%s\n",
		cfg.Postgres.User, cfg.Postgres.Host, cfg.Postgres.Port, cfg.Postgres.Database)
	fmt.Printf("execute           %v\n", *execute)
	fmt.Println()

	if !*execute {
		fmt.Println("DRY RUN — pass --execute to run. Note: requires Databento")
		fmt.Println("account unlock; see docs/PROGRESS.md decisions log.")
		return
	}

	rootCtx, cancel := context.WithCancel(context.Background())
	defer cancel()
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	go func() { <-stop; cancel() }()

	archive, err := store.NewArchiveWriter(rootCtx, cfg.Postgres.DSN(), store.WriterOpts{})
	if err != nil {
		log.Fatalf("archive writer: %v", err)
	}
	defer func() { _ = archive.Close() }()

	done := make(chan error, 1)
	go func() { done <- archive.Run(rootCtx) }()

	for _, day := range tradingDays {
		if err := backfillDay(rootCtx, cfg.Databento.APIKey, *dataset, parents, day, archive); err != nil {
			log.Printf("day %s failed: %v", day.Format("2006-01-02"), err)
			continue
		}
		log.Printf("day %s OK", day.Format("2006-01-02"))
	}

	cancel()
	if err := <-done; err != nil && err != context.Canceled {
		log.Printf("archive writer exit: %v", err)
	}
}

// backfillDay is the per-day worker. Marked TODO until Databento
// account is unlocked — once it is, this fetches the day's tick range
// via dbn_hist.GetRange, scans Cmbp1Msg / Mbp0Msg records via the
// same convertCmbp1 path live ingest uses, and Writes each Tick to
// the archive writer.
func backfillDay(ctx context.Context, apiKey, dataset string,
	parents []string, day time.Time, archive *store.ArchiveWriter) error {
	_ = ctx
	_ = apiKey
	_ = dataset
	_ = parents
	_ = day
	_ = archive

	// TODO(post-unlock): wire dbn_hist.GetRange + scanner per
	// internal/feed/databento/bootstrap.go's pattern. The convert
	// helpers in internal/feed/databento/convert.go already produce
	// feed.Tick values that archive.Write accepts; this function just
	// glues the historical scanner output to that conversion.
	return fmt.Errorf("not implemented yet — Databento account locked, see docs/PROGRESS.md")
}

// tradingDays returns each weekday in [start, end). US market holidays
// are NOT excluded — Databento returns empty rows for those days, the
// archive writer drops zero-row batches, and a downstream count check
// would catch any unexpected zero-day.
func tradingDays(start, end time.Time) []time.Time {
	days := []time.Time{}
	for d := start; d.Before(end); d = d.AddDate(0, 0, 1) {
		if d.Weekday() == weekendIsoSaturday || d.Weekday() == weekendIsoSunday {
			continue
		}
		days = append(days, d)
	}
	return days
}

func splitCSV(s string) []string {
	out := []string{}
	for _, p := range strings.Split(s, ",") {
		p = strings.TrimSpace(p)
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

// Compile-time assertion that the feed package types we'll need at
// implementation time are still exported. Prevents accidental rename.
var _ = feed.SchemaCMBP1
