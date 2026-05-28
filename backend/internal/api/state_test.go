package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"flowgreeks/internal/feed"

	"github.com/go-chi/chi/v5"
)

func TestCacheRoundTrip(t *testing.T) {
	c := NewCache()
	if _, ok := c.Get(feed.SymbolSPX, StateKindGEX); ok {
		t.Fatal("empty cache should miss")
	}
	snap := Snapshot{
		Symbol: feed.SymbolSPX,
		Kind:   StateKindGEX,
		Data:   json.RawMessage(`{"spot":5810}`),
		TsNs:   42,
	}
	c.Update(snap)
	got, ok := c.Get(feed.SymbolSPX, StateKindGEX)
	if !ok {
		t.Fatal("get after update should hit")
	}
	if got.TsNs != 42 || string(got.Data) != `{"spot":5810}` {
		t.Errorf("round-trip mismatch: %+v", got)
	}
	if _, ok := c.Get(feed.SymbolNDX, StateKindGEX); ok {
		t.Error("different symbol should not match")
	}
}

func TestParseStateSubject(t *testing.T) {
	cases := []struct {
		subj   string
		sym    feed.Symbol
		kind   StateKind
		ok     bool
	}{
		{"state.spx.gex", feed.SymbolSPX, StateKindGEX, true},
		{"state.ndx.gex", feed.SymbolNDX, StateKindGEX, true},
		{"state.SPX.gex", feed.SymbolSPX, StateKindGEX, true},
		{"ticks.spx.>", 0, "", false},
		{"state.foo.gex", 0, "", false},
		{"state.gex", 0, "", false},
	}
	for _, c := range cases {
		sym, kind, ok := parseStateSubject(c.subj)
		if ok != c.ok || sym != c.sym || kind != c.kind {
			t.Errorf("parseStateSubject(%q) = (%v,%v,%v), want (%v,%v,%v)",
				c.subj, sym, kind, ok, c.sym, c.kind, c.ok)
		}
	}
}

func TestBrokerFanout(t *testing.T) {
	b := NewBroker()
	subA := b.Subscribe(8, SubFilter{})
	subB := b.Subscribe(8, SubFilter{})
	defer b.Unsubscribe(subA)
	defer b.Unsubscribe(subB)

	snap := Snapshot{Symbol: feed.SymbolSPX, Kind: StateKindGEX, TsNs: 100}
	b.Publish(snap)

	if got := receive(t, subA); got.TsNs != 100 {
		t.Errorf("subA missed snap: got %+v", got)
	}
	if got := receive(t, subB); got.TsNs != 100 {
		t.Errorf("subB missed snap: got %+v", got)
	}
}

func TestBrokerFilterBySymbol(t *testing.T) {
	b := NewBroker()
	sub := b.Subscribe(8, SubFilter{
		Symbols: map[feed.Symbol]struct{}{feed.SymbolSPX: {}},
	})
	defer b.Unsubscribe(sub)

	b.Publish(Snapshot{Symbol: feed.SymbolNDX, Kind: StateKindGEX, TsNs: 1})
	b.Publish(Snapshot{Symbol: feed.SymbolSPX, Kind: StateKindGEX, TsNs: 2})

	got := receive(t, sub)
	if got.TsNs != 2 {
		t.Errorf("expected ndx filtered out, got TsNs=%d", got.TsNs)
	}
	select {
	case extra := <-sub.Events():
		t.Errorf("expected only one event, also got TsNs=%d", extra.TsNs)
	case <-time.After(50 * time.Millisecond):
	}
}

func TestBrokerDropsOnFull(t *testing.T) {
	b := NewBroker()
	sub := b.Subscribe(2, SubFilter{})
	defer b.Unsubscribe(sub)

	for i := 0; i < 10; i++ {
		b.Publish(Snapshot{Symbol: feed.SymbolSPX, TsNs: uint64(i)})
	}
	if sub.Dropped() == 0 {
		t.Error("expected non-zero dropped count under saturation")
	}
}

func TestBrokerMultiSubscriberConcurrency(t *testing.T) {
	b := NewBroker()
	const subs = 20
	const events = 100
	all := make([]*Subscriber, subs)
	for i := range all {
		all[i] = b.Subscribe(events*2, SubFilter{})
	}
	defer func() {
		for _, s := range all {
			b.Unsubscribe(s)
		}
	}()

	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		for i := 0; i < events; i++ {
			b.Publish(Snapshot{TsNs: uint64(i)})
		}
	}()
	wg.Wait()

	for _, s := range all {
		count := 0
		drain := time.NewTimer(50 * time.Millisecond)
	inner:
		for {
			select {
			case <-s.Events():
				count++
			case <-drain.C:
				break inner
			}
		}
		if count != events {
			t.Errorf("subscriber got %d events, want %d", count, events)
		}
	}
}

func TestRESTSnapshotMissing(t *testing.T) {
	h := &Handlers{Cache: NewCache(), Broker: NewBroker()}
	r := chi.NewRouter()
	h.Mount(r)
	srv := httptest.NewServer(r)
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/api/snapshot/spx")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("missing snapshot should 404, got %d", resp.StatusCode)
	}
}

func TestRESTSnapshotBadSymbol(t *testing.T) {
	h := &Handlers{Cache: NewCache(), Broker: NewBroker()}
	r := chi.NewRouter()
	h.Mount(r)
	srv := httptest.NewServer(r)
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/api/snapshot/aapl")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("bad symbol should 400, got %d", resp.StatusCode)
	}
}

func TestRESTSnapshotOK(t *testing.T) {
	c := NewCache()
	c.Update(Snapshot{
		Symbol: feed.SymbolSPX,
		Kind:   StateKindGEX,
		Data:   json.RawMessage(`{"spot":5810,"net_gex":-2.8e9}`),
		TsNs:   42,
	})
	h := &Handlers{Cache: c, Broker: NewBroker()}
	r := chi.NewRouter()
	h.Mount(r)
	srv := httptest.NewServer(r)
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/api/snapshot/spx")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("expected 200, got %d", resp.StatusCode)
	}
	var got map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&got); err != nil {
		t.Fatal(err)
	}
	if got["spot"].(float64) != 5810 {
		t.Errorf("snapshot spot mismatch: %v", got["spot"])
	}
}

func TestRESTLevelsExtractsKeyFields(t *testing.T) {
	c := NewCache()
	c.Update(Snapshot{
		Symbol: feed.SymbolSPX,
		Kind:   StateKindGEX,
		Data: json.RawMessage(`{
			"spot":5810,"zero_gamma":5805,"call_wall":5840,
			"put_wall":5775,"expected_mv":0.42,"net_gex":-2.8e9,
			"regime":1,"ts_ns":42
		}`),
	})
	h := &Handlers{Cache: c}
	r := chi.NewRouter()
	h.Mount(r)
	srv := httptest.NewServer(r)
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/api/levels/spx")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var got map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&got); err != nil {
		t.Fatal(err)
	}
	if got["call_wall"].(float64) != 5840 {
		t.Errorf("call_wall: %v", got["call_wall"])
	}
	if got["put_wall"].(float64) != 5775 {
		t.Errorf("put_wall: %v", got["put_wall"])
	}
}

func receive(t *testing.T, sub *Subscriber) Snapshot {
	t.Helper()
	select {
	case s := <-sub.Events():
		return s
	case <-time.After(200 * time.Millisecond):
		t.Fatal("timeout waiting for event")
	}
	return Snapshot{}
}
