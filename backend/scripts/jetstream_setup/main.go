// JetStream setup helper.
//
// Idempotently creates / updates the three streams FlowGreeks uses,
// matching the configuration documented in docs/DATA_MODEL.md:
//
//   TICKS — 24h retention, 10GB max, file storage   (subjects: ticks.>)
//   STATE — 1h retention,  256MB,  memory storage   (subjects: state.>)
//   FLOW  — 7d retention,  4GB,    file storage     (subjects: flow.>, narrative.>)
//
// internal/bus's Publisher already calls CreateStream for TICKS, but
// nothing in the runtime sets up STATE or FLOW. Run this once after
// `make up` (or after cycling NATS volumes):
//
//   go run ./scripts/jetstream_setup
//
// Idempotent: existing streams are reconfigured to match, not replaced.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

type streamSpec struct {
	name      string
	subjects  []string
	maxAge    time.Duration
	maxBytes  int64
	storage   jetstream.StorageType
}

var streams = []streamSpec{
	{
		name:     "TICKS",
		subjects: []string{"ticks.>"},
		maxAge:   24 * time.Hour,
		maxBytes: 10 * 1024 * 1024 * 1024, // 10 GB
		storage:  jetstream.FileStorage,
	},
	{
		name:     "STATE",
		subjects: []string{"state.>"},
		maxAge:   1 * time.Hour,
		maxBytes: 256 * 1024 * 1024, // 256 MB
		storage:  jetstream.MemoryStorage,
	},
	{
		name:     "FLOW",
		subjects: []string{"flow.>", "narrative.>"},
		maxAge:   7 * 24 * time.Hour,
		maxBytes: 4 * 1024 * 1024 * 1024, // 4 GB
		storage:  jetstream.FileStorage,
	},
}

func main() {
	url := flag.String("nats", envOr("NATS_URL", nats.DefaultURL), "NATS URL")
	dryRun := flag.Bool("dry-run", false, "print plan without applying")
	flag.Parse()

	nc, err := nats.Connect(*url, nats.Name("flowgreeks-jetstream-setup"))
	if err != nil {
		log.Fatalf("connect %s: %v", *url, err)
	}
	defer nc.Close()

	js, err := jetstream.New(nc)
	if err != nil {
		log.Fatalf("jetstream: %v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	fmt.Println("─── jetstream setup ───────────────────────────────")
	fmt.Printf("nats     %s\n", *url)
	fmt.Printf("dry-run  %v\n\n", *dryRun)

	for _, s := range streams {
		if err := apply(ctx, js, s, *dryRun); err != nil {
			log.Printf("[%s] FAIL: %v", s.name, err)
			os.Exit(1)
		}
	}
}

func apply(ctx context.Context, js jetstream.JetStream, s streamSpec, dryRun bool) error {
	cfg := jetstream.StreamConfig{
		Name:      s.name,
		Subjects:  s.subjects,
		Retention: jetstream.LimitsPolicy,
		Discard:   jetstream.DiscardOld,
		MaxAge:    s.maxAge,
		MaxBytes:  s.maxBytes,
		Storage:   s.storage,
	}

	existing, err := js.Stream(ctx, s.name)
	if err == nil {
		current := existing.CachedInfo().Config
		if streamsEqual(current, cfg) {
			fmt.Printf("[%s] up to date (%s, %d MB, %v)\n",
				s.name, current.Storage, current.MaxBytes/1024/1024, current.MaxAge)
			return nil
		}
		if dryRun {
			fmt.Printf("[%s] WOULD UPDATE\n", s.name)
			return nil
		}
		if _, err := js.UpdateStream(ctx, cfg); err != nil {
			return fmt.Errorf("update: %w", err)
		}
		fmt.Printf("[%s] updated → %s, %d MB, %v\n",
			s.name, cfg.Storage, cfg.MaxBytes/1024/1024, cfg.MaxAge)
		return nil
	}
	if !errors.Is(err, jetstream.ErrStreamNotFound) {
		return fmt.Errorf("lookup: %w", err)
	}

	if dryRun {
		fmt.Printf("[%s] WOULD CREATE\n", s.name)
		return nil
	}
	if _, err := js.CreateStream(ctx, cfg); err != nil {
		return fmt.Errorf("create: %w", err)
	}
	fmt.Printf("[%s] created → %s, %d MB, %v\n",
		s.name, cfg.Storage, cfg.MaxBytes/1024/1024, cfg.MaxAge)
	return nil
}

// streamsEqual returns true if the durable knobs we manage already
// match. Ignores fields server-set (created, num_replicas defaults).
func streamsEqual(have, want jetstream.StreamConfig) bool {
	if have.Storage != want.Storage {
		return false
	}
	if have.MaxAge != want.MaxAge {
		return false
	}
	if have.MaxBytes != want.MaxBytes {
		return false
	}
	if len(have.Subjects) != len(want.Subjects) {
		return false
	}
	for i, s := range want.Subjects {
		if have.Subjects[i] != s {
			return false
		}
	}
	return true
}

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
