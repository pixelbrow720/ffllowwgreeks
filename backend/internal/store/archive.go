// Package store provides Postgres / Redis adapters used by FlowGreeks
// cold-path workers and read APIs.
//
// ArchiveWriter is the cold-path tick archiver. It buffers feed.Ticks coming
// off the bus and flushes them to TimescaleDB in batches via COPY. The hot
// path never blocks on this writer: Write() is a non-blocking channel send
// and returns ErrBufferFull when the caller is producing faster than Postgres
// can absorb. Backpressure policy (drop vs retry) is the caller's choice.
package store

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"flowgreeks/internal/feed"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	ticksWritten = promauto.NewCounter(prometheus.CounterOpts{
		Name: "flowgreeks_archive_ticks_written_total",
		Help: "Total ticks successfully written to the TimescaleDB archive.",
	})
	ticksDropped = promauto.NewCounter(prometheus.CounterOpts{
		Name: "flowgreeks_archive_ticks_dropped_total",
		Help: "Total ticks dropped because the archive writer's buffer was full.",
	})
	flushDuration = promauto.NewHistogram(prometheus.HistogramOpts{
		Name:    "flowgreeks_archive_flush_duration_seconds",
		Help:    "Duration of a single batch COPY into the ticks hypertable.",
		Buckets: prometheus.ExponentialBuckets(0.001, 2, 12),
	})
)

const (
	defaultBatchSize      = 5000
	defaultFlushInterval  = time.Second
	defaultBufferCapacity = 50000
	flushRetryAttempts    = 3
	flushRetryBackoff     = 200 * time.Millisecond
	closeWaitTimeout      = 2 * time.Second
)

// copyColumns is the column order for COPY into ticks. Must match tickToRow.
var copyColumns = []string{
	"ts", "recv_ts", "symbol",
	"expiry", "strike", "side",
	"tick_type",
	"price", "size",
	"bid", "ask", "bid_size", "ask_size",
	"open_interest", "aggressor", "exchange", "instrument_id",
}

// ErrBufferFull is returned by Write when the internal buffer cannot accept
// another tick without blocking.
var ErrBufferFull = errors.New("archive writer buffer full")

// ErrWriterClosed is returned by Write after Close has been called.
var ErrWriterClosed = errors.New("archive writer closed")

// WriterOpts configures an ArchiveWriter. Zero values fall back to defaults.
type WriterOpts struct {
	// BatchSize triggers a flush when the in-memory batch reaches this size.
	// Default: 5000.
	BatchSize int
	// FlushInterval triggers a flush after this much time has elapsed since
	// the last flush, even if the batch is not full. Default: 1s.
	FlushInterval time.Duration
	// BufferCapacity sizes the channel that Write pushes onto. Once full,
	// Write returns ErrBufferFull. Default: 50000.
	BufferCapacity int
	// Logger is used for flush info / drop warnings. Defaults to slog.Default().
	Logger *slog.Logger
}

func (o *WriterOpts) applyDefaults() {
	if o.BatchSize <= 0 {
		o.BatchSize = defaultBatchSize
	}
	if o.FlushInterval <= 0 {
		o.FlushInterval = defaultFlushInterval
	}
	if o.BufferCapacity <= 0 {
		o.BufferCapacity = defaultBufferCapacity
	}
	if o.Logger == nil {
		o.Logger = slog.Default()
	}
}

// ArchiveWriter buffers normalized ticks and writes them to TimescaleDB in
// batches via COPY. Construct with NewArchiveWriter, drive with Run, stop
// with Close (or by canceling Run's context).
type ArchiveWriter struct {
	pool *pgxpool.Pool
	opts WriterOpts
	log  *slog.Logger

	in      chan feed.Tick
	closeCh chan struct{}
	done    chan struct{}

	running   atomic.Bool
	closeOnce sync.Once
}

// NewArchiveWriter dials Postgres and returns a writer ready to be Run().
// The dsn is a libpq-style connection string; pgxpool handles parsing.
func NewArchiveWriter(ctx context.Context, dsn string, opts WriterOpts) (*ArchiveWriter, error) {
	opts.applyDefaults()

	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, fmt.Errorf("pgxpool.New: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("pgxpool ping: %w", err)
	}

	return &ArchiveWriter{
		pool:    pool,
		opts:    opts,
		log:     opts.Logger,
		in:      make(chan feed.Tick, opts.BufferCapacity),
		closeCh: make(chan struct{}),
		done:    make(chan struct{}),
	}, nil
}

// Write enqueues a tick for the archive worker. Non-blocking: returns
// ErrBufferFull if the internal channel is saturated, ErrWriterClosed if
// the writer has been closed. Caller decides drop vs retry policy.
func (w *ArchiveWriter) Write(t feed.Tick) error {
	select {
	case <-w.closeCh:
		return ErrWriterClosed
	default:
	}
	select {
	case w.in <- t:
		return nil
	default:
		ticksDropped.Inc()
		w.log.Warn("archive: tick dropped, buffer full",
			"buffer_capacity", w.opts.BufferCapacity)
		return ErrBufferFull
	}
}

// Run drains the buffer and flushes batches until ctx is canceled or Close
// is called. The final pending batch is flushed before Run returns.
//
// Only one Run goroutine may be active per ArchiveWriter.
func (w *ArchiveWriter) Run(ctx context.Context) error {
	if !w.running.CompareAndSwap(false, true) {
		return errors.New("archive writer: Run already started")
	}
	defer w.running.Store(false)
	defer close(w.done)

	batch := make([][]any, 0, w.opts.BatchSize)
	ticker := time.NewTicker(w.opts.FlushInterval)
	defer ticker.Stop()

	flush := func(reason string) {
		if len(batch) == 0 {
			return
		}
		// Steady-state flushes use the caller's ctx so a long-cancel
		// short-circuits in-progress COPY. Shutdown flushes (ctx
		// already cancelled) get a fresh deadline so the final batch
		// actually lands instead of returning ctx.Err immediately.
		flushCtx := ctx
		if ctx.Err() != nil {
			var cancel context.CancelFunc
			flushCtx, cancel = context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
		}
		if err := w.copyBatch(flushCtx, batch); err != nil {
			w.log.Error("archive: flush failed",
				"reason", reason,
				"batch_size", len(batch),
				"error", err)
		}
		batch = batch[:0]
	}

	drainBuffered := func() {
		for {
			select {
			case t := <-w.in:
				batch = append(batch, tickToRow(t))
				if len(batch) >= w.opts.BatchSize {
					flush("drain-size")
				}
			default:
				return
			}
		}
	}

	for {
		select {
		case <-ctx.Done():
			drainBuffered()
			flush("shutdown")
			return ctx.Err()
		case <-w.closeCh:
			drainBuffered()
			flush("close")
			return nil
		case t := <-w.in:
			batch = append(batch, tickToRow(t))
			if len(batch) >= w.opts.BatchSize {
				flush("size")
			}
		case <-ticker.C:
			flush("interval")
		}
	}
}

// Close performs a graceful shutdown: stops accepting writes, lets Run drain
// any buffered ticks and flush, then closes the connection pool. Idempotent.
//
// Typical usage: cancel Run's context (or just call Close), then call Close
// to release the pool. If Run was never started, Close drains and flushes
// itself with a fresh background context.
func (w *ArchiveWriter) Close() error {
	w.closeOnce.Do(func() {
		close(w.closeCh)
		select {
		case <-w.done:
			// Run observed closeCh and exited cleanly.
		case <-time.After(closeWaitTimeout):
			// Run was never started or did not exit in time. Self-drain.
			w.selfDrain()
		}
		w.pool.Close()
	})
	return nil
}

// selfDrain is used by Close when Run isn't there to do the work. Best-effort.
func (w *ArchiveWriter) selfDrain() {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	batch := make([][]any, 0, w.opts.BatchSize)
	for {
		select {
		case t := <-w.in:
			batch = append(batch, tickToRow(t))
			if len(batch) >= w.opts.BatchSize {
				if err := w.copyBatch(ctx, batch); err != nil {
					w.log.Error("archive: self-drain flush failed", "error", err)
				}
				batch = batch[:0]
			}
		default:
			if len(batch) > 0 {
				if err := w.copyBatch(ctx, batch); err != nil {
					w.log.Error("archive: self-drain final flush failed", "error", err)
				}
			}
			return
		}
	}
}

// copyBatch performs a COPY with up to flushRetryAttempts retries on failure.
func (w *ArchiveWriter) copyBatch(ctx context.Context, batch [][]any) error {
	if len(batch) == 0 {
		return nil
	}

	start := time.Now()
	var lastErr error
	for attempt := 1; attempt <= flushRetryAttempts; attempt++ {
		n, err := w.pool.CopyFrom(ctx,
			pgx.Identifier{"ticks"},
			copyColumns,
			pgx.CopyFromRows(batch),
		)
		if err == nil {
			d := time.Since(start)
			flushDuration.Observe(d.Seconds())
			ticksWritten.Add(float64(n))
			w.log.Info("archive: flush ok",
				"batch_size", len(batch),
				"rows", n,
				"duration_ms", d.Milliseconds())
			return nil
		}
		lastErr = err
		w.log.Warn("archive: flush attempt failed",
			"attempt", attempt,
			"batch_size", len(batch),
			"error", err)
		if attempt < flushRetryAttempts {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(flushRetryBackoff):
			}
		}
	}
	return fmt.Errorf("copy %d rows: failed after %d attempts: %w",
		len(batch), flushRetryAttempts, lastErr)
}

// tickToRow projects a feed.Tick onto the COPY column order. Column order
// here MUST match copyColumns. Option-only fields (expiry, strike, side)
// are nil for futures so the COPY writer encodes SQL NULL.
func tickToRow(t feed.Tick) []any {
	var expiry, strike, side any
	if !t.IsFuture() {
		if t.Expiry != 0 {
			y := int(t.Expiry / 10000)
			m := int((t.Expiry / 100) % 100)
			d := int(t.Expiry % 100)
			expiry = time.Date(y, time.Month(m), d, 0, 0, 0, 0, time.UTC)
		}
		if t.Strike != 0 {
			strike = int32(t.Strike)
		}
		if t.Side != feed.SideUnknown {
			side = int16(t.Side)
		}
	}

	return []any{
		time.Unix(0, int64(t.TsEvent)).UTC(),
		time.Unix(0, int64(t.TsRecv)).UTC(),
		int16(t.Symbol),
		expiry,
		strike,
		side,
		int16(t.TickType),
		t.Price,
		int32(t.Size),
		t.Bid,
		t.Ask,
		int32(t.BidSize),
		int32(t.AskSize),
		int32(t.OpenInterest),
		int16(t.Aggressor),
		int16(t.Exchange),
		int64(t.InstrumentID),
	}
}
