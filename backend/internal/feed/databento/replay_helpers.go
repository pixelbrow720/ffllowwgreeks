// Replay-side helpers expose the unexported instrument registry and
// DBN→Tick converters so file-based replay binaries (cmd/replay_dbn) can
// reuse the exact same parsing as the live ingest path.
//
// Strictly additive: the live Client/visitor are untouched, so the hot
// path semantics, allocations, and behavior remain identical.

package databento

import (
	dbn "github.com/NimbleMarkets/dbn-go"

	"flowgreeks/internal/feed"
)

// Registry is an instrument_id → instrumentMeta cache, populated by
// resolving raw vendor symbols (e.g. OSI option strings or CME outright
// codes). The replay binary populates it from the definition schema
// before consuming quote/trade files.
type Registry struct {
	m map[uint32]instrumentMeta
}

// NewRegistry returns an empty registry sized for a typical OPRA day
// (~250k contracts across SPX + NDX, comfortably under 1MB).
func NewRegistry() *Registry {
	return &Registry{m: make(map[uint32]instrumentMeta, 4096)}
}

// Resolve parses raw and stores the resulting metadata under
// instrumentID. Returns false if the symbol is unrecognized (heartbeats,
// unknown roots) — callers may bump a counter but should not treat this
// as fatal.
func (r *Registry) Resolve(instrumentID uint32, raw string) bool {
	m, ok := resolveSymbol(raw)
	if !ok {
		return false
	}
	r.m[instrumentID] = m
	return true
}

// Lookup returns the symbol root for instrumentID, or feed.SymbolUnknown
// if the id was never resolved. Cheap — used by the replay symbol filter.
func (r *Registry) Lookup(instrumentID uint32) feed.Symbol {
	m, ok := r.m[instrumentID]
	if !ok {
		return feed.SymbolUnknown
	}
	return m.Symbol
}

// Size reports the number of resolved instruments.
func (r *Registry) Size() int { return len(r.m) }

// ConvertCmbp1 fills out from msg using the cached metadata for the
// record's instrument_id. Returns false (no Tick produced) when the id
// is unknown — caller should drop the record.
func (r *Registry) ConvertCmbp1(out *feed.Tick, msg *dbn.Cmbp1Msg, tsRecv uint64) bool {
	meta, ok := r.m[msg.Header.InstrumentID]
	if !ok {
		return false
	}
	convertCmbp1(out, msg, meta, tsRecv)
	return true
}

// ConvertMbp1 is the futures-side analogue: fills out from a single-venue
// MBP-1 quote (used by GLBX.MDP3 ES/NQ).
func (r *Registry) ConvertMbp1(out *feed.Tick, msg *dbn.Mbp1Msg, tsRecv uint64) bool {
	meta, ok := r.m[msg.Header.InstrumentID]
	if !ok {
		return false
	}
	convertMbp1(out, msg, meta, tsRecv)
	return true
}

// ConvertTrade fills out from an Mbp0 trade print.
func (r *Registry) ConvertTrade(out *feed.Tick, msg *dbn.Mbp0Msg, tsRecv uint64) bool {
	meta, ok := r.m[msg.Header.InstrumentID]
	if !ok {
		return false
	}
	convertTrade(out, msg, meta, tsRecv)
	return true
}

// DbnFixedString trims a fixed-size DBN byte buffer to the first NUL.
// Re-exported so the replay binary can decode RawSymbol without
// duplicating the loop.
func DbnFixedString(b []byte) string { return dbnFixedString(b) }
