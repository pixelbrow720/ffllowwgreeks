package store

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"flowgreeks/internal/dealer"
	"flowgreeks/internal/feed"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	stateRowsWritten = promauto.NewCounter(prometheus.CounterOpts{
		Name: "flowgreeks_state_rows_written_total",
		Help: "Total dealer_state_1s rows successfully COPYed to TimescaleDB.",
	})
	stateRowsDropped = promauto.NewCounter(prometheus.CounterOpts{
		Name: "flowgreeks_state_rows_dropped_total",
		Help: "Total dealer_state_1s rows dropped because the writer's buffer was full.",
	})
	stateFlushDuration = promauto.NewHistogram(prometheus.HistogramOpts{
		Name:    "flowgreeks_state_flush_duration_seconds",
		Help:    "Duration of a single batch COPY into dealer_state_1s.",
		Buckets: prometheus.ExponentialBuckets(0.001, 2, 12),
	})
	stateFlushErrors = promauto.NewCounter(prometheus.CounterOpts{
		Name: "flowgreeks_state_flush_errors_total",
		Help: "Total flush failures against dealer_state_1s.",
	})
)

// StateRow is the wire+row shape for one per-second snapshot. Matches
// dealer_state_1s migration and what compute publishes on
// state.<sym>.gex.
type StateRow struct {
	Ts       time.Time
	Symbol   feed.Symbol
	Spot     float64
	BasisSmooth float64

	NetGEX     float64
	ZeroGamma  float64
	CallWall   float64
	PutWall    float64
	ExpectedMv float64
	Regime     dealer.Regime
	CharmZone  dealer.CharmZone
	CharmVelocity float64

	DPIComposite     float32
	DPINetGamma      float32
	DPICharmVelocity float32
	DPIVanna         float32
	DPITTC           float32
	DPIFlow          float32

	PulseGamma float32
	PulseCharm float32
	PulseVanna float32
	PulseTotal float32

	PinActive    bool
	PinTopStrike float64
	PinTopProb   float32
}

// StateWriter batches StateRow values and bulk-inserts via COPY FROM.
// Mirrors ArchiveWriter (internal/store/archive.go) for ticks, sized
// for the lower volume of state rows (~1 row/sec/symbol = ~7k/day).
//
// Lifecycle: NewStateWriter constructs (Run not yet started). Caller
// invokes Run in a goroutine. Close cancels the closeCh so Run drains
// + flushes + exits, then Close pool-closes. Calling Close before Run
// is safe — Close pool-closes immediately and Run becomes a no-op when
// the caller eventually invokes it (the channel is already closed).
type StateWriter struct {
	pool *pgxpool.Pool
	log  *slog.Logger

	flushInterval time.Duration
	batchSize     int

	in      chan StateRow
	closeCh chan struct{}
	done    chan struct{}

	running   atomic.Bool
	closeOnce sync.Once
}

// StateWriterOpts tunes the writer.
type StateWriterOpts struct {
	BatchSize      int           // default 500
	FlushInterval  time.Duration // default 5s (state is low volume)
	BufferCapacity int           // default 5000
}

// NewStateWriter constructs a writer + opens its background drain.
func NewStateWriter(ctx context.Context, dsn string, log *slog.Logger, opts StateWriterOpts) (*StateWriter, error) {
	if opts.BatchSize <= 0 {
		opts.BatchSize = 500
	}
	if opts.FlushInterval <= 0 {
		opts.FlushInterval = 5 * time.Second
	}
	if opts.BufferCapacity <= 0 {
		opts.BufferCapacity = 5000
	}
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, fmt.Errorf("state writer: pool: %w", err)
	}
	pingCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if err := pool.Ping(pingCtx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("state writer: ping: %w", err)
	}
	return &StateWriter{
		pool:          pool,
		log:           log,
		flushInterval: opts.FlushInterval,
		batchSize:     opts.BatchSize,
		in:            make(chan StateRow, opts.BufferCapacity),
		closeCh:       make(chan struct{}),
		done:          make(chan struct{}),
	}, nil
}

// Write enqueues a row. Non-blocking: returns ErrBufferFull when the
// buffer is saturated so the hot path never blocks. Caller bumps the
// dropped counter and continues.
func (w *StateWriter) Write(r StateRow) error {
	select {
	case <-w.closeCh:
		return errors.New("state writer closed")
	default:
	}
	select {
	case w.in <- r:
		return nil
	default:
		stateRowsDropped.Inc()
		return errors.New("state writer buffer full")
	}
}

// Run drains the buffer until ctx is cancelled or Close is called.
// Final flush before return uses a fresh deadline so the last batch
// actually lands even when ctx is already cancelled.
//
// Only one Run goroutine may be active per StateWriter.
func (w *StateWriter) Run(ctx context.Context) error {
	if !w.running.CompareAndSwap(false, true) {
		return errors.New("state writer: Run already started")
	}
	defer w.running.Store(false)
	defer close(w.done)

	t := time.NewTicker(w.flushInterval)
	defer t.Stop()

	batch := make([]StateRow, 0, w.batchSize)
	flush := func(reason string) {
		if len(batch) == 0 {
			return
		}
		flushCtx := ctx
		if ctx.Err() != nil {
			var cancel context.CancelFunc
			flushCtx, cancel = context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
		}
		if err := w.flush(flushCtx, batch); err != nil {
			w.log.Warn("state writer flush failed", "err", err, "rows", len(batch), "reason", reason)
		} else {
			w.log.Info("state writer flush", "rows", len(batch), "reason", reason)
		}
		batch = batch[:0]
	}

	drain := func() {
		for {
			select {
			case r := <-w.in:
				batch = append(batch, r)
			default:
				return
			}
		}
	}

	for {
		select {
		case <-ctx.Done():
			drain()
			flush("ctx_done_final")
			return nil
		case <-w.closeCh:
			drain()
			flush("close")
			return nil
		case r := <-w.in:
			batch = append(batch, r)
			if len(batch) >= w.batchSize {
				flush("batch_size")
			}
		case <-t.C:
			flush("interval")
		}
	}
}

// Close performs a graceful shutdown: stops accepting writes, waits
// for Run to drain + flush (or self-drains if Run was never invoked),
// then closes the connection pool. Idempotent.
func (w *StateWriter) Close() error {
	w.closeOnce.Do(func() {
		close(w.closeCh)
		select {
		case <-w.done:
			// Run observed closeCh and exited cleanly.
		case <-time.After(2 * time.Second):
			// Run was never started or didn't exit in time. Self-drain
			// the buffer with a fresh deadline so the in-flight batch
			// lands instead of being silently dropped.
			w.selfDrain()
		}
		w.pool.Close()
	})
	return nil
}

// selfDrain handles the rare case where Close is called without Run
// having been started, or where Run is wedged. Best-effort.
func (w *StateWriter) selfDrain() {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	batch := make([]StateRow, 0, w.batchSize)
	for {
		select {
		case r := <-w.in:
			batch = append(batch, r)
			if len(batch) >= w.batchSize {
				if err := w.flush(ctx, batch); err != nil {
					w.log.Warn("state writer self-drain flush failed", "err", err)
				}
				batch = batch[:0]
			}
		default:
			if len(batch) > 0 {
				if err := w.flush(ctx, batch); err != nil {
					w.log.Warn("state writer self-drain final flush failed", "err", err)
				}
			}
			return
		}
	}
}

func (w *StateWriter) flush(ctx context.Context, rows []StateRow) error {
	cols := []string{
		"ts", "symbol", "spot", "basis_smooth",
		"net_gex", "zero_gamma", "call_wall", "put_wall", "expected_mv",
		"regime", "charm_zone", "charm_velocity",
		"dpi_composite", "dpi_net_gamma", "dpi_charm_velocity", "dpi_vanna", "dpi_ttc", "dpi_flow",
		"pulse_gamma", "pulse_charm", "pulse_vanna", "pulse_total",
		"pin_active", "pin_top_strike", "pin_top_prob",
	}
	src := pgx.CopyFromSlice(len(rows), func(i int) ([]any, error) {
		r := rows[i]
		return []any{
			r.Ts, int16(r.Symbol), r.Spot, r.BasisSmooth,
			r.NetGEX, r.ZeroGamma, r.CallWall, r.PutWall, r.ExpectedMv,
			int16(r.Regime), int16(r.CharmZone), r.CharmVelocity,
			r.DPIComposite, r.DPINetGamma, r.DPICharmVelocity, r.DPIVanna, r.DPITTC, r.DPIFlow,
			r.PulseGamma, r.PulseCharm, r.PulseVanna, r.PulseTotal,
			r.PinActive, r.PinTopStrike, r.PinTopProb,
		}, nil
	})
	flushCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	start := time.Now()
	n, err := w.pool.CopyFrom(flushCtx, pgx.Identifier{"dealer_state_1s"}, cols, src)
	stateFlushDuration.Observe(time.Since(start).Seconds())
	if err != nil {
		stateFlushErrors.Inc()
		return err
	}
	stateRowsWritten.Add(float64(n))
	return nil
}

// QueryStates loads the historical state stream for one symbol over a
// range, ordered by ts ASC. Used by the backtest REST endpoint.
func QueryStates(ctx context.Context, pool *pgxpool.Pool, sym feed.Symbol, start, end time.Time) ([]StateRow, error) {
	const q = `
		SELECT ts, symbol, spot, basis_smooth,
		       net_gex, zero_gamma, call_wall, put_wall, expected_mv,
		       regime, charm_zone, charm_velocity,
		       dpi_composite, dpi_net_gamma, dpi_charm_velocity, dpi_vanna, dpi_ttc, dpi_flow,
		       pulse_gamma, pulse_charm, pulse_vanna, pulse_total,
		       pin_active, pin_top_strike, pin_top_prob
		FROM dealer_state_1s
		WHERE symbol = $1 AND ts >= $2 AND ts < $3
		ORDER BY ts ASC
	`
	rows, err := pool.Query(ctx, q, int16(sym), start, end)
	if err != nil {
		return nil, fmt.Errorf("query states: %w", err)
	}
	defer rows.Close()
	out := make([]StateRow, 0, 1024)
	for rows.Next() {
		var r StateRow
		var symInt, regime, zone int16
		var spot, basisSmooth, netGEX, zeroGamma, callWall, putWall, expectedMv, charmVel, pinTopStrike *float64
		if err := rows.Scan(
			&r.Ts, &symInt, &spot, &basisSmooth,
			&netGEX, &zeroGamma, &callWall, &putWall, &expectedMv,
			&regime, &zone, &charmVel,
			&r.DPIComposite, &r.DPINetGamma, &r.DPICharmVelocity, &r.DPIVanna, &r.DPITTC, &r.DPIFlow,
			&r.PulseGamma, &r.PulseCharm, &r.PulseVanna, &r.PulseTotal,
			&r.PinActive, &pinTopStrike, &r.PinTopProb,
		); err != nil {
			return nil, fmt.Errorf("scan state: %w", err)
		}
		r.Symbol = feed.Symbol(symInt)
		r.Regime = dealer.Regime(regime)
		r.CharmZone = dealer.CharmZone(zone)
		r.Spot = derefF(spot)
		r.BasisSmooth = derefF(basisSmooth)
		r.NetGEX = derefF(netGEX)
		r.ZeroGamma = derefF(zeroGamma)
		r.CallWall = derefF(callWall)
		r.PutWall = derefF(putWall)
		r.ExpectedMv = derefF(expectedMv)
		r.CharmVelocity = derefF(charmVel)
		r.PinTopStrike = derefF(pinTopStrike)
		out = append(out, r)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("rows: %w", err)
	}
	return out, nil
}

func derefF(p *float64) float64 {
	if p == nil {
		return 0
	}
	return *p
}
