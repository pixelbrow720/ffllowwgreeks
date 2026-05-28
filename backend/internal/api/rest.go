package api

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"flowgreeks/internal/feed"

	"github.com/go-chi/chi/v5"
)

// Handlers carries the REST surface for the api binary. Wire via Mount.
type Handlers struct {
	Cache  *Cache
	Broker *Broker
}

// Mount attaches every REST route on this Handlers struct.
// Equivalent to calling MountPublic + MountProtected on the same router.
func (h *Handlers) Mount(r chi.Router) {
	h.MountPublic(r)
	h.MountProtected(r)
}

// MountPublic registers routes that should NOT be gated by auth — they
// power landing pages, marketing surfaces, and the read-only dashboard
// view a logged-out user sees.
func (h *Handlers) MountPublic(r chi.Router) {
	r.Get("/api/snapshot/{symbol}", h.snapshot)
	r.Get("/api/levels/{symbol}", h.levels)
}

// MountProtected registers routes that perform real work and should
// be gated when APIKEY_ENABLED=true. The api binary wraps the chi.Router
// passed here with apikey.Middleware; during dev (gate disabled) it's
// just the root router and these routes stay open.
func (h *Handlers) MountProtected(r chi.Router) {
	r.Post("/api/simulate/{symbol}", h.simulate)
}

// snapshot returns the latest full state snapshot for a symbol.
//
//	GET /api/snapshot/spx -> raw compute state JSON
func (h *Handlers) snapshot(w http.ResponseWriter, r *http.Request) {
	sym := feed.ParseSymbol(strings.ToUpper(chi.URLParam(r, "symbol")))
	if sym == feed.SymbolUnknown {
		writeJSONError(w, http.StatusBadRequest, "unknown symbol")
		return
	}
	snap, ok := h.Cache.Get(sym, StateKindGEX)
	if !ok {
		writeJSONError(w, http.StatusNotFound, "no state available yet")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Snapshot-Ts-Ns", time.Unix(0, int64(snap.TsNs)).UTC().Format(time.RFC3339Nano))
	_, _ = w.Write(snap.Data)
}

// levels extracts just the key-level fields (walls, flip, expected move)
// from the latest snapshot. Cheap projection so dashboards don't have
// to download the full strike matrix.
//
//	GET /api/levels/spx -> { spot, zero_gamma, call_wall, put_wall, expected_mv }
func (h *Handlers) levels(w http.ResponseWriter, r *http.Request) {
	sym := feed.ParseSymbol(strings.ToUpper(chi.URLParam(r, "symbol")))
	if sym == feed.SymbolUnknown {
		writeJSONError(w, http.StatusBadRequest, "unknown symbol")
		return
	}
	snap, ok := h.Cache.Get(sym, StateKindGEX)
	if !ok {
		writeJSONError(w, http.StatusNotFound, "no state available yet")
		return
	}
	var full struct {
		Spot       float64 `json:"spot"`
		ZeroGamma  float64 `json:"zero_gamma"`
		CallWall   float64 `json:"call_wall"`
		PutWall    float64 `json:"put_wall"`
		ExpectedMv float64 `json:"expected_mv"`
		NetGEX     float64 `json:"net_gex"`
		Regime     uint8   `json:"regime"`
		TsNs       uint64  `json:"ts_ns"`
	}
	if err := json.Unmarshal(snap.Data, &full); err != nil {
		writeJSONError(w, http.StatusInternalServerError, "decode snapshot")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(full)
}

func writeJSONError(w http.ResponseWriter, status int, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write([]byte(`{"error":` + jsonString(msg) + `}`))
}

func jsonString(s string) string {
	b, err := json.Marshal(s)
	if err != nil {
		return `""`
	}
	return string(b)
}
