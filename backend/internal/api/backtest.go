package api

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"

	"flowgreeks/internal/alerts"
	"flowgreeks/internal/backtest"
	"flowgreeks/internal/feed"
	"flowgreeks/internal/store"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// BacktestHandlers exposes POST /api/backtest/run. Requires a pgxpool
// pointed at a database with dealer_state_1s populated by compute.
type BacktestHandlers struct {
	Pool *pgxpool.Pool
}

// Mount registers the backtest endpoint on r.
func (h *BacktestHandlers) Mount(r chi.Router) {
	r.Post("/api/backtest/run", h.run)
}

// runRequest is the wire shape accepted by POST /api/backtest/run.
//
// Entry / Exit reuse alerts.Rule's (Kind, Threshold, StringArg) triple so
// any rule a user already saved on the alerts engine can be backtested
// without redefinition. Symbol is taken from the rule. Direction is
// "long" or "short"; defaults to long.
type runRequest struct {
	Symbol      string    `json:"symbol"`
	From        time.Time `json:"from"`
	To          time.Time `json:"to"`
	Direction   string    `json:"direction"`
	MaxHoldMin  float64   `json:"max_hold_min"`
	CooldownMin float64   `json:"cooldown_min"`
	Entry       ruleSpec  `json:"entry"`
	Exit        *ruleSpec `json:"exit,omitempty"`
	Name        string    `json:"name"`
}

type ruleSpec struct {
	Kind      alerts.RuleKind `json:"kind"`
	Threshold float64         `json:"threshold,omitempty"`
	StringArg string          `json:"string_arg,omitempty"`
}

type tradeOut struct {
	EntryTs    time.Time `json:"entry_ts"`
	ExitTs     time.Time `json:"exit_ts"`
	EntrySpot  float64   `json:"entry_spot"`
	ExitSpot   float64   `json:"exit_spot"`
	Direction  string    `json:"direction"`
	ReturnPct  float64   `json:"return_pct"`
	HeldSec    float64   `json:"held_sec"`
	ExitReason string    `json:"exit_reason"`
}

type runResponse struct {
	Strategy   string     `json:"strategy"`
	Symbol     string     `json:"symbol"`
	From       time.Time  `json:"from"`
	To         time.Time  `json:"to"`
	Count      int        `json:"count"`
	Wins       int        `json:"wins"`
	Losses     int        `json:"losses"`
	WinRate    float64    `json:"win_rate"`
	MeanReturn float64    `json:"mean_return"`
	Stddev     float64    `json:"stddev"`
	Sharpe     float64    `json:"sharpe"`
	Sortino    float64    `json:"sortino"`
	MaxDD      float64    `json:"max_dd"`
	TotalRet   float64    `json:"total_return"`
	Snapshots  int        `json:"snapshots_evaluated"`
	Trades     []tradeOut `json:"trades"`
}

func (h *BacktestHandlers) run(w http.ResponseWriter, r *http.Request) {
	if h == nil || h.Pool == nil {
		writeJSONError(w, http.StatusServiceUnavailable, "backtest disabled: no postgres pool")
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 64*1024))
	if err != nil {
		writeJSONError(w, http.StatusBadRequest, "read body: "+err.Error())
		return
	}
	defer r.Body.Close()

	var req runRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSONError(w, http.StatusBadRequest, "decode: "+err.Error())
		return
	}
	sym := feed.ParseSymbol(strings.ToUpper(req.Symbol))
	if sym == feed.SymbolUnknown {
		writeJSONError(w, http.StatusBadRequest, "unknown symbol")
		return
	}
	if req.From.IsZero() || req.To.IsZero() || !req.To.After(req.From) {
		writeJSONError(w, http.StatusBadRequest, "from/to required, to must be after from")
		return
	}
	if req.To.Sub(req.From) > 31*24*time.Hour {
		writeJSONError(w, http.StatusBadRequest, "range too large (>31d)")
		return
	}
	if req.Entry.Kind == "" {
		writeJSONError(w, http.StatusBadRequest, "entry.kind required")
		return
	}

	dir := backtest.Long
	if strings.EqualFold(req.Direction, "short") {
		dir = backtest.Short
	}
	name := req.Name
	if name == "" {
		name = string(req.Entry.Kind)
	}

	entryRule := alerts.Rule{
		Symbol: sym, Kind: req.Entry.Kind,
		Threshold: req.Entry.Threshold, StringArg: req.Entry.StringArg,
	}
	strat := backtest.Strategy{
		Name:        name,
		Entry:       backtest.PredicateFromAlertRule(entryRule, sym),
		Direction:   dir,
		MaxHoldMin:  req.MaxHoldMin,
		CooldownMin: req.CooldownMin,
	}
	if req.Exit != nil {
		exitRule := alerts.Rule{
			Symbol: sym, Kind: req.Exit.Kind,
			Threshold: req.Exit.Threshold, StringArg: req.Exit.StringArg,
		}
		strat.Exit = backtest.PredicateFromAlertRule(exitRule, sym)
	}

	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()

	rows, err := store.QueryStates(ctx, h.Pool, sym, req.From, req.To)
	if err != nil {
		writeJSONError(w, http.StatusInternalServerError, "query states: "+err.Error())
		return
	}
	if len(rows) == 0 {
		writeJSONError(w, http.StatusNotFound, "no state rows in range — compute may not have written any")
		return
	}

	stream := make(chan backtest.Snapshot, 256)
	go func() {
		defer close(stream)
		for _, sr := range rows {
			snap := backtest.Snapshot{
				Ts:    sr.Ts,
				Spot:  sr.Spot,
				State: stateRowToAlertSnapshot(sr),
			}
			select {
			case <-ctx.Done():
				return
			case stream <- snap:
			}
		}
	}()

	res, err := backtest.Run(ctx, strat, stream)
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			writeJSONError(w, http.StatusGatewayTimeout, "backtest exceeded 30s budget")
			return
		}
		writeJSONError(w, http.StatusInternalServerError, "backtest run: "+err.Error())
		return
	}

	out := runResponse{
		Strategy:   res.Strategy,
		Symbol:     strings.ToLower(sym.String()),
		From:       req.From,
		To:         req.To,
		Count:      res.Count,
		Wins:       res.Wins,
		Losses:     res.Losses,
		WinRate:    res.WinRate,
		MeanReturn: res.MeanReturn,
		Stddev:     res.Stddev,
		Sharpe:     res.Sharpe,
		Sortino:    res.Sortino,
		MaxDD:      res.MaxDD,
		TotalRet:   res.TotalRet,
		Snapshots:  len(rows),
		Trades:     make([]tradeOut, 0, len(res.Trades)),
	}
	for _, t := range res.Trades {
		dirStr := "long"
		if t.Direction == backtest.Short {
			dirStr = "short"
		}
		out.Trades = append(out.Trades, tradeOut{
			EntryTs: t.EntryTs, ExitTs: t.ExitTs,
			EntrySpot: t.EntrySpot, ExitSpot: t.ExitSpot,
			Direction: dirStr, ReturnPct: t.ReturnPct,
			HeldSec: t.Held.Seconds(), ExitReason: t.ExitReason,
		})
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(out)
}

// stateRowToAlertSnapshot projects a stored dealer_state row into the
// trimmed alerts.Snapshot the backtest predicate consumes.
func stateRowToAlertSnapshot(r store.StateRow) alerts.Snapshot {
	s := alerts.Snapshot{
		Symbol:    r.Symbol,
		TsNs:      uint64(r.Ts.UnixNano()),
		NetGEX:    r.NetGEX,
		Regime:    uint8(r.Regime),
		CharmZone: uint8(r.CharmZone),
	}
	s.DPI.Composite = float64(r.DPIComposite)
	s.Pin.Active = r.PinActive
	s.Pin.TopProbability = float64(r.PinTopProb)
	s.Pin.TopStrike = r.PinTopStrike
	return s
}
