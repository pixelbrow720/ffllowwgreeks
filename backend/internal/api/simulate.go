package api

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"

	"flowgreeks/internal/dealer"
	"flowgreeks/internal/feed"

	"github.com/go-chi/chi/v5"
)

// SimulateRequest is the JSON payload for POST /api/simulate/{symbol}.
//
// Fields are the same as dealer.ScenarioInput but copied here so the
// HTTP layer doesn't leak the internal type name into the OpenAPI surface.
type SimulateRequest struct {
	SpotPctChange   float64 `json:"spot_pct_change"`
	DurationMinutes float64 `json:"duration_minutes"`
	VolPtChange     float64 `json:"vol_pt_change"`
}

// SimulateResponse mirrors dealer.ScenarioResult with JSON-friendly tags.
// We rebuild it rather than expose the dealer type so the wire schema is
// stable across internal refactors.
type SimulateResponse struct {
	Symbol           string                  `json:"symbol"`
	Spot             float64                 `json:"spot"`
	NewSpot          float64                 `json:"new_spot"`
	DurationYears    float64                 `json:"duration_years"`
	ForcedDelta      float64                 `json:"forced_delta"`
	ForcedNotional   float64                 `json:"forced_notional"`
	CharmAid         float64                 `json:"charm_aid"`
	NetPressure      float64                 `json:"net_pressure"`
	TopContributions []strikeContributionDTO `json:"top_contributions"`
}

type strikeContributionDTO struct {
	Expiry         uint32  `json:"expiry"`
	Strike         float64 `json:"strike"`
	Side           string  `json:"side"` // "C" / "P"
	OldDelta       float64 `json:"old_delta"`
	NewDelta       float64 `json:"new_delta"`
	DeltaChange    float64 `json:"delta_change"`
	ForcedNotional float64 `json:"forced_notional"`
}

// simulate runs the What-If Dealer Simulator against the latest snapshot
// for the requested symbol.
//
// The simulator wants populated StrikeRow values (DealerPos, IV, Delta,
// Charm). The cached snapshot from compute already carries them in its
// `strikes` array, so we decode → simulate → encode.
func (h *Handlers) simulate(w http.ResponseWriter, r *http.Request) {
	sym := feed.ParseSymbol(strings.ToUpper(chi.URLParam(r, "symbol")))
	if sym == feed.SymbolUnknown {
		writeJSONError(w, http.StatusBadRequest, "unknown symbol")
		return
	}

	body, err := io.ReadAll(io.LimitReader(r.Body, 4096))
	if err != nil {
		writeJSONError(w, http.StatusBadRequest, "read body: "+err.Error())
		return
	}
	defer r.Body.Close()

	var req SimulateRequest
	if len(body) > 0 {
		if err := json.Unmarshal(body, &req); err != nil {
			writeJSONError(w, http.StatusBadRequest, "decode body: "+err.Error())
			return
		}
	}

	snap, ok := h.Cache.Get(sym, StateKindGEX)
	if !ok {
		writeJSONError(w, http.StatusNotFound, "no state available yet — wait for first compute publish")
		return
	}

	var raw struct {
		Spot    float64 `json:"spot"`
		Strikes []struct {
			Expiry      uint32  `json:"expiry"`
			Strike      uint32  `json:"strike"`
			Side        uint8   `json:"side"`
			DealerPos   int64   `json:"dealer_pos"`
			IV          float64 `json:"iv"`
			Gamma       float64 `json:"gamma"`
			Charm       float64 `json:"charm"`
			Vanna       float64 `json:"vanna"`
			GEXNotional float64 `json:"gex_notional"`
		} `json:"strikes"`
	}
	if err := json.Unmarshal(snap.Data, &raw); err != nil {
		writeJSONError(w, http.StatusInternalServerError, "decode snapshot: "+err.Error())
		return
	}

	rows := make([]dealer.StrikeRow, 0, len(raw.Strikes))
	for _, s := range raw.Strikes {
		rows = append(rows, dealer.StrikeRow{
			Expiry:    s.Expiry,
			Strike:    s.Strike,
			Side:      feed.Side(s.Side),
			DealerPos: s.DealerPos,
			IV:        s.IV,
			Gamma:     s.Gamma,
			Charm:     s.Charm,
			Vanna:     s.Vanna,
		})
	}

	// Default rate/yield mirrors cmd/compute. M6 calendar service can
	// surface these via Cache so they aren't hard-coded here.
	const r0, q0 = 0.045, 0.013

	res := dealer.Simulate(rows, raw.Spot, time.Now().UTC(), r0, q0, dealer.ScenarioInput{
		SpotPctChange:   req.SpotPctChange,
		DurationMinutes: req.DurationMinutes,
		VolPtChange:     req.VolPtChange,
	})

	resp := SimulateResponse{
		Symbol:         strings.ToLower(sym.String()),
		Spot:           raw.Spot,
		NewSpot:        res.NewSpot,
		DurationYears:  res.DurationYears,
		ForcedDelta:    res.ForcedDelta,
		ForcedNotional: res.ForcedNotional,
		CharmAid:       res.CharmAid,
		NetPressure:    res.NetPressure,
	}
	resp.TopContributions = make([]strikeContributionDTO, 0, len(res.TopContributions))
	for _, c := range res.TopContributions {
		side := "C"
		if c.Side == feed.SidePut {
			side = "P"
		}
		resp.TopContributions = append(resp.TopContributions, strikeContributionDTO{
			Expiry:         c.Expiry,
			Strike:         feed.DecodeStrike(c.Strike),
			Side:           side,
			OldDelta:       c.OldDelta,
			NewDelta:       c.NewDelta,
			DeltaChange:    c.DeltaChange,
			ForcedNotional: c.ForcedNotional,
		})
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}
