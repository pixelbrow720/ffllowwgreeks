// End-to-end smoke test for the demo stack.
//
// Pre-condition: `make demo-up` is running. This binary then walks
// every public surface of the api binary and prints PASS/FAIL per
// check. Exits non-zero if any check fails so it can wedge into CI.
//
// Checks:
//   - GET /health           returns ok
//   - GET /health/ready     returns ready (200) with both deps OK
//   - GET /metrics          serves prometheus exposition
//   - GET /api/snapshot/spx returns a non-empty JSON object
//   - GET /api/snapshot/ndx returns a non-empty JSON object
//   - GET /api/levels/spx   returns spot > 0
//   - POST /api/simulate/spx with a small spot move returns forced_notional ≠ 0
//   - WS  /ws/live          subscribes, receives ≥1 snapshot in 5s
//
// Usage:
//   go run ./scripts/smoke/e2e -url http://localhost:8080
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

type result struct {
	name string
	ok   bool
	note string
}

func main() {
	base := flag.String("url", "http://localhost:8080", "api base URL")
	wsTimeout := flag.Duration("ws-timeout", 5*time.Second, "max wait for first WS snapshot")
	flag.Parse()

	results := []result{
		check("GET /health", func() string {
			body, status, err := getJSON(*base + "/health")
			if err != nil || status != 200 {
				return fmt.Sprintf("status=%d err=%v", status, err)
			}
			if !strings.Contains(string(body), `"status":"ok"`) {
				return fmt.Sprintf("unexpected body: %s", body)
			}
			return ""
		}),
		check("GET /health/ready", func() string {
			body, status, err := getJSON(*base + "/health/ready")
			if err != nil || status != 200 {
				return fmt.Sprintf("status=%d err=%v body=%s", status, err, string(body))
			}
			var r struct {
				Status string                       `json:"status"`
				Deps   map[string]map[string]any    `json:"deps"`
			}
			if err := json.Unmarshal(body, &r); err != nil {
				return "decode: " + err.Error()
			}
			if r.Status != "ready" {
				return "status=" + r.Status
			}
			for k, v := range r.Deps {
				if ok, _ := v["ok"].(bool); !ok {
					return fmt.Sprintf("dep %s not ok: %v", k, v)
				}
			}
			return ""
		}),
		check("GET /metrics", func() string {
			body, status, err := getRaw(*base + "/metrics")
			if err != nil || status != 200 {
				return fmt.Sprintf("status=%d err=%v", status, err)
			}
			if !strings.Contains(string(body), "go_goroutines") {
				return "no go_goroutines line — promhttp not mounted?"
			}
			return ""
		}),
		check("GET /api/snapshot/spx", checkSnapshot(*base, "spx")),
		check("GET /api/snapshot/ndx", checkSnapshot(*base, "ndx")),
		check("GET /api/levels/spx", func() string {
			body, status, err := getJSON(*base + "/api/levels/spx")
			if err != nil || status != 200 {
				return fmt.Sprintf("status=%d err=%v", status, err)
			}
			var r struct {
				Spot float64 `json:"spot"`
			}
			if err := json.Unmarshal(body, &r); err != nil {
				return "decode: " + err.Error()
			}
			if r.Spot <= 0 {
				return fmt.Sprintf("spot=%.2f", r.Spot)
			}
			return ""
		}),
		check("POST /api/simulate/spx", func() string {
			body := []byte(`{"spot_pct_change":0.005,"duration_minutes":15,"vol_pt_change":0}`)
			resp, err := http.Post(*base+"/api/simulate/spx", "application/json", bytes.NewReader(body))
			if err != nil {
				return err.Error()
			}
			defer resp.Body.Close()
			b, _ := io.ReadAll(resp.Body)
			if resp.StatusCode != 200 {
				return fmt.Sprintf("status=%d body=%s", resp.StatusCode, b)
			}
			var r struct {
				ForcedNotional float64 `json:"forced_notional"`
			}
			if err := json.Unmarshal(b, &r); err != nil {
				return "decode: " + err.Error()
			}
			// Synthetic state has dealer positions on every strike, so a
			// non-trivial spot move must produce non-zero forced flow.
			if r.ForcedNotional == 0 {
				return "forced_notional is zero — simulator did not see strike matrix"
			}
			return ""
		}),
		check("WS /ws/live", func() string {
			return checkWSLive(*base, *wsTimeout)
		}),
	}

	pass, fail := 0, 0
	for _, r := range results {
		mark := "PASS"
		if !r.ok {
			mark = "FAIL"
			fail++
		} else {
			pass++
		}
		if r.note != "" {
			fmt.Printf("%-4s  %-30s  %s\n", mark, r.name, r.note)
		} else {
			fmt.Printf("%-4s  %s\n", mark, r.name)
		}
	}
	fmt.Println()
	fmt.Printf("%d passed, %d failed\n", pass, fail)
	if fail > 0 {
		os.Exit(1)
	}
}

func check(name string, fn func() string) result {
	note := fn()
	return result{name: name, ok: note == "", note: note}
}

func getJSON(target string) ([]byte, int, error) {
	resp, err := http.Get(target)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	return body, resp.StatusCode, nil
}

func getRaw(target string) ([]byte, int, error) { return getJSON(target) }

func checkSnapshot(base, sym string) func() string {
	return func() string {
		body, status, err := getJSON(base + "/api/snapshot/" + sym)
		if err != nil || status != 200 {
			return fmt.Sprintf("status=%d err=%v", status, err)
		}
		var r map[string]any
		if err := json.Unmarshal(body, &r); err != nil {
			return "decode: " + err.Error()
		}
		if len(r) == 0 {
			return "empty body"
		}
		if spot, _ := r["spot"].(float64); spot <= 0 {
			return fmt.Sprintf("spot=%.2f", spot)
		}
		return ""
	}
}

func checkWSLive(base string, timeout time.Duration) string {
	u, err := url.Parse(base)
	if err != nil {
		return "parse url: " + err.Error()
	}
	switch u.Scheme {
	case "http":
		u.Scheme = "ws"
	case "https":
		u.Scheme = "wss"
	}
	u.Path = "/ws/live"

	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	c, _, err := websocket.Dial(ctx, u.String(), nil)
	if err != nil {
		return "dial: " + err.Error()
	}
	defer c.CloseNow()
	if err := wsjson.Write(ctx, c, map[string]any{
		"action":  "subscribe",
		"symbols": []string{"spx", "ndx"},
		"kinds":   []string{"gex"},
	}); err != nil {
		return "subscribe: " + err.Error()
	}
	c.SetReadLimit(1024 * 1024)
	for {
		typ, data, err := c.Read(ctx)
		if err != nil {
			return "read: " + err.Error()
		}
		if typ != websocket.MessageText {
			continue
		}
		var ev struct {
			Type string `json:"type"`
		}
		if json.Unmarshal(data, &ev) == nil && ev.Type == "snapshot" {
			return ""
		}
	}
}
