package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"flowgreeks/internal/feed"

	"github.com/go-chi/chi/v5"
)

func TestSimulateMissingSnapshot(t *testing.T) {
	h := &Handlers{Cache: NewCache(), Broker: NewBroker()}
	r := chi.NewRouter()
	h.Mount(r)
	srv := httptest.NewServer(r)
	defer srv.Close()

	resp, err := http.Post(srv.URL+"/api/simulate/spx", "application/json", bytes.NewReader([]byte(`{}`)))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("expected 404 with no cached snapshot, got %d", resp.StatusCode)
	}
}

func TestSimulateBadSymbol(t *testing.T) {
	h := &Handlers{Cache: NewCache(), Broker: NewBroker()}
	r := chi.NewRouter()
	h.Mount(r)
	srv := httptest.NewServer(r)
	defer srv.Close()

	resp, err := http.Post(srv.URL+"/api/simulate/aapl", "application/json", bytes.NewReader([]byte(`{}`)))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("expected 400 for unknown symbol, got %d", resp.StatusCode)
	}
}

func TestSimulateOK(t *testing.T) {
	cache := NewCache()
	// Synthetic snapshot mirroring what cmd/compute publishes. Two
	// strikes (call ATM, put ATM) with non-zero dealer position +
	// Greeks so the simulator returns a non-zero result.
	cache.Update(Snapshot{
		Symbol: feed.SymbolSPX,
		Kind:   StateKindGEX,
		Data: json.RawMessage(`{
			"spot": 5810,
			"strikes": [
				{"expiry": 20990101, "strike": 5810000, "side": 1, "dealer_pos": -1000, "iv": 0.18, "gamma": 0.001, "charm": 0.0, "vanna": 0.0, "gex_notional": -1.0e8},
				{"expiry": 20990101, "strike": 5810000, "side": 2, "dealer_pos": -500, "iv": 0.18, "gamma": 0.001, "charm": 0.0, "vanna": 0.0, "gex_notional": -5.0e7}
			]
		}`),
	})
	h := &Handlers{Cache: cache, Broker: NewBroker()}
	r := chi.NewRouter()
	h.Mount(r)
	srv := httptest.NewServer(r)
	defer srv.Close()

	body := []byte(`{"spot_pct_change": 0.005, "duration_minutes": 30, "vol_pt_change": 0}`)
	resp, err := http.Post(srv.URL+"/api/simulate/spx", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("expected 200, got %d", resp.StatusCode)
	}

	var got SimulateResponse
	if err := json.NewDecoder(resp.Body).Decode(&got); err != nil {
		t.Fatal(err)
	}
	if got.Symbol != "spx" {
		t.Errorf("symbol mismatch: %v", got.Symbol)
	}
	if got.Spot != 5810 {
		t.Errorf("spot mismatch: %v", got.Spot)
	}
	if got.NewSpot <= 5810 {
		t.Errorf("NewSpot should be > 5810 after +0.5%% move, got %v", got.NewSpot)
	}
}
