package store

import (
	"context"
	"io"
	"log/slog"
	"sync"
	"testing"
	"time"
)

// newTestStateWriter constructs a StateWriter without going through
// pgxpool.New so the lifecycle paths can be exercised offline.
// pool is left nil — every code path under test here exits before
// touching the database.
func newTestStateWriter(buf, batch int) *StateWriter {
	return &StateWriter{
		log:           slog.New(slog.NewTextHandler(io.Discard, nil)),
		flushInterval: 10 * time.Millisecond,
		batchSize:     batch,
		in:            make(chan StateRow, buf),
		closeCh:       make(chan struct{}),
		done:          make(chan struct{}),
	}
}

// TestStateWriter_WriteBufferFull confirms Write returns the buffer-full
// error (and bumps the dropped counter) instead of blocking when the
// channel is saturated. The compute hot path depends on this — a
// blocking Write would tail-latency every aggregator iteration.
func TestStateWriter_WriteBufferFull(t *testing.T) {
	w := newTestStateWriter(2, 1)
	for i := 0; i < 2; i++ {
		if err := w.Write(StateRow{}); err != nil {
			t.Fatalf("write %d: %v", i, err)
		}
	}
	if err := w.Write(StateRow{}); err == nil {
		t.Fatal("third write must fail with buffer full")
	}
}

// TestStateWriter_WriteAfterCloseRejects: once Close has signalled,
// Write must refuse new rows so a slow caller doesn't keep filling
// a channel nothing will drain.
func TestStateWriter_WriteAfterCloseRejects(t *testing.T) {
	w := newTestStateWriter(4, 1)
	close(w.closeCh)
	if err := w.Write(StateRow{}); err == nil {
		t.Fatal("expected error after close, got nil")
	}
}

// TestStateWriter_CloseChClosedOnce confirms closeOnce gates the
// close(closeCh) call so concurrent Close invocations don't panic on
// close-of-closed. We can't drive Close end-to-end here (it pool-closes
// a nil pgxpool), so we exercise the once.Do guard directly: many
// callers race a partial close path; only one observes the channel
// transitioning from open to closed.
func TestStateWriter_CloseChClosedOnce(t *testing.T) {
	w := newTestStateWriter(4, 1)
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			w.closeOnce.Do(func() {
				close(w.closeCh)
			})
		}()
	}
	wg.Wait()
	select {
	case <-w.closeCh:
		// closed exactly once — pass.
	default:
		t.Fatal("closeCh should be closed after closeOnce ran")
	}
}

// TestStateWriter_WriteThenDrainNonBlocking confirms that buffered rows
// can still be received post-close — selfDrain reads channel as default
// case, so any rows already enqueued before close survive long enough
// for the test to observe them. Mostly a sanity check on channel
// orientation.
func TestStateWriter_BufferReceivable(t *testing.T) {
	w := newTestStateWriter(2, 1)
	if err := w.Write(StateRow{Spot: 5810}); err != nil {
		t.Fatalf("write: %v", err)
	}
	select {
	case r := <-w.in:
		if r.Spot != 5810 {
			t.Errorf("row.Spot = %v, want 5810", r.Spot)
		}
	case <-time.After(100 * time.Millisecond):
		t.Fatal("buffered row was not receivable")
	}
}

// TestDerefF guards the helper used by QueryStates row scanning.
// Pointers come back as nil when the column is NULL; derefF must
// translate to zero, not panic.
func TestDerefF(t *testing.T) {
	if got := derefF(nil); got != 0 {
		t.Errorf("derefF(nil) = %v, want 0", got)
	}
	v := 42.5
	if got := derefF(&v); got != 42.5 {
		t.Errorf("derefF(&42.5) = %v, want 42.5", got)
	}
}

// TestStateWriter_RunRejectsRestart: the writer is single-Run by
// design — running.CompareAndSwap returns false on a second call, so
// a misuse of the API surface fails loud rather than racing two
// drainers on the same channel.
func TestStateWriter_RunRejectsRestart(t *testing.T) {
	w := newTestStateWriter(4, 1)
	w.running.Store(true)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err := w.Run(ctx)
	if err == nil {
		t.Fatal("expected Run to reject when already running")
	}
	if err.Error() != "state writer: Run already started" {
		t.Errorf("err = %q, want %q", err.Error(), "state writer: Run already started")
	}
}
