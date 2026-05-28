// Package backtest evaluates rule-based strategies against historical
// state snapshots and reports performance metrics.
//
// Scope (v1): the strategy's edge is measured against the underlying's
// forward return after each entry signal — i.e. we treat the entry as
// a long/short on the index, and exit either on a separate exit
// predicate or when the holding period elapses. This isn't a full
// option-pricing P&L but it's the right primitive to validate a
// signal's directional value before spending effort on per-strike
// option simulation.
package backtest

import (
	"context"
	"errors"
	"fmt"
	"math"
	"sort"
	"time"

	"flowgreeks/internal/alerts"
	"flowgreeks/internal/feed"
)

// Direction of the trade taken on an entry signal.
type Direction int8

const (
	Long  Direction = +1
	Short Direction = -1
)

// Predicate evaluates a snapshot to a boolean. Returns true to fire.
type Predicate func(s alerts.Snapshot) bool

// Strategy declares an entry + exit pair plus a holding-period fallback.
type Strategy struct {
	Name        string
	Entry       Predicate
	Exit        Predicate
	Direction   Direction
	MaxHoldMin  float64 // 0 = no time-based exit
	CooldownMin float64 // ignore new entries within this many min after exit (default 5)
}

// Trade records one open→close cycle.
type Trade struct {
	EntryTs   time.Time
	ExitTs    time.Time
	EntrySpot float64
	ExitSpot  float64
	Direction Direction
	ReturnPct float64       // signed, in fraction (0.0042 = +0.42%)
	Held      time.Duration // ExitTs − EntryTs
	ExitReason string
}

// Result aggregates all trades + summary metrics.
type Result struct {
	Strategy   string
	Trades     []Trade
	Count      int
	Wins       int
	Losses     int
	WinRate    float64
	MeanReturn float64
	Stddev     float64
	Sharpe     float64
	Sortino    float64
	MaxDD      float64
	TotalRet   float64
	From       time.Time
	To         time.Time
}

// Snapshot pairs a timestamp + spot + alerts.Snapshot for backtest input.
// Caller hydrates this from the historical state archive.
type Snapshot struct {
	Ts   time.Time
	Spot float64
	State alerts.Snapshot
}

// Run evaluates the strategy over the snapshot stream and returns the
// performance Result. Exits stragglers at the end of the stream.
func Run(ctx context.Context, strat Strategy, stream <-chan Snapshot) (Result, error) {
	if strat.Entry == nil {
		return Result{}, errors.New("backtest: nil entry predicate")
	}
	if strat.Direction != Long && strat.Direction != Short {
		strat.Direction = Long
	}
	if strat.CooldownMin <= 0 {
		strat.CooldownMin = 5
	}

	var (
		open       bool
		entryAt    time.Time
		entrySpot  float64
		lastExitAt time.Time
		lastSpot   float64
		trades     = []Trade{}
		first, last time.Time
	)

	for {
		select {
		case <-ctx.Done():
			return Result{}, ctx.Err()
		case s, ok := <-stream:
			if !ok {
				if open {
					if last.IsZero() {
						last = entryAt
					}
					exitSpot := lastSpot
					if exitSpot == 0 {
						exitSpot = entrySpot
					}
					trades = append(trades, Trade{
						EntryTs: entryAt, ExitTs: last,
						EntrySpot: entrySpot, ExitSpot: exitSpot,
						Direction: strat.Direction,
						ReturnPct: returnPct(entrySpot, exitSpot, strat.Direction),
						Held:      last.Sub(entryAt),
						ExitReason: "stream_end",
					})
				}
				return summarize(strat.Name, trades, first, last), nil
			}
			if first.IsZero() {
				first = s.Ts
			}
			last = s.Ts
			if s.Spot > 0 {
				lastSpot = s.Spot
			}

			if open {
				exit := false
				reason := ""
				if strat.Exit != nil && strat.Exit(s.State) {
					exit = true
					reason = "exit_signal"
				}
				if !exit && strat.MaxHoldMin > 0 {
					if s.Ts.Sub(entryAt).Minutes() >= strat.MaxHoldMin {
						exit = true
						reason = "max_hold"
					}
				}
				if exit {
					trades = append(trades, Trade{
						EntryTs: entryAt, ExitTs: s.Ts,
						EntrySpot: entrySpot, ExitSpot: s.Spot,
						Direction: strat.Direction,
						ReturnPct: returnPct(entrySpot, s.Spot, strat.Direction),
						Held:      s.Ts.Sub(entryAt),
						ExitReason: reason,
					})
					open = false
					lastExitAt = s.Ts
				}
				continue
			}

			// Cooldown gate.
			if !lastExitAt.IsZero() && s.Ts.Sub(lastExitAt).Minutes() < strat.CooldownMin {
				continue
			}
			if strat.Entry(s.State) && s.Spot > 0 {
				open = true
				entryAt = s.Ts
				entrySpot = s.Spot
			}
		}
	}
}

func returnPct(entry, exit float64, dir Direction) float64 {
	if entry <= 0 {
		return 0
	}
	r := (exit - entry) / entry
	if dir == Short {
		r = -r
	}
	return r
}

// summarize computes summary metrics over the trade list.
//
// Annualization: Sharpe and Sortino are scaled by sqrt(tradesPerYear),
// where tradesPerYear is derived from the actual test window so a
// strategy firing 4 trades/day and one firing 1/week aren't both
// scaled by sqrt(252). When the window is too narrow to estimate
// (single trade, zero-duration window) we leave the ratio in
// trade-space.
//
// Sortino is computed independently of MeanReturn's sign — the previous
// formula short-circuited to 0 when mean was exactly 0, which is wrong
// by definition (Sortino is defined whenever downside deviation > 0).
func summarize(name string, trades []Trade, from, to time.Time) Result {
	r := Result{Strategy: name, Trades: trades, From: from, To: to, Count: len(trades)}
	if len(trades) == 0 {
		return r
	}
	rets := make([]float64, len(trades))
	for i, t := range trades {
		rets[i] = t.ReturnPct
		if t.ReturnPct > 0 {
			r.Wins++
		} else if t.ReturnPct < 0 {
			r.Losses++
		}
	}
	r.WinRate = float64(r.Wins) / float64(len(trades))
	r.MeanReturn = mean(rets)
	r.Stddev = stddev(rets, r.MeanReturn)

	annFactor := annualisationFactor(len(trades), from, to)
	if r.Stddev > 0 {
		r.Sharpe = r.MeanReturn / r.Stddev * annFactor
	}
	r.Sortino = sortino(rets, r.MeanReturn) * annFactor
	r.TotalRet = compoundReturn(rets)
	r.MaxDD = maxDrawdown(rets)
	return r
}

// annualisationFactor returns sqrt(trades/year) derived from the actual
// test window. Falls back to 1 (no scaling) when the window is too
// narrow to estimate, so the caller always gets a stable ratio.
func annualisationFactor(n int, from, to time.Time) float64 {
	if n < 2 || from.IsZero() || to.IsZero() {
		return 1
	}
	d := to.Sub(from).Hours() / 24
	if d <= 0 {
		return 1
	}
	tradesPerYear := float64(n) * (365.25 / d)
	if tradesPerYear <= 1 {
		return 1
	}
	return math.Sqrt(tradesPerYear)
}

func mean(xs []float64) float64 {
	if len(xs) == 0 {
		return 0
	}
	var s float64
	for _, x := range xs {
		s += x
	}
	return s / float64(len(xs))
}

func stddev(xs []float64, m float64) float64 {
	if len(xs) < 2 {
		return 0
	}
	var s float64
	for _, x := range xs {
		s += (x - m) * (x - m)
	}
	return math.Sqrt(s / float64(len(xs)-1))
}

// sortino: mean / stddev of negative returns only — penalizes downside.
// Defined whenever downside deviation > 0; the previous m == 0 short-
// circuit returned 0 even when downside was real, which is wrong by
// definition.
func sortino(xs []float64, m float64) float64 {
	var down float64
	var n int
	for _, x := range xs {
		if x < 0 {
			down += x * x
			n++
		}
	}
	if n == 0 {
		return 0
	}
	dd := math.Sqrt(down / float64(n))
	if dd == 0 {
		return 0
	}
	return m / dd
}

// compoundReturn computes (1+r1)(1+r2)... − 1.
func compoundReturn(xs []float64) float64 {
	v := 1.0
	for _, x := range xs {
		v *= (1 + x)
	}
	return v - 1
}

// maxDrawdown computes the peak-to-trough drawdown of the equity curve.
func maxDrawdown(xs []float64) float64 {
	v, peak, dd := 1.0, 1.0, 0.0
	for _, x := range xs {
		v *= (1 + x)
		if v > peak {
			peak = v
		}
		drawdown := (peak - v) / peak
		if drawdown > dd {
			dd = drawdown
		}
	}
	return dd
}

// SortTradesByReturn sorts a trade list in descending return order. Used
// for "best/worst trades" reports.
func SortTradesByReturn(ts []Trade) {
	sort.Slice(ts, func(i, j int) bool { return ts[i].ReturnPct > ts[j].ReturnPct })
}

// String returns a summary line for diagnostic logging.
func (r Result) String() string {
	return fmt.Sprintf(
		"%s: trades=%d win%%=%.1f mean=%.4f sharpe=%.2f maxDD=%.2f%% total=%.2f%%",
		r.Strategy, r.Count, r.WinRate*100, r.MeanReturn, r.Sharpe, r.MaxDD*100, r.TotalRet*100,
	)
}

// PredicateFromAlertRule reuses an alerts.Rule predicate so the same
// signal that fires alerts can be backtested without redefinition.
func PredicateFromAlertRule(r alerts.Rule, sym feed.Symbol) Predicate {
	return func(s alerts.Snapshot) bool {
		if s.Symbol != sym {
			return false
		}
		// Lean on alerts.Engine via a one-off engine? Cheaper: replicate
		// the predicate inline. Engine.evaluate is unexported; expose
		// a public helper here when the alerts package gains one.
		return alertsRuleMatches(r, s)
	}
}
