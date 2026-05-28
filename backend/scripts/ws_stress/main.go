// WebSocket stress test for /ws/live.
//
// Spins up N concurrent clients against the api binary, subscribes
// each to spx + ndx gex streams, consumes messages for the test
// duration, and reports per-client receive stats + tail latency
// from snapshot ts_ns to local recv time.
//
// Pair with scripts/synth_state to generate load without Databento:
//
//   # window 1: NATS + api + synth state
//   docker compose -f deploy/docker-compose.yml --profile demo up -d
//
//   # window 2: stress
//   go run ./scripts/ws_stress -clients 1000 -duration 60s
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"sort"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

type clientStats struct {
	connected   bool
	connectErr  string
	recvCount   uint64
	bytesRecv   uint64
	firstRecvNs int64
	lastRecvNs  int64
	// rolling snapshot of recv timestamps for latency from ts_ns -> local recv
	latSamplesNs []int64
}

type wsMsg struct {
	Type string          `json:"type"`
	TsNs uint64          `json:"ts_ns,omitempty"`
	Data json.RawMessage `json:"data,omitempty"`
}

func main() {
	url := flag.String("url", "ws://localhost:8080/ws/live", "WebSocket URL")
	n := flag.Int("clients", 100, "concurrent client count")
	duration := flag.Duration("duration", 30*time.Second, "test duration")
	rampUp := flag.Duration("ramp-up", 5*time.Second, "spread connection establishment across this window")
	subSymbols := flag.String("symbols", "spx,ndx", "comma-separated symbols to subscribe")
	subKinds := flag.String("kinds", "gex", "comma-separated kinds to subscribe")
	connectTimeout := flag.Duration("connect-timeout", 10*time.Second, "per-client connect deadline")
	flag.Parse()

	syms := splitCSV(*subSymbols)
	kinds := splitCSV(*subKinds)

	log.Printf("ws-stress: clients=%d duration=%s url=%s", *n, *duration, *url)
	log.Printf("subscribe symbols=%v kinds=%v ramp_up=%s", syms, kinds, *rampUp)

	rootCtx, cancel := context.WithCancel(context.Background())
	defer cancel()
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	go func() { <-stop; cancel() }()

	stats := make([]clientStats, *n)
	var wg sync.WaitGroup
	wg.Add(*n)

	var connectedCount, connectErrors atomic.Int64
	startedAt := time.Now()
	deadline := startedAt.Add(*duration)

	rampStep := time.Duration(0)
	if *n > 1 {
		rampStep = *rampUp / time.Duration(*n)
	}

	for i := 0; i < *n; i++ {
		go func(idx int) {
			defer wg.Done()
			time.Sleep(rampStep * time.Duration(idx))
			runClient(rootCtx, *url, idx, syms, kinds, deadline,
				*connectTimeout, &stats[idx], &connectedCount, &connectErrors)
		}(i)
	}

	progress := time.NewTicker(5 * time.Second)
	defer progress.Stop()
	go func() {
		for {
			select {
			case <-rootCtx.Done():
				return
			case <-progress.C:
				log.Printf("progress: connected=%d errors=%d", connectedCount.Load(), connectErrors.Load())
			}
		}
	}()

	wg.Wait()
	report(stats, startedAt, time.Now())
}

func runClient(ctx context.Context, url string, idx int, syms, kinds []string,
	deadline time.Time, connectTimeout time.Duration, st *clientStats,
	connectedCount, connectErrors *atomic.Int64,
) {
	dialCtx, dialCancel := context.WithTimeout(ctx, connectTimeout)
	defer dialCancel()

	c, _, err := websocket.Dial(dialCtx, url, &websocket.DialOptions{
		HTTPHeader: http.Header{"User-Agent": {fmt.Sprintf("flowgreeks-stress/%d", idx)}},
	})
	if err != nil {
		connectErrors.Add(1)
		st.connectErr = err.Error()
		return
	}
	defer c.CloseNow()
	st.connected = true
	connectedCount.Add(1)

	if err := wsjson.Write(ctx, c, map[string]any{
		"action":  "subscribe",
		"symbols": syms,
		"kinds":   kinds,
	}); err != nil {
		st.connectErr = "subscribe write: " + err.Error()
		return
	}

	c.SetReadLimit(1024 * 1024) // 1MB per frame is plenty

	// Use a per-client deadline so we don't run past the test duration.
	clientCtx, clientCancel := context.WithDeadline(ctx, deadline)
	defer clientCancel()

	var msg wsMsg
	for {
		typ, data, err := c.Read(clientCtx)
		if err != nil {
			return
		}
		if typ != websocket.MessageText {
			continue
		}
		st.recvCount++
		st.bytesRecv += uint64(len(data))
		nowNs := time.Now().UnixNano()
		if st.firstRecvNs == 0 {
			st.firstRecvNs = nowNs
		}
		st.lastRecvNs = nowNs

		// Parse just enough to grab ts_ns for latency. Skip on parse fail.
		msg = wsMsg{}
		if err := json.Unmarshal(data, &msg); err == nil && msg.TsNs > 0 {
			lat := nowNs - int64(msg.TsNs)
			if lat > 0 && lat < int64(time.Hour) { // sanity guard
				st.latSamplesNs = append(st.latSamplesNs, lat)
			}
		}
	}
}

func splitCSV(s string) []string {
	if s == "" {
		return nil
	}
	var out []string
	for _, p := range splitFields(s, ',') {
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

func splitFields(s string, sep rune) []string {
	out := []string{}
	cur := ""
	for _, r := range s {
		if r == sep {
			out = append(out, cur)
			cur = ""
			continue
		}
		cur += string(r)
	}
	if cur != "" {
		out = append(out, cur)
	}
	return out
}

func report(stats []clientStats, start, end time.Time) {
	totalRecv, totalBytes := uint64(0), uint64(0)
	connected, failed := 0, 0
	var allLat []int64
	for i := range stats {
		s := &stats[i]
		if s.connected {
			connected++
		} else {
			failed++
		}
		totalRecv += s.recvCount
		totalBytes += s.bytesRecv
		allLat = append(allLat, s.latSamplesNs...)
	}
	dur := end.Sub(start)
	fmt.Println()
	fmt.Println("─── ws-stress report ──────────────────────────────")
	fmt.Printf("duration               %s\n", dur.Round(time.Millisecond))
	fmt.Printf("clients connected      %d / %d\n", connected, len(stats))
	fmt.Printf("clients failed         %d\n", failed)
	fmt.Printf("messages received      %d\n", totalRecv)
	if connected > 0 {
		fmt.Printf("avg msgs per client    %.1f\n", float64(totalRecv)/float64(connected))
	}
	if dur.Seconds() > 0 {
		fmt.Printf("aggregate msg/s        %.1f\n", float64(totalRecv)/dur.Seconds())
		fmt.Printf("aggregate KB/s         %.1f\n", float64(totalBytes)/1024/dur.Seconds())
	}
	if len(allLat) > 0 {
		sort.Slice(allLat, func(i, j int) bool { return allLat[i] < allLat[j] })
		fmt.Printf("latency (ts_ns→recv)\n")
		fmt.Printf("  p50                  %s\n", time.Duration(percentile(allLat, 50)).Round(time.Microsecond))
		fmt.Printf("  p95                  %s\n", time.Duration(percentile(allLat, 95)).Round(time.Microsecond))
		fmt.Printf("  p99                  %s\n", time.Duration(percentile(allLat, 99)).Round(time.Microsecond))
		fmt.Printf("  max                  %s\n", time.Duration(allLat[len(allLat)-1]).Round(time.Microsecond))
	}

	// Sample of connect errors so the operator can spot the failure mode.
	errCounts := map[string]int{}
	for _, s := range stats {
		if !s.connected && s.connectErr != "" {
			errCounts[s.connectErr]++
		}
	}
	if len(errCounts) > 0 {
		fmt.Printf("connect errors (top 5):\n")
		type kv struct {
			k string
			v int
		}
		ranked := make([]kv, 0, len(errCounts))
		for k, v := range errCounts {
			ranked = append(ranked, kv{k, v})
		}
		sort.Slice(ranked, func(i, j int) bool { return ranked[i].v > ranked[j].v })
		for i, r := range ranked {
			if i >= 5 {
				break
			}
			fmt.Printf("  %4d × %s\n", r.v, r.k)
		}
	}

	if failed > 0 || (connected > 0 && totalRecv == 0) {
		os.Exit(1)
	}
}

// percentile returns sorted[i] where i is round(p/100 * (n-1)).
func percentile(sorted []int64, p int) int64 {
	if len(sorted) == 0 {
		return 0
	}
	idx := (p * (len(sorted) - 1)) / 100
	return sorted[idx]
}
