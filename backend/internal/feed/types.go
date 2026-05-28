// Package feed defines the canonical normalized event types that flow
// through FlowGreeks compute pipeline, plus the Feed interface for vendor
// adapters.
//
// Source-of-truth for these types lives here. Every other package depends
// on these definitions but not on any vendor (Databento) types — so we can
// swap vendors without rippling changes through compute, dealer, or store.
package feed

import (
	"context"
	"time"
)

// ─── Enums ────────────────────────────────────────────────────────────────

// Symbol identifies the underlying. Stored as uint8 for cache-friendly
// fixed-width packing.
type Symbol uint8

const (
	SymbolUnknown Symbol = 0
	SymbolSPX     Symbol = 1
	SymbolNDX     Symbol = 2
)

// String returns the human-readable ticker for the symbol.
func (s Symbol) String() string {
	switch s {
	case SymbolSPX:
		return "SPX"
	case SymbolNDX:
		return "NDX"
	default:
		return "UNKNOWN"
	}
}

// ParseSymbol maps a parent symbol string to our internal Symbol.
// Accepts "SPX", "SPXW", "NDX", "NDXP" (case-insensitive).
func ParseSymbol(s string) Symbol {
	switch s {
	case "SPX", "spx", "SPXW", "spxw":
		return SymbolSPX
	case "NDX", "ndx", "NDXP", "ndxp":
		return SymbolNDX
	default:
		return SymbolUnknown
	}
}

// Side is option side: call or put.
type Side uint8

const (
	SideUnknown Side = 0
	SideCall    Side = 1
	SidePut     Side = 2
)

// String returns "C", "P", or "?".
func (s Side) String() string {
	switch s {
	case SideCall:
		return "C"
	case SidePut:
		return "P"
	default:
		return "?"
	}
}

// TickType discriminates the kind of event carried in a Tick.
type TickType uint8

const (
	TickTypeUnknown TickType = 0
	TickTypeQuote   TickType = 1 // BBO update
	TickTypeTrade   TickType = 2 // executed print
	TickTypeOI      TickType = 3 // open interest update (start of day)
)

// Aggressor records who initiated a trade per Lee-Ready classification.
// Populated by classifier downstream of feed; raw feed sets Unknown.
type Aggressor uint8

const (
	AggressorUnknown Aggressor = 0
	AggressorBuy     Aggressor = 1 // lifted ask → customer bought
	AggressorSell    Aggressor = 2 // hit bid → customer sold
)

// AssetClass differentiates option ticks from futures ticks within the
// same Tick struct (futures are used for spot proxy / basis tracking).
type AssetClass uint8

const (
	AssetClassUnknown AssetClass = 0
	AssetClassOption  AssetClass = 1
	AssetClassFuture  AssetClass = 2
)

// ─── Tick ─────────────────────────────────────────────────────────────────

// Tick is the canonical normalized event. One value covers quotes, trades,
// OI updates, options, and futures.
//
// Layout is intentionally fixed-size where possible: no pointers, no
// time.Time (stored as nanos uint64 for cache-friendly hot path).
type Tick struct {
	// Timing — nanoseconds since unix epoch UTC. UTC for backend, frontend
	// converts to user TZ via Intl.DateTimeFormat.
	TsEvent uint64 // exchange event timestamp
	TsRecv  uint64 // ingest receive timestamp

	// Identification
	Symbol     Symbol
	AssetClass AssetClass
	TickType   TickType

	// Option-specific (zero for futures)
	Expiry uint32 // YYYYMMDD as int (e.g. 20260620)
	Strike uint32 // strike * 1000 (e.g. 5810500 = $5810.50)
	Side   Side

	// Future-specific (zero for options)
	FuturesContract [12]byte // e.g. "ESM6\x00\x00\x00\x00\x00\x00\x00\x00"

	// Trade fields (TickType == Trade)
	Price     float64
	Size      uint32
	Aggressor Aggressor

	// Quote fields (TickType == Quote)
	Bid     float64
	Ask     float64
	BidSize uint32
	AskSize uint32

	// OI field (TickType == OI)
	OpenInterest uint32

	// Provenance
	Exchange   uint8  // OPRA participant code or CME venue
	InstrumentID uint64 // raw vendor instrument id (for debugging / replay)
}

// ─── Feed interface ───────────────────────────────────────────────────────

// Feed is the contract any market-data vendor adapter must satisfy.
// dbn-go is the first implementation in internal/feed/databento.
type Feed interface {
	// Connect establishes a session with the upstream vendor and authenticates.
	Connect(ctx context.Context) error

	// Subscribe registers interest in the given subscriptions. Multiple calls
	// are additive (do not replace prior subscriptions).
	Subscribe(ctx context.Context, subs []Subscription) error

	// Start begins streaming. Ticks are delivered to the channel returned
	// by Ticks(). Caller must drain Ticks() promptly to avoid backpressure.
	Start(ctx context.Context) error

	// Ticks returns a receive-only channel of normalized ticks. Closed when
	// the feed terminates.
	Ticks() <-chan Tick

	// Errors returns a receive-only channel of non-fatal errors (e.g.
	// per-message decode failures). Fatal errors terminate Start().
	Errors() <-chan error

	// Stop closes the feed cleanly. Idempotent.
	Stop() error
}

// Subscription describes a desired data stream.
type Subscription struct {
	Dataset Dataset
	Schema  Schema
	Symbol  Symbol // FlowGreeks-internal symbol; adapter maps to vendor symbology
}

// Dataset identifies the upstream data source.
type Dataset string

const (
	DatasetOPRAPillar Dataset = "OPRA.PILLAR"
	DatasetCMEGlobex  Dataset = "GLBX.MDP3"
)

// Schema identifies the level of granularity / message type.
type Schema string

const (
	SchemaMBP1       Schema = "mbp-1"      // top-of-book single venue (GLBX.MDP3)
	SchemaCMBP1      Schema = "cmbp-1"     // consolidated MBP-1 across exchanges (OPRA.PILLAR)
	SchemaTrades     Schema = "trades"     // trade prints only
	SchemaTBBO       Schema = "tbbo"       // trade + BBO at trade time (GLBX)
	SchemaTCBBO      Schema = "tcbbo"      // trade + consolidated BBO (OPRA)
	SchemaDefinition Schema = "definition" // instrument definitions (strike, expiry, side)
	SchemaStatistics Schema = "statistics" // open interest + cumulative volume snapshots
)

// ─── Helpers ──────────────────────────────────────────────────────────────

// EventTime returns TsEvent as a time.Time. Convenience for non-hot-path
// code (logging, archival). Hot path should use uint64 directly.
func (t Tick) EventTime() time.Time {
	return time.Unix(0, int64(t.TsEvent)).UTC()
}

// ReceiveTime returns TsRecv as a time.Time. Same caveats as EventTime.
func (t Tick) ReceiveTime() time.Time {
	return time.Unix(0, int64(t.TsRecv)).UTC()
}

// IsOption reports whether the tick is an option event.
func (t Tick) IsOption() bool { return t.AssetClass == AssetClassOption }

// IsFuture reports whether the tick is a futures event.
func (t Tick) IsFuture() bool { return t.AssetClass == AssetClassFuture }
