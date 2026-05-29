package api

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"flowgreeks/internal/feed"
	"flowgreeks/internal/store"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Handlers carries the REST surface for the api binary. Wire via Mount.
//
// Pool is optional — when set, the public history endpoint reads from
// dealer_state_1s so dashboards can backfill spot/dpi/etc instead of
// waiting for the WS to produce a fresh sample. Nil Pool keeps the
// dev-without-postgres path open: history endpoint returns 503.
type Handlers struct {
	Cache  *Cache
	Broker *Broker
	Pool   *pgxpool.Pool
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
	r.Get("/api/history/{symbol}", h.history)
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

// history returns a compact time-series of dealer state for the given
// symbol over the requested window, downsampled to keep the payload
// reasonable. Used by the dashboard to backfill spot/dpi/etc on first
// paint instead of waiting for the WS stream to produce a fresh
// minute of samples.
//
//	GET /api/history/spx?from=2026-02-12T13:30:00Z&to=2026-02-12T16:00:00Z&max=720
//	  -> { from, to, stride, samples: [...] }
//
// Pool absent ⇒ 503 (dev-without-postgres path).
func (h *Handlers) history(w http.ResponseWriter, r *http.Request) {
	if h.Pool == nil {
		writeJSONError(w, http.StatusServiceUnavailable, "history disabled (no pool)")
		return
	}
	sym := feed.ParseSymbol(strings.ToUpper(chi.URLParam(r, "symbol")))
	if sym == feed.SymbolUnknown {
		writeJSONError(w, http.StatusBadRequest, "unknown symbol")
		return
	}

	now := time.Now().UTC()
	from := now.Add(-2 * time.Hour)
	to := now
	if s := r.URL.Query().Get("from"); s != "" {
		t, err := time.Parse(time.RFC3339Nano, s)
		if err != nil {
			writeJSONError(w, http.StatusBadRequest, "from: "+err.Error())
			return
		}
		from = t.UTC()
	}
	if s := r.URL.Query().Get("to"); s != "" {
		t, err := time.Parse(time.RFC3339Nano, s)
		if err != nil {
			writeJSONError(w, http.StatusBadRequest, "to: "+err.Error())
			return
		}
		to = t.UTC()
	}
	if !to.After(from) {
		writeJSONError(w, http.StatusBadRequest, "to must be after from")
		return
	}
	// Hard cap window. 24h covers any single session we'd ever paint;
	// anything bigger is a caller bug.
	if to.Sub(from) > 24*time.Hour {
		writeJSONError(w, http.StatusBadRequest, "window > 24h")
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	rows, err := store.QueryStates(ctx, h.Pool, sym, from, to)
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, "query: "+err.Error())
		return
	}

	// Downsample for sane payload size. Backend writes 1Hz; browser
	// chart only needs ~1 sample / 5-15s for a smooth 2h window.
	maxSamples := 720
	if v := r.URL.Query().Get("max"); v != "" {
		var n int
		if err := json.Unmarshal([]byte(v), &n); err == nil && n > 0 && n <= 5000 {
			maxSamples = n
		}
	}
	stride := 1
	if len(rows) > maxSamples {
		stride = len(rows) / maxSamples
		if stride < 1 {
			stride = 1
		}
	}

	type sample struct {
		TsNs         uint64  `json:"ts_ns"`
		Spot         float64 `json:"spot"`
		NetGEX       float64 `json:"net_gex"`
		ZeroGamma    float64 `json:"zero_gamma"`
		CallWall     float64 `json:"call_wall"`
		PutWall      float64 `json:"put_wall"`
		DPI          float32 `json:"dpi"`
		Regime       uint8   `json:"regime"`
		CharmZone    uint8   `json:"charm_zone"`
		PinActive    bool    `json:"pin_active"`
		PinTopStrike float64 `json:"pin_top_strike"`
		PinTopProb   float32 `json:"pin_top_prob"`
	}
	out := struct {
		From    time.Time `json:"from"`
		To      time.Time `json:"to"`
		Stride  int       `json:"stride"`
		Samples []sample  `json:"samples"`
	}{From: from, To: to, Stride: stride}

	out.Samples = make([]sample, 0, len(rows)/stride+1)
	for i := 0; i < len(rows); i += stride {
		row := rows[i]
		out.Samples = append(out.Samples, sample{
			TsNs:         uint64(row.Ts.UnixNano()),
			Spot:         row.Spot,
			NetGEX:       row.NetGEX,
			ZeroGamma:    row.ZeroGamma,
			CallWall:     row.CallWall,
			PutWall:      row.PutWall,
			DPI:          row.DPIComposite,
			Regime:       uint8(row.Regime),
			CharmZone:    uint8(row.CharmZone),
			PinActive:    row.PinActive,
			PinTopStrike: row.PinTopStrike,
			PinTopProb:   row.PinTopProb,
		})
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(out)
}
