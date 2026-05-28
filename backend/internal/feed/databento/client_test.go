package databento

import (
	"testing"

	dbn "github.com/NimbleMarkets/dbn-go"

	"flowgreeks/internal/feed"
)

func TestNewRequiresAPIKey(t *testing.T) {
	if _, err := New(Config{}); err == nil {
		t.Fatal("expected error when APIKey is empty")
	}
}

func TestNewDefaultsBufferSize(t *testing.T) {
	c, err := New(Config{APIKey: "k"})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if cap(c.ticks) != defaultBufferSize {
		t.Fatalf("ticks cap = %d, want %d", cap(c.ticks), defaultBufferSize)
	}
}

func TestClientImplementsFeed(t *testing.T) {
	var _ feed.Feed = (*Client)(nil)
}

func TestClientChannels(t *testing.T) {
	c, err := New(Config{APIKey: "k", BufferSize: 4})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if c.Ticks() == nil {
		t.Fatal("Ticks returned nil")
	}
	if c.Errors() == nil {
		t.Fatal("Errors returned nil")
	}

	// pushTick should land on the channel non-blockingly.
	want := feed.Tick{Symbol: feed.SymbolSPX, TickType: feed.TickTypeQuote}
	c.pushTick(want)
	got := <-c.Ticks()
	if got != want {
		t.Fatalf("Ticks got %+v, want %+v", got, want)
	}

	// pushErr should land on the err channel.
	c.pushErr(errSentinel{})
	if err := <-c.Errors(); err == nil {
		t.Fatal("expected non-nil error from Errors")
	}
}

type errSentinel struct{}

func (errSentinel) Error() string { return "sentinel" }

func TestParseOPRASymbolSPXW(t *testing.T) {
	// 6 chars (space-padded) + YYMMDD + side + 8-digit strike (×1000)
	m, ok := parseOPRASymbol("SPXW  250620C05810000")
	if !ok {
		t.Fatal("parseOPRASymbol failed")
	}
	if m.Symbol != feed.SymbolSPX {
		t.Errorf("Symbol = %v, want SPX", m.Symbol)
	}
	if m.AssetClass != feed.AssetClassOption {
		t.Errorf("AssetClass = %v, want Option", m.AssetClass)
	}
	if m.Expiry != 20250620 {
		t.Errorf("Expiry = %d, want 20250620", m.Expiry)
	}
	if m.Side != feed.SideCall {
		t.Errorf("Side = %v, want Call", m.Side)
	}
	if m.Strike != 5810000 {
		t.Errorf("Strike = %d, want 5810000", m.Strike)
	}
}

func TestParseOPRASymbolNDXP(t *testing.T) {
	m, ok := parseOPRASymbol("NDXP  260925P15000000")
	if !ok {
		t.Fatal("parseOPRASymbol failed")
	}
	if m.Symbol != feed.SymbolNDX {
		t.Errorf("Symbol = %v, want NDX", m.Symbol)
	}
	if m.Side != feed.SidePut {
		t.Errorf("Side = %v, want Put", m.Side)
	}
	if m.Expiry != 20260925 {
		t.Errorf("Expiry = %d, want 20260925", m.Expiry)
	}
}

func TestParseOPRASymbolUnknownRoot(t *testing.T) {
	if _, ok := parseOPRASymbol("AAPL  250620C00200000"); ok {
		t.Fatal("expected ok=false for AAPL")
	}
}

func TestParseFutureSymbol(t *testing.T) {
	m, ok := parseFutureSymbol("ESM6")
	if !ok {
		t.Fatal("parseFutureSymbol failed")
	}
	if m.Symbol != feed.SymbolSPX {
		t.Errorf("Symbol = %v, want SPX", m.Symbol)
	}
	if m.AssetClass != feed.AssetClassFuture {
		t.Errorf("AssetClass = %v, want Future", m.AssetClass)
	}
	if got := feed.DecodeFuturesContract(m.Future); got != "ESM6" {
		t.Errorf("Future = %q, want ESM6", got)
	}
}

func TestVendorSymbols(t *testing.T) {
	cases := []struct {
		ds   feed.Dataset
		sym  feed.Symbol
		want []string
	}{
		{feed.DatasetOPRAPillar, feed.SymbolSPX, []string{"SPX.OPT", "SPXW.OPT"}},
		{feed.DatasetOPRAPillar, feed.SymbolNDX, []string{"NDX.OPT", "NDXP.OPT"}},
		{feed.DatasetCMEGlobex, feed.SymbolSPX, []string{"ES.FUT"}},
		{feed.DatasetCMEGlobex, feed.SymbolNDX, []string{"NQ.FUT"}},
	}
	for _, tc := range cases {
		got, err := vendorSymbols(tc.ds, tc.sym)
		if err != nil {
			t.Errorf("%s/%s: %v", tc.ds, tc.sym, err)
			continue
		}
		if !equalStrings(got, tc.want) {
			t.Errorf("%s/%s: got %v, want %v", tc.ds, tc.sym, got, tc.want)
		}
	}
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func TestConvertMbp1ToTick(t *testing.T) {
	meta := instrumentMeta{
		Symbol:     feed.SymbolSPX,
		AssetClass: feed.AssetClassOption,
		Expiry:     20250620,
		Strike:     5810000,
		Side:       feed.SideCall,
	}
	msg := &dbn.Mbp1Msg{
		Header: dbn.RHeader{
			TsEvent:      1_700_000_000_000_000_000,
			InstrumentID: 12345,
			PublisherID:  2,
		},
		Level: dbn.BidAskPair{
			BidPx: 5_120_000_000, // 5.12 in fixed9
			AskPx: 5_130_000_000, // 5.13
			BidSz: 10,
			AskSz: 12,
		},
	}

	const tsRecv uint64 = 1_700_000_000_000_001_000
	var got feed.Tick
	convertMbp1(&got, msg, meta, tsRecv)

	if got.TickType != feed.TickTypeQuote {
		t.Errorf("TickType = %v, want Quote", got.TickType)
	}
	if got.Symbol != feed.SymbolSPX {
		t.Errorf("Symbol = %v, want SPX", got.Symbol)
	}
	if got.AssetClass != feed.AssetClassOption {
		t.Errorf("AssetClass = %v, want Option", got.AssetClass)
	}
	if got.Expiry != 20250620 {
		t.Errorf("Expiry = %d", got.Expiry)
	}
	if got.Strike != 5810000 {
		t.Errorf("Strike = %d", got.Strike)
	}
	if got.Side != feed.SideCall {
		t.Errorf("Side = %v", got.Side)
	}
	if got.Bid != 5.12 {
		t.Errorf("Bid = %v, want 5.12", got.Bid)
	}
	if got.Ask != 5.13 {
		t.Errorf("Ask = %v, want 5.13", got.Ask)
	}
	if got.BidSize != 10 || got.AskSize != 12 {
		t.Errorf("Sizes = %d/%d, want 10/12", got.BidSize, got.AskSize)
	}
	if got.TsEvent != msg.Header.TsEvent {
		t.Errorf("TsEvent = %d", got.TsEvent)
	}
	if got.TsRecv != tsRecv {
		t.Errorf("TsRecv = %d", got.TsRecv)
	}
	if got.InstrumentID != 12345 {
		t.Errorf("InstrumentID = %d", got.InstrumentID)
	}
}

func TestConvertTradeToTick(t *testing.T) {
	meta := instrumentMeta{
		Symbol:     feed.SymbolSPX,
		AssetClass: feed.AssetClassOption,
		Expiry:     20250620,
		Strike:     5810000,
		Side:       feed.SidePut,
	}
	msg := &dbn.Mbp0Msg{
		Header: dbn.RHeader{
			TsEvent:      1_700_000_000_000_000_000,
			InstrumentID: 99,
		},
		Price: 4_250_000_000, // 4.25
		Size:  7,
	}
	var got feed.Tick
	convertTrade(&got, msg, meta, 12345)

	if got.TickType != feed.TickTypeTrade {
		t.Errorf("TickType = %v, want Trade", got.TickType)
	}
	if got.Aggressor != feed.AggressorUnknown {
		t.Errorf("Aggressor = %v, want Unknown", got.Aggressor)
	}
	if got.Price != 4.25 {
		t.Errorf("Price = %v, want 4.25", got.Price)
	}
	if got.Size != 7 {
		t.Errorf("Size = %d, want 7", got.Size)
	}
	if got.Side != feed.SidePut {
		t.Errorf("Side = %v, want Put", got.Side)
	}
}

func TestConvertTradeFutures(t *testing.T) {
	meta := instrumentMeta{
		Symbol:     feed.SymbolSPX,
		AssetClass: feed.AssetClassFuture,
		Future:     feed.EncodeFuturesContract("ESM6"),
	}
	msg := &dbn.Mbp0Msg{
		Header: dbn.RHeader{TsEvent: 1, InstrumentID: 1},
		Price:  5_800_000_000_000, // 5800.00
		Size:   1,
	}
	var got feed.Tick
	convertTrade(&got, msg, meta, 2)

	if got.AssetClass != feed.AssetClassFuture {
		t.Errorf("AssetClass = %v", got.AssetClass)
	}
	if feed.DecodeFuturesContract(got.FuturesContract) != "ESM6" {
		t.Errorf("Future = %q", feed.DecodeFuturesContract(got.FuturesContract))
	}
	if got.Price != 5800.0 {
		t.Errorf("Price = %v, want 5800", got.Price)
	}
}

// TestDbnFixedString covers the helper used by bootstrap to trim
// fixed-width vendor symbol buffers. The DBN format right-pads symbols
// with NUL, so the helper has to find the first NUL — but also handle
// the no-NUL case for max-length symbols.
func TestDbnFixedString(t *testing.T) {
	cases := []struct {
		name string
		in   []byte
		want string
	}{
		{"trailing nuls", []byte("SPXW\x00\x00\x00\x00\x00"), "SPXW"},
		{"no nul", []byte("SPXW123456"), "SPXW123456"},
		{"empty", []byte{}, ""},
		{"all nul", []byte{0, 0, 0}, ""},
		{"nul mid-string keeps prefix", []byte("AB\x00CD"), "AB"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := dbnFixedString(c.in)
			if got != c.want {
				t.Errorf("dbnFixedString(%q) = %q, want %q", c.in, got, c.want)
			}
		})
	}
}

// TestResolveSymbol_Empty + the assorted invalid-shape cases — the
// hot path in bootstrap is wrapped in a `if !ok continue`, so silent
// returning false on garbage is the contract. These guard against
// accidental panic paths if upstream sends malformed bytes.
func TestResolveSymbol_RejectsMalformed(t *testing.T) {
	cases := []string{
		"",
		"   ",
		"SPXW",                       // too short for both option + future paths
		"SPXW  250620X05810000",      // unknown side char
		"SPXW  XX0620C05810000",      // bad year
		"SPXW  25XX20C05810000",      // bad month
		"SPXW  2506XXC05810000",      // bad day
		"SPXW  250620CXXXXXX00",      // bad strike
		"AAPL  250620C00200000",      // unknown root for option
		"XX",                         // unknown future root
	}
	for _, c := range cases {
		t.Run(c, func(t *testing.T) {
			if _, ok := resolveSymbol(c); ok {
				t.Errorf("resolveSymbol(%q) returned ok=true; should be false", c)
			}
		})
	}
}

// TestResolveSymbol_AcceptsOptionAndFuturePaths confirms the dispatch
// reaches both branches and the right metadata comes back.
func TestResolveSymbol_AcceptsOptionAndFuturePaths(t *testing.T) {
	t.Run("option", func(t *testing.T) {
		m, ok := resolveSymbol("SPXW  250620C05810000")
		if !ok {
			t.Fatal("expected ok")
		}
		if m.AssetClass != feed.AssetClassOption {
			t.Errorf("AssetClass = %v, want Option", m.AssetClass)
		}
	})
	t.Run("future", func(t *testing.T) {
		m, ok := resolveSymbol("ESM6")
		if !ok {
			t.Fatal("expected ok")
		}
		if m.AssetClass != feed.AssetClassFuture {
			t.Errorf("AssetClass = %v, want Future", m.AssetClass)
		}
	})
}

// TestParseFutureSymbol_NQ exercises the NDX path (parseFutureSymbol
// branches on the first 2 chars; the SPX path was already covered).
func TestParseFutureSymbol_NQ(t *testing.T) {
	m, ok := parseFutureSymbol("NQM6")
	if !ok {
		t.Fatal("parseFutureSymbol failed")
	}
	if m.Symbol != feed.SymbolNDX {
		t.Errorf("Symbol = %v, want NDX", m.Symbol)
	}
	if feed.DecodeFuturesContract(m.Future) != "NQM6" {
		t.Errorf("Future = %q, want NQM6", feed.DecodeFuturesContract(m.Future))
	}
}

// TestConvertCmbp1ToTick exercises the consolidated-quote variant
// (used for OPRA.PILLAR, separate path from MBP-1 used by GLBX).
func TestConvertCmbp1ToTick(t *testing.T) {
	meta := instrumentMeta{
		Symbol:     feed.SymbolSPX,
		AssetClass: feed.AssetClassOption,
		Expiry:     20260620,
		Strike:     5810000,
		Side:       feed.SideCall,
	}
	msg := &dbn.Cmbp1Msg{
		Header: dbn.RHeader{
			TsEvent:      1_700_000_000_000_000_000,
			InstrumentID: 999,
		},
		Level: dbn.ConsolidatedBidAskPair{
			BidPx: 1_500_000_000, // 1.50
			AskPx: 1_550_000_000, // 1.55
			BidSz: 5,
			AskSz: 7,
		},
	}
	var got feed.Tick
	convertCmbp1(&got, msg, meta, 1234)
	if got.TickType != feed.TickTypeQuote {
		t.Errorf("TickType = %v, want Quote", got.TickType)
	}
	if got.Bid != 1.50 || got.Ask != 1.55 {
		t.Errorf("Bid/Ask = %v/%v, want 1.50/1.55", got.Bid, got.Ask)
	}
	if got.BidSize != 5 || got.AskSize != 7 {
		t.Errorf("Sizes = %d/%d, want 5/7", got.BidSize, got.AskSize)
	}
	if got.InstrumentID != 999 {
		t.Errorf("InstrumentID = %d, want 999", got.InstrumentID)
	}
}

// TestBootstrapDataset_EmptyAPIKey + nil parent list — the only two
// paths in bootstrapDataset that don't hit the network. Covers the
// guard-clause behaviour.
func TestBootstrapDataset_GuardClauses(t *testing.T) {
	t.Run("empty api key", func(t *testing.T) {
		_, err := bootstrapDataset("", feed.DatasetOPRAPillar, []string{"SPX.OPT"})
		if err == nil {
			t.Fatal("expected error on empty api key")
		}
	})
	t.Run("empty symbols", func(t *testing.T) {
		out, err := bootstrapDataset("k", feed.DatasetOPRAPillar, nil)
		if err != nil {
			t.Fatalf("nil symbols should return (nil,nil): %v", err)
		}
		if out != nil {
			t.Errorf("expected nil map for nil symbols, got %v", out)
		}
	})
}
