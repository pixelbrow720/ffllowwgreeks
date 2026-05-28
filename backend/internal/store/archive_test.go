package store

import (
	"context"
	"testing"
	"time"

	"flowgreeks/internal/feed"
)

func TestTickToRow_OptionQuote(t *testing.T) {
	tk := feed.Tick{
		TsEvent:    uint64(time.Date(2026, 5, 25, 14, 30, 0, 0, time.UTC).UnixNano()),
		TsRecv:     uint64(time.Date(2026, 5, 25, 14, 30, 0, 1_000_000, time.UTC).UnixNano()),
		Symbol:     feed.SymbolSPX,
		AssetClass: feed.AssetClassOption,
		TickType:   feed.TickTypeQuote,
		Expiry:     20260620,
		Strike:     5810500,
		Side:       feed.SideCall,
		Bid:        12.50,
		Ask:        12.75,
		BidSize:    100,
		AskSize:    150,
		Exchange:   7,
		InstrumentID: 123456,
	}

	row := tickToRow(tk)
	if got := len(row); got != len(copyColumns) {
		t.Fatalf("row width = %d, want %d", got, len(copyColumns))
	}

	wantExpiry := time.Date(2026, 6, 20, 0, 0, 0, 0, time.UTC)
	cases := []struct {
		idx  int
		got  any
		want any
	}{
		{0, row[0], time.Unix(0, int64(tk.TsEvent)).UTC()},
		{1, row[1], time.Unix(0, int64(tk.TsRecv)).UTC()},
		{2, row[2], int16(feed.SymbolSPX)},
		{3, row[3], wantExpiry},
		{4, row[4], int32(5810500)},
		{5, row[5], int16(feed.SideCall)},
		{6, row[6], int16(feed.TickTypeQuote)},
		{7, row[7], 0.0},
		{8, row[8], int32(0)},
		{9, row[9], 12.50},
		{10, row[10], 12.75},
		{11, row[11], int32(100)},
		{12, row[12], int32(150)},
		{13, row[13], int32(0)},
		{14, row[14], int16(feed.AggressorUnknown)},
		{15, row[15], int16(7)},
		{16, row[16], int64(123456)},
	}
	for _, c := range cases {
		if c.got != c.want {
			t.Errorf("col[%d]=%v (%T), want %v (%T)", c.idx, c.got, c.got, c.want, c.want)
		}
	}
}

func TestTickToRow_OptionTrade(t *testing.T) {
	tk := feed.Tick{
		TsEvent:    uint64(time.Date(2026, 5, 25, 15, 0, 0, 0, time.UTC).UnixNano()),
		TsRecv:     uint64(time.Date(2026, 5, 25, 15, 0, 0, 500_000, time.UTC).UnixNano()),
		Symbol:     feed.SymbolNDX,
		AssetClass: feed.AssetClassOption,
		TickType:   feed.TickTypeTrade,
		Expiry:     20260918,
		Strike:     20000000,
		Side:       feed.SidePut,
		Price:      45.25,
		Size:       7,
		Aggressor:  feed.AggressorBuy,
		Exchange:   3,
	}

	row := tickToRow(tk)
	if row[2] != int16(feed.SymbolNDX) {
		t.Errorf("symbol = %v, want NDX", row[2])
	}
	if row[3] != time.Date(2026, 9, 18, 0, 0, 0, 0, time.UTC) {
		t.Errorf("expiry = %v, want 2026-09-18", row[3])
	}
	if row[4] != int32(20000000) {
		t.Errorf("strike = %v, want 20000000", row[4])
	}
	if row[5] != int16(feed.SidePut) {
		t.Errorf("side = %v, want Put", row[5])
	}
	if row[6] != int16(feed.TickTypeTrade) {
		t.Errorf("tick_type = %v, want Trade", row[6])
	}
	if row[7] != 45.25 {
		t.Errorf("price = %v, want 45.25", row[7])
	}
	if row[8] != int32(7) {
		t.Errorf("size = %v, want 7", row[8])
	}
	if row[14] != int16(feed.AggressorBuy) {
		t.Errorf("aggressor = %v, want Buy", row[14])
	}
}

func TestTickToRow_FutureNullsOptionFields(t *testing.T) {
	tk := feed.Tick{
		TsEvent:    uint64(time.Date(2026, 5, 25, 14, 0, 0, 0, time.UTC).UnixNano()),
		TsRecv:     uint64(time.Date(2026, 5, 25, 14, 0, 0, 0, time.UTC).UnixNano()),
		Symbol:     feed.SymbolSPX,
		AssetClass: feed.AssetClassFuture,
		TickType:   feed.TickTypeTrade,
		Price:      5817.25,
		Size:       3,
		InstrumentID: 999,
	}

	row := tickToRow(tk)
	if row[3] != nil {
		t.Errorf("expiry must be nil for future, got %v (%T)", row[3], row[3])
	}
	if row[4] != nil {
		t.Errorf("strike must be nil for future, got %v (%T)", row[4], row[4])
	}
	if row[5] != nil {
		t.Errorf("side must be nil for future, got %v (%T)", row[5], row[5])
	}
	if row[16] != int64(999) {
		t.Errorf("instrument_id = %v, want 999", row[16])
	}
}

func TestTickToRow_OptionZeroExpiryAndSideNil(t *testing.T) {
	tk := feed.Tick{
		AssetClass: feed.AssetClassOption,
		TickType:   feed.TickTypeQuote,
	}
	row := tickToRow(tk)
	if row[3] != nil {
		t.Errorf("expiry must be nil when zero, got %v", row[3])
	}
	if row[4] != nil {
		t.Errorf("strike must be nil when zero, got %v", row[4])
	}
	if row[5] != nil {
		t.Errorf("side must be nil when unknown, got %v", row[5])
	}
}

func TestWriterOpts_Defaults(t *testing.T) {
	var o WriterOpts
	o.applyDefaults()
	if o.BatchSize != defaultBatchSize {
		t.Errorf("BatchSize default = %d, want %d", o.BatchSize, defaultBatchSize)
	}
	if o.FlushInterval != defaultFlushInterval {
		t.Errorf("FlushInterval default = %v, want %v", o.FlushInterval, defaultFlushInterval)
	}
	if o.BufferCapacity != defaultBufferCapacity {
		t.Errorf("BufferCapacity default = %d, want %d", o.BufferCapacity, defaultBufferCapacity)
	}
	if o.Logger == nil {
		t.Error("Logger default must not be nil")
	}
}

// TestArchiveWriter_BufferOverflow verifies Write returns ErrBufferFull when
// the channel is saturated. We construct an ArchiveWriter without a real
// Postgres pool by skipping NewArchiveWriter and wiring the channel
// directly — Write does not touch the pool.
func TestArchiveWriter_BufferOverflow(t *testing.T) {
	opts := WriterOpts{BufferCapacity: 2}
	opts.applyDefaults()

	w := &ArchiveWriter{
		opts:    opts,
		log:     opts.Logger,
		in:      make(chan feed.Tick, opts.BufferCapacity),
		closeCh: make(chan struct{}),
		done:    make(chan struct{}),
	}

	// Fill buffer.
	if err := w.Write(feed.Tick{}); err != nil {
		t.Fatalf("Write 1: unexpected error %v", err)
	}
	if err := w.Write(feed.Tick{}); err != nil {
		t.Fatalf("Write 2: unexpected error %v", err)
	}

	if err := w.Write(feed.Tick{}); err != ErrBufferFull {
		t.Fatalf("Write 3: got %v, want ErrBufferFull", err)
	}
}

func TestArchiveWriter_WriteAfterClose(t *testing.T) {
	opts := WriterOpts{BufferCapacity: 4}
	opts.applyDefaults()
	w := &ArchiveWriter{
		opts:    opts,
		log:     opts.Logger,
		in:      make(chan feed.Tick, opts.BufferCapacity),
		closeCh: make(chan struct{}),
		done:    make(chan struct{}),
	}
	close(w.closeCh)
	if err := w.Write(feed.Tick{}); err != ErrWriterClosed {
		t.Fatalf("Write after close: got %v, want ErrWriterClosed", err)
	}
}

// Compile-time guard that NewArchiveWriter signature matches the spec; the
// function isn't invoked here (no live Postgres in unit tests).
var _ = func(ctx context.Context, dsn string, opts WriterOpts) (*ArchiveWriter, error) {
	return NewArchiveWriter(ctx, dsn, opts)
}
