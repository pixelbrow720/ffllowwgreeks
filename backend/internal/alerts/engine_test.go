package alerts

import (
	"encoding/json"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"flowgreeks/internal/feed"
)

type captureSink struct {
	mu  sync.Mutex
	got []Trigger
}

func (c *captureSink) Deliver(t Trigger) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.got = append(c.got, t)
	return nil
}

func (c *captureSink) count() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.got)
}

func mkSnapshot(sym feed.Symbol, dpi float64, regime, zone uint8, netGEX float64) Snapshot {
	s := Snapshot{
		Symbol: sym,
		TsNs:   uint64(time.Now().UnixNano()),
		NetGEX: netGEX,
		Regime: regime,
		CharmZone: zone,
	}
	s.DPI.Composite = dpi
	return s
}

func TestEngine_DPIAbove(t *testing.T) {
	e := NewEngine()
	sink := &captureSink{}
	e.AddSink("default", sink)
	e.AddRule(Rule{
		ID: "r1", UserID: "u1", Symbol: feed.SymbolSPX,
		Kind: RuleDPIAbove, Threshold: 80, Enabled: true,
		Cooldown: 1 * time.Millisecond,
	})
	e.OnSnapshot(mkSnapshot(feed.SymbolSPX, 70, 1, 3, 0)) // below
	if sink.count() != 0 {
		t.Errorf("DPI 70 should not fire >80 rule")
	}
	e.OnSnapshot(mkSnapshot(feed.SymbolSPX, 85, 1, 3, 0)) // above
	if sink.count() != 1 {
		t.Errorf("DPI 85 should fire >80 rule, got %d triggers", sink.count())
	}
}

func TestEngine_Cooldown(t *testing.T) {
	e := NewEngine()
	sink := &captureSink{}
	e.AddSink("default", sink)
	e.AddRule(Rule{
		ID: "r1", UserID: "u1", Symbol: feed.SymbolSPX,
		Kind: RuleDPIAbove, Threshold: 80, Enabled: true,
		Cooldown: 100 * time.Millisecond,
	})
	for i := 0; i < 5; i++ {
		e.OnSnapshot(mkSnapshot(feed.SymbolSPX, 90, 1, 3, 0))
	}
	if sink.count() != 1 {
		t.Errorf("cooldown should gate to 1 fire, got %d", sink.count())
	}
}

func TestEngine_DisabledIgnored(t *testing.T) {
	e := NewEngine()
	sink := &captureSink{}
	e.AddSink("default", sink)
	e.AddRule(Rule{
		ID: "r1", Symbol: feed.SymbolSPX, Kind: RuleDPIAbove,
		Threshold: 50, Enabled: false, Cooldown: time.Second,
	})
	e.OnSnapshot(mkSnapshot(feed.SymbolSPX, 99, 1, 3, 0))
	if sink.count() != 0 {
		t.Error("disabled rule should not fire")
	}
}

func TestEngine_SymbolFilter(t *testing.T) {
	e := NewEngine()
	sink := &captureSink{}
	e.AddSink("default", sink)
	e.AddRule(Rule{
		ID: "spx", Symbol: feed.SymbolSPX, Kind: RuleDPIAbove,
		Threshold: 50, Enabled: true, Cooldown: time.Millisecond,
	})
	e.OnSnapshot(mkSnapshot(feed.SymbolNDX, 99, 1, 3, 0))
	if sink.count() != 0 {
		t.Error("rule for SPX must not fire on NDX snapshot")
	}
}

func TestEngine_RegimeAndZone(t *testing.T) {
	e := NewEngine()
	sink := &captureSink{}
	e.AddSink("default", sink)
	e.AddRule(Rule{
		ID: "rZone", Symbol: feed.SymbolSPX, Kind: RuleCharmZone,
		StringArg: "PEAK", Enabled: true, Cooldown: time.Millisecond,
	})
	e.AddRule(Rule{
		ID: "rRegime", Symbol: feed.SymbolSPX, Kind: RuleRegime,
		StringArg: "SHORT_GAMMA", Enabled: true, Cooldown: time.Millisecond,
	})
	e.OnSnapshot(mkSnapshot(feed.SymbolSPX, 50, 1, 3, 0))
	if sink.count() != 2 {
		t.Errorf("expected zone+regime to both fire, got %d", sink.count())
	}
}

func TestEngine_PinProb(t *testing.T) {
	e := NewEngine()
	sink := &captureSink{}
	e.AddSink("default", sink)
	e.AddRule(Rule{
		ID: "rPin", Symbol: feed.SymbolSPX, Kind: RulePinProb,
		Threshold: 0.40, Enabled: true, Cooldown: time.Millisecond,
	})
	s := mkSnapshot(feed.SymbolSPX, 0, 0, 0, 0)
	s.Pin.Active = true
	s.Pin.TopProbability = 0.55
	s.Pin.TopStrike = 5825
	e.OnSnapshot(s)
	if sink.count() != 1 {
		t.Errorf("expected pin rule to fire, got %d", sink.count())
	}
}

func TestEngine_AddRemoveListRules(t *testing.T) {
	e := NewEngine()
	e.AddRule(Rule{ID: "a", UserID: "u1", Enabled: true})
	e.AddRule(Rule{ID: "b", UserID: "u2", Enabled: true})
	if got := e.ListRules(""); len(got) != 2 {
		t.Errorf("ListRules(all) should return 2, got %d", len(got))
	}
	if got := e.ListRules("u1"); len(got) != 1 {
		t.Errorf("ListRules(u1) should return 1, got %d", len(got))
	}
	if !e.RemoveRule("a") {
		t.Error("RemoveRule(a) should return true")
	}
	if e.RemoveRule("a") {
		t.Error("RemoveRule(a) second time should return false")
	}
}

func TestEngine_UpsertRuleForOwner_RejectsHijack(t *testing.T) {
	e := NewEngine()
	if err := e.UpsertRuleForOwner(Rule{ID: "r1", Symbol: feed.SymbolSPX, Enabled: true}, "u1"); err != nil {
		t.Fatalf("u1 initial upsert: %v", err)
	}
	// u2 attempts to overwrite u1's rule with the same id.
	err := e.UpsertRuleForOwner(Rule{ID: "r1", Symbol: feed.SymbolSPX, Enabled: true}, "u2")
	if err != ErrRuleNotOwned {
		t.Fatalf("hijack attempt err = %v, want ErrRuleNotOwned", err)
	}
	got := e.ListRules("u1")
	if len(got) != 1 || got[0].UserID != "u1" {
		t.Errorf("u1's rule should still be owned by u1, got %+v", got)
	}
	if len(e.ListRules("u2")) != 0 {
		t.Error("u2 should have no rules")
	}
}

func TestEngine_UpsertRuleForOwner_OwnerCanReplaceOwn(t *testing.T) {
	e := NewEngine()
	if err := e.UpsertRuleForOwner(Rule{ID: "r1", Symbol: feed.SymbolSPX, Threshold: 50, Enabled: true}, "u1"); err != nil {
		t.Fatal(err)
	}
	if err := e.UpsertRuleForOwner(Rule{ID: "r1", Symbol: feed.SymbolSPX, Threshold: 80, Enabled: true}, "u1"); err != nil {
		t.Fatalf("u1 replacing own rule: %v", err)
	}
	got := e.ListRules("u1")
	if len(got) != 1 || got[0].Threshold != 80 {
		t.Errorf("expected threshold updated to 80, got %+v", got)
	}
}

func TestEngine_UpsertRuleForOwner_RejectsEmptyOwner(t *testing.T) {
	e := NewEngine()
	if err := e.UpsertRuleForOwner(Rule{ID: "r1", Enabled: true}, ""); err != ErrRuleNotOwned {
		t.Errorf("empty owner err = %v, want ErrRuleNotOwned", err)
	}
}

func TestEngine_RemoveRuleForOwner_OnlyOwnerCanDelete(t *testing.T) {
	e := NewEngine()
	if err := e.UpsertRuleForOwner(Rule{ID: "r1", Symbol: feed.SymbolSPX, Enabled: true}, "u1"); err != nil {
		t.Fatal(err)
	}
	// u2 tries to delete u1's rule.
	if e.RemoveRuleForOwner("r1", "u2") {
		t.Error("u2 should not be able to delete u1's rule")
	}
	if got := e.ListRules("u1"); len(got) != 1 {
		t.Errorf("rule survived hostile delete, got %d rules", len(got))
	}
	// u1 deletes own rule.
	if !e.RemoveRuleForOwner("r1", "u1") {
		t.Error("u1 should be able to delete own rule")
	}
	// Idempotent / non-existent.
	if e.RemoveRuleForOwner("r1", "u1") {
		t.Error("second delete should return false")
	}
	if e.RemoveRuleForOwner("nonexistent", "u1") {
		t.Error("delete on missing id should return false")
	}
}

func TestEngine_RemoveRuleForOwner_RejectsEmptyOwner(t *testing.T) {
	e := NewEngine()
	e.AddRule(Rule{ID: "r1", UserID: "u1", Enabled: true})
	if e.RemoveRuleForOwner("r1", "") {
		t.Error("empty owner should not delete anything")
	}
}

func TestFanoutSink_FilterAndDrop(t *testing.T) {
	f := NewFanoutSink()
	chU1, _ := f.Subscribe(2, "u1")
	chAll, _ := f.Subscribe(2, "")

	_ = f.Deliver(Trigger{UserID: "u1"})
	_ = f.Deliver(Trigger{UserID: "u2"})

	got1 := drain(chU1, 50*time.Millisecond)
	gotAll := drain(chAll, 50*time.Millisecond)
	if len(got1) != 1 || got1[0].UserID != "u1" {
		t.Errorf("u1 sub: got %v, want 1×u1", got1)
	}
	if len(gotAll) != 2 {
		t.Errorf("all sub: got %v, want 2", gotAll)
	}

	// Saturate to test drop.
	chDrop, _ := f.Subscribe(1, "")
	for i := 0; i < 5; i++ {
		_ = f.Deliver(Trigger{UserID: "u3"})
	}
	got := drain(chDrop, 50*time.Millisecond)
	if len(got) > 1 {
		t.Errorf("buf=1 sub should drop all but 1, got %d", len(got))
	}
}

func TestSnapshotDecode(t *testing.T) {
	raw := []byte(`{
		"ts_ns": 42, "net_gex": -2.8e9, "regime": 1, "charm_zone": 3,
		"dpi": {"composite": 78},
		"pin": {"active": true, "top_probability": 0.45, "top_strike": 5825}
	}`)
	s, err := DecodeSnapshot(feed.SymbolSPX, raw)
	if err != nil {
		t.Fatal(err)
	}
	if s.Symbol != feed.SymbolSPX || s.DPI.Composite != 78 || !s.Pin.Active {
		t.Errorf("decode mismatch: %+v", s)
	}
	// Round-trip through JSON to confirm fields stay accessible.
	b, _ := json.Marshal(s)
	if len(b) == 0 {
		t.Error("re-marshal returned empty")
	}
}

func drain(ch <-chan Trigger, dur time.Duration) []Trigger {
	out := []Trigger{}
	deadline := time.NewTimer(dur)
	defer deadline.Stop()
	for {
		select {
		case t, ok := <-ch:
			if !ok {
				return out
			}
			out = append(out, t)
		case <-deadline.C:
			return out
		}
	}
}

// guard against accidental import drift
var _ atomic.Bool
