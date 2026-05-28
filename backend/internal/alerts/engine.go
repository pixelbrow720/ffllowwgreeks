package alerts

import (
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"
)

// ErrRuleNotOwned is returned when a tenant tries to upsert / remove a
// rule whose existing UserID differs from theirs. Callers translate this
// to 404 (not 403) so existence isn't probeable.
var ErrRuleNotOwned = errors.New("alerts: rule belongs to a different owner")

// Engine holds the rule set, evaluates each Snapshot against it, and
// dispatches Triggers to the registered sinks.
//
// Thread-safety: Rule CRUD is RWMutex-guarded; OnSnapshot is called
// from a single goroutine in the api binary (the NATS subscriber) so
// no locking is needed on the hot path beyond the rule-list snapshot.
type Engine struct {
	mu    sync.RWMutex
	rules map[string]*Rule

	// sinksMu guards sinks. Modifying the sink set is rare; reading
	// happens on every trigger.
	sinksMu sync.RWMutex
	sinks   map[string]Sink
}

// NewEngine constructs an empty engine.
func NewEngine() *Engine {
	return &Engine{
		rules: make(map[string]*Rule, 32),
		sinks: make(map[string]Sink, 4),
	}
}

// AddRule installs a new rule. Replaces any existing rule with the
// same id.
func (e *Engine) AddRule(r Rule) {
	if r.Cooldown <= 0 {
		r.Cooldown = 60 * time.Second
	}
	e.mu.Lock()
	rr := r
	e.rules[r.ID] = &rr
	count := len(e.rules)
	e.mu.Unlock()
	rulesGauge.Set(float64(count))
}

// RemoveRule deletes a rule. Returns false if it was not present.
//
// No ownership check — exposed for seeding (tests, e2e harness, admin
// tooling). REST handlers must use RemoveRuleForOwner instead so one
// tenant cannot delete another tenant's rule by id.
func (e *Engine) RemoveRule(id string) bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	if _, ok := e.rules[id]; !ok {
		return false
	}
	delete(e.rules, id)
	rulesGauge.Set(float64(len(e.rules)))
	return true
}

// UpsertRuleForOwner installs or replaces a rule, but only if the
// existing rule (when present) already belongs to ownerID. Returns
// ErrRuleNotOwned otherwise so a tenant cannot hijack another tenant's
// rule by POSTing the same id.
//
// The rule's UserID is force-set to ownerID before storage; callers do
// not need to set it themselves.
func (e *Engine) UpsertRuleForOwner(r Rule, ownerID string) error {
	if ownerID == "" {
		return ErrRuleNotOwned
	}
	if r.Cooldown <= 0 {
		r.Cooldown = 60 * time.Second
	}
	r.UserID = ownerID
	e.mu.Lock()
	defer e.mu.Unlock()
	if existing, ok := e.rules[r.ID]; ok && existing.UserID != ownerID {
		return ErrRuleNotOwned
	}
	rr := r
	e.rules[r.ID] = &rr
	rulesGauge.Set(float64(len(e.rules)))
	return nil
}

// RemoveRuleForOwner deletes a rule only when it belongs to ownerID.
// Returns false if the rule does not exist OR it belongs to someone
// else — handlers should map both to 404 so existence is not probeable.
func (e *Engine) RemoveRuleForOwner(id, ownerID string) bool {
	if ownerID == "" {
		return false
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	r, ok := e.rules[id]
	if !ok || r.UserID != ownerID {
		return false
	}
	delete(e.rules, id)
	rulesGauge.Set(float64(len(e.rules)))
	return true
}

// ListRules returns a copy of the rule set, optionally filtered by user.
// Empty userID returns all rules (admin view). Results are sorted by
// rule ID so paged responses are stable across calls — without a sort,
// map iteration order would scramble pages.
func (e *Engine) ListRules(userID string) []Rule {
	e.mu.RLock()
	defer e.mu.RUnlock()
	out := make([]Rule, 0, len(e.rules))
	for _, r := range e.rules {
		if userID != "" && r.UserID != userID {
			continue
		}
		out = append(out, *r)
	}
	sortRulesByID(out)
	return out
}

// ListRulesPage is the paginated counterpart to ListRules. Returns up
// to limit rules starting at offset, plus the total count (post-filter,
// pre-pagination) so callers can render "showing 1-25 of 312".
//
// limit < 1 is normalised to a sensible default; offset < 0 to 0.
func (e *Engine) ListRulesPage(userID string, offset, limit int) (rules []Rule, total int) {
	if limit < 1 {
		limit = 50
	}
	if offset < 0 {
		offset = 0
	}
	all := e.ListRules(userID)
	total = len(all)
	if offset >= total {
		return nil, total
	}
	end := offset + limit
	if end > total {
		end = total
	}
	return all[offset:end], total
}

func sortRulesByID(rules []Rule) {
	sort.Slice(rules, func(i, j int) bool { return rules[i].ID < rules[j].ID })
}

// AddSink registers a delivery target by id.
func (e *Engine) AddSink(id string, s Sink) {
	e.sinksMu.Lock()
	e.sinks[id] = s
	e.sinksMu.Unlock()
}

// OnSnapshot evaluates every enabled rule against the snapshot and
// delivers any triggers. Caller is expected to invoke this once per
// state.<sym>.gex message arrival.
//
// Time fallback: TsNs == 0 means the publisher didn't stamp it, in
// which case we use wall clock. Note that time.Unix(0, 0).IsZero() is
// FALSE — the epoch is 1970-01-01, not Go's zero time — so the check
// has to be on TsNs directly.
func (e *Engine) OnSnapshot(s Snapshot) {
	var now time.Time
	if s.TsNs == 0 {
		now = time.Now().UTC()
	} else {
		now = time.Unix(0, int64(s.TsNs)).UTC()
	}

	// Snapshot the rule set so we can evaluate without holding the
	// lock during sink delivery.
	e.mu.RLock()
	rules := make([]*Rule, 0, len(e.rules))
	for _, r := range e.rules {
		rules = append(rules, r)
	}
	e.mu.RUnlock()

	for _, r := range rules {
		if !r.Enabled {
			continue
		}
		if r.Symbol != s.Symbol {
			continue
		}
		evaluationsTotal.Inc()
		fired, text, refs := evaluate(r, s)
		if !fired {
			continue
		}
		// Cooldown gate.
		e.mu.Lock()
		if !r.lastFiredAt.IsZero() && now.Sub(r.lastFiredAt) < r.Cooldown {
			e.mu.Unlock()
			cooldownSuppressedTotal.WithLabelValues(string(r.Kind)).Inc()
			continue
		}
		r.lastFiredAt = now
		e.mu.Unlock()

		firedTotal.WithLabelValues(string(r.Kind)).Inc()

		t := Trigger{
			RuleID: r.ID, UserID: r.UserID,
			Symbol: strings.ToLower(s.Symbol.String()),
			Kind:   r.Kind, TsNs: s.TsNs,
			Text:   text, Refs: refs,
		}
		e.dispatch(r, t)
	}
}

func (e *Engine) dispatch(r *Rule, t Trigger) {
	e.sinksMu.RLock()
	defer e.sinksMu.RUnlock()
	deliver := func(id string, s Sink) {
		if err := s.Deliver(t); err != nil {
			deliveryErrorsTotal.WithLabelValues(id).Inc()
			return
		}
		deliveriesTotal.WithLabelValues(id).Inc()
	}
	if len(r.Sinks) == 0 {
		// Default fan-out: deliver to every registered sink.
		for id, s := range e.sinks {
			deliver(id, s)
		}
		return
	}
	for _, id := range r.Sinks {
		if s, ok := e.sinks[id]; ok {
			deliver(id, s)
		}
	}
}

// evaluate is the predicate dispatch. Returns (fired, text, refs).
func evaluate(r *Rule, s Snapshot) (bool, string, map[string]any) {
	switch r.Kind {
	case RuleDPIAbove:
		if s.DPI.Composite > r.Threshold {
			return true,
				fmt.Sprintf("DPI %.1f > %.0f on %s",
					s.DPI.Composite, r.Threshold, strings.ToUpper(s.Symbol.String())),
				map[string]any{"dpi": s.DPI.Composite, "threshold": r.Threshold}
		}
	case RuleDPIBelow:
		if s.DPI.Composite < r.Threshold {
			return true,
				fmt.Sprintf("DPI %.1f < %.0f on %s",
					s.DPI.Composite, r.Threshold, strings.ToUpper(s.Symbol.String())),
				map[string]any{"dpi": s.DPI.Composite, "threshold": r.Threshold}
		}
	case RuleNetGEXAbove:
		if s.NetGEX > r.Threshold {
			return true,
				fmt.Sprintf("Net GEX %.2g > %.2g on %s",
					s.NetGEX, r.Threshold, strings.ToUpper(s.Symbol.String())),
				map[string]any{"net_gex": s.NetGEX, "threshold": r.Threshold}
		}
	case RuleNetGEXBelow:
		if s.NetGEX < r.Threshold {
			return true,
				fmt.Sprintf("Net GEX %.2g < %.2g on %s",
					s.NetGEX, r.Threshold, strings.ToUpper(s.Symbol.String())),
				map[string]any{"net_gex": s.NetGEX, "threshold": r.Threshold}
		}
	case RuleCharmZone:
		// StringArg expected to be the numeric zone as a string ("3" =
		// PEAK) or the label. We accept both for ergonomics.
		if zoneMatches(r.StringArg, s.CharmZone) {
			return true,
				fmt.Sprintf("Charm zone %s on %s",
					strings.ToUpper(r.StringArg), strings.ToUpper(s.Symbol.String())),
				map[string]any{"zone": s.CharmZone}
		}
	case RuleRegime:
		if regimeMatches(r.StringArg, s.Regime) {
			return true,
				fmt.Sprintf("Regime %s on %s",
					strings.ToUpper(r.StringArg), strings.ToUpper(s.Symbol.String())),
				map[string]any{"regime": s.Regime}
		}
	case RulePinProb:
		if s.Pin.Active && s.Pin.TopProbability > r.Threshold {
			return true,
				fmt.Sprintf("Pin %.0f%% at %.0f on %s",
					s.Pin.TopProbability*100, s.Pin.TopStrike, strings.ToUpper(s.Symbol.String())),
				map[string]any{"prob": s.Pin.TopProbability, "strike": s.Pin.TopStrike}
		}
	}
	return false, "", nil
}

func zoneMatches(want string, zone uint8) bool {
	want = strings.ToUpper(strings.TrimSpace(want))
	switch want {
	case "WEAK", "1":
		return zone == 1
	case "RISING", "2":
		return zone == 2
	case "PEAK", "3":
		return zone == 3
	case "FADING", "4":
		return zone == 4
	case "PIN", "5":
		return zone == 5
	}
	return false
}

func regimeMatches(want string, regime uint8) bool {
	want = strings.ToUpper(strings.TrimSpace(want))
	switch want {
	case "SHORT", "SHORT_GAMMA", "1":
		return regime == 1
	case "LONG", "LONG_GAMMA", "2":
		return regime == 2
	case "NEUTRAL", "3":
		return regime == 3
	}
	return false
}
