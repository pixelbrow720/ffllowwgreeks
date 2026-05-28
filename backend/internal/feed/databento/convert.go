// Package databento contains the dbn-go-backed implementation of feed.Feed.
//
// This file holds the pure DBN→Tick conversion logic. Kept free of network
// state so it can be exercised by unit tests without a real connection.
package databento

import (
	"strconv"
	"strings"

	dbn "github.com/NimbleMarkets/dbn-go"

	"flowgreeks/internal/feed"
)

// instrumentMeta is the cached, pre-decoded contract metadata for an
// instrument_id. Resolved once on the first SymbolMappingMsg and reused for
// every subsequent Mbp0/Mbp1 record on that id — keeps the hot path O(1).
type instrumentMeta struct {
	Symbol     feed.Symbol
	AssetClass feed.AssetClass
	Expiry     uint32
	Strike     uint32
	Side       feed.Side
	Future     [12]byte
}

// resolveSymbol parses a raw vendor symbol into instrumentMeta. Returns ok=false
// for symbols we can't classify (heartbeats, definitions, unknown roots).
func resolveSymbol(raw string) (instrumentMeta, bool) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return instrumentMeta{}, false
	}
	if m, ok := parseOPRASymbol(raw); ok {
		return m, true
	}
	if m, ok := parseFutureSymbol(raw); ok {
		return m, true
	}
	return instrumentMeta{}, false
}

// parseOPRASymbol parses an OSI-format option symbol:
//
//	"SPXW  250620C05810000"  →  root=SPXW, exp=20250620, C, strike=5810000
//
// 21 chars total: 6-char root (space-padded) + YYMMDD + C/P + 8-digit strike.
// The 8-digit strike is OSI fixed × 1000, which already matches feed.Tick.Strike.
func parseOPRASymbol(raw string) (instrumentMeta, bool) {
	if len(raw) != 21 {
		return instrumentMeta{}, false
	}
	root := strings.TrimSpace(raw[:6])
	sym := feed.ParseSymbol(root)
	if sym == feed.SymbolUnknown {
		return instrumentMeta{}, false
	}
	yy, err := strconv.Atoi(raw[6:8])
	if err != nil {
		return instrumentMeta{}, false
	}
	mm, err := strconv.Atoi(raw[8:10])
	if err != nil {
		return instrumentMeta{}, false
	}
	dd, err := strconv.Atoi(raw[10:12])
	if err != nil {
		return instrumentMeta{}, false
	}
	var side feed.Side
	switch raw[12] {
	case 'C':
		side = feed.SideCall
	case 'P':
		side = feed.SidePut
	default:
		return instrumentMeta{}, false
	}
	strike, err := strconv.Atoi(raw[13:21])
	if err != nil {
		return instrumentMeta{}, false
	}
	return instrumentMeta{
		Symbol:     sym,
		AssetClass: feed.AssetClassOption,
		Expiry:     uint32((2000+yy)*10000 + mm*100 + dd),
		Strike:     uint32(strike),
		Side:       side,
	}, true
}

// parseFutureSymbol maps a CME Globex outright contract symbol to our internal
// representation. Maps ES.* → SPX (E-mini S&P), NQ.* → NDX (E-mini Nasdaq).
func parseFutureSymbol(raw string) (instrumentMeta, bool) {
	if len(raw) < 3 {
		return instrumentMeta{}, false
	}
	var sym feed.Symbol
	switch raw[:2] {
	case "ES":
		sym = feed.SymbolSPX
	case "NQ":
		sym = feed.SymbolNDX
	default:
		return instrumentMeta{}, false
	}
	return instrumentMeta{
		Symbol:     sym,
		AssetClass: feed.AssetClassFuture,
		Future:     feed.EncodeFuturesContract(raw),
	}, true
}

// convertMbp1 normalizes an Mbp1Msg (top-of-book quote) into a feed.Tick.
// tsRecv is the adapter-side receive timestamp (caller-supplied to keep this
// pure). Mutates the supplied *feed.Tick in place to allow caller-side pooling.
func convertMbp1(out *feed.Tick, msg *dbn.Mbp1Msg, meta instrumentMeta, tsRecv uint64) {
	*out = feed.Tick{
		TsEvent:      msg.Header.TsEvent,
		TsRecv:       tsRecv,
		Symbol:       meta.Symbol,
		AssetClass:   meta.AssetClass,
		TickType:     feed.TickTypeQuote,
		Expiry:       meta.Expiry,
		Strike:       meta.Strike,
		Side:         meta.Side,
		FuturesContract: meta.Future,
		Bid:          dbn.Fixed9ToFloat64(msg.Level.BidPx),
		Ask:          dbn.Fixed9ToFloat64(msg.Level.AskPx),
		BidSize:      msg.Level.BidSz,
		AskSize:      msg.Level.AskSz,
		Exchange:     uint8(msg.Header.PublisherID),
		InstrumentID: uint64(msg.Header.InstrumentID),
	}
}

// convertCmbp1 normalizes a Cmbp1Msg (consolidated top-of-book quote across
// venues — used by OPRA.PILLAR) into a feed.Tick. Same shape as Mbp1 but
// the level type is ConsolidatedBidAskPair.
func convertCmbp1(out *feed.Tick, msg *dbn.Cmbp1Msg, meta instrumentMeta, tsRecv uint64) {
	*out = feed.Tick{
		TsEvent:      msg.Header.TsEvent,
		TsRecv:       tsRecv,
		Symbol:       meta.Symbol,
		AssetClass:   meta.AssetClass,
		TickType:     feed.TickTypeQuote,
		Expiry:       meta.Expiry,
		Strike:       meta.Strike,
		Side:         meta.Side,
		FuturesContract: meta.Future,
		Bid:          dbn.Fixed9ToFloat64(msg.Level.BidPx),
		Ask:          dbn.Fixed9ToFloat64(msg.Level.AskPx),
		BidSize:      msg.Level.BidSz,
		AskSize:      msg.Level.AskSz,
		Exchange:     uint8(msg.Header.PublisherID),
		InstrumentID: uint64(msg.Header.InstrumentID),
	}
}

// convertTrade normalizes an Mbp0Msg (trade print) into a feed.Tick. Aggressor
// is left Unknown — Lee-Ready classification is a downstream concern.
func convertTrade(out *feed.Tick, msg *dbn.Mbp0Msg, meta instrumentMeta, tsRecv uint64) {
	*out = feed.Tick{
		TsEvent:      msg.Header.TsEvent,
		TsRecv:       tsRecv,
		Symbol:       meta.Symbol,
		AssetClass:   meta.AssetClass,
		TickType:     feed.TickTypeTrade,
		Expiry:       meta.Expiry,
		Strike:       meta.Strike,
		Side:         meta.Side,
		FuturesContract: meta.Future,
		Price:        dbn.Fixed9ToFloat64(msg.Price),
		Size:         msg.Size,
		Aggressor:    feed.AggressorUnknown,
		Exchange:     uint8(msg.Header.PublisherID),
		InstrumentID: uint64(msg.Header.InstrumentID),
	}
}
