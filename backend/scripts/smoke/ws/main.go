// One-shot WebSocket client for M4 smoke test.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/coder/websocket"
)

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()

	url := os.Getenv("WS_URL")
	if url == "" {
		url = "ws://localhost:8089/ws/live"
	}

	c, _, err := websocket.Dial(ctx, url, nil)
	if err != nil {
		fmt.Fprintln(os.Stderr, "dial:", err)
		os.Exit(1)
	}
	defer c.CloseNow()
	fmt.Println("connected:", url)

	// Subscribe to SPX gex.
	sub := []byte(`{"action":"subscribe","symbols":["spx"],"kinds":["gex"]}`)
	if err := c.Write(ctx, websocket.MessageText, sub); err != nil {
		fmt.Fprintln(os.Stderr, "write:", err)
		os.Exit(1)
	}

	for i := 0; i < 5; i++ {
		_, data, err := c.Read(ctx)
		if err != nil {
			fmt.Fprintln(os.Stderr, "read:", err)
			os.Exit(1)
		}
		var ev map[string]any
		_ = json.Unmarshal(data, &ev)
		t, _ := ev["type"].(string)
		fmt.Printf("[%d] type=%s symbol=%v kind=%v\n", i, t, ev["symbol"], ev["kind"])
		if t == "snapshot" {
			fmt.Println("  ✓ received snapshot — WS path works")
			return
		}
	}
	fmt.Println("no snapshot received in 5 messages")
	os.Exit(2)
}
