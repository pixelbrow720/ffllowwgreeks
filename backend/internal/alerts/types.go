// Package alerts evaluates user-defined trigger rules against the live
// dealer state and delivers events to subscribed channels.
//
// Design:
//   - Rules are declarative and stateful: a rule describes a condition
//     plus a cooldown window so a noisy signal doesn't spam.
//   - The engine consumes the same JSON snapshots the api WS broker
//     serves to dashboard clients, evaluates rules per snapshot, and
//     emits Trigger events.
//   - Delivery is pluggable (Sink interface). Built-in sinks: webhook
//     (HTTP POST), WS broadcast (in-process channel for the dashboard
//     "Alerts" panel).
package alerts

import (
	"encoding/json"
	"time"

	"flowgreeks/internal/feed"
)

// RuleKind identifies the predicate flavour. New kinds extend this enum
// and add a case in evaluate().
type RuleKind string

const (
	// DPI cross/threshold rules.
	RuleDPIAbove RuleKind = "dpi_above"
	RuleDPIBelow RuleKind = "dpi_below"
	// Charm zone enter (zone equals one of the configured values).
	RuleCharmZone RuleKind = "charm_zone"
	// Regime equals SHORT/LONG/NEUTRAL.
	RuleRegime RuleKind = "regime"
	// Pin probability above threshold while engine active.
	RulePinProb RuleKind = "pin_prob_above"
	// Net GEX threshold (signed).
	RuleNetGEXAbove RuleKind = "net_gex_above"
	RuleNetGEXBelow RuleKind = "net_gex_below"
)

// Rule is a single user-defined alert. ID + UserID identify it.
type Rule struct {
	ID       string        `json:"id"`
	UserID   string        `json:"user_id"`
	Symbol   feed.Symbol   `json:"symbol"`
	Kind     RuleKind      `json:"kind"`
	Threshold float64      `json:"threshold,omitempty"`
	StringArg string       `json:"string_arg,omitempty"` // e.g. zone or regime label
	Cooldown  time.Duration `json:"cooldown_ns,omitempty"`
	Sinks     []string     `json:"sinks,omitempty"` // ids of sinks to deliver to
	Enabled   bool         `json:"enabled"`

	// Internal: not serialised
	lastFiredAt time.Time
}

// Trigger is one delivered alert event.
type Trigger struct {
	RuleID   string                 `json:"rule_id"`
	UserID   string                 `json:"user_id"`
	Symbol   string                 `json:"symbol"`
	Kind     RuleKind               `json:"kind"`
	TsNs     uint64                 `json:"ts_ns"`
	Text     string                 `json:"text"`
	Refs     map[string]any         `json:"refs,omitempty"`
}

// Snapshot is the subset of compute's state the alerts engine reads.
// Mirrors the on-the-wire `state.<sym>.gex` shape.
type Snapshot struct {
	Symbol     feed.Symbol `json:"-"`
	TsNs       uint64      `json:"ts_ns"`
	NetGEX     float64     `json:"net_gex"`
	Regime     uint8       `json:"regime"` // dealer.Regime
	CharmZone  uint8       `json:"charm_zone"`
	DPI        struct {
		Composite float64 `json:"composite"`
	} `json:"dpi"`
	Pin struct {
		Active         bool    `json:"active"`
		TopProbability float64 `json:"top_probability"`
		TopStrike      float64 `json:"top_strike"`
	} `json:"pin"`
}

// DecodeSnapshot parses a `state.<sym>.gex` JSON payload (the same shape
// cmd/compute publishes) into our trimmed Snapshot. Symbol must be
// supplied by the caller — the wire payload carries the int code which
// is harder to use here.
func DecodeSnapshot(sym feed.Symbol, data []byte) (Snapshot, error) {
	var s Snapshot
	if err := json.Unmarshal(data, &s); err != nil {
		return Snapshot{}, err
	}
	s.Symbol = sym
	return s, nil
}
