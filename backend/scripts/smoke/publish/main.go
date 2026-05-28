// One-shot publisher for M4 smoke test. Publishes a synthetic gex
// snapshot to state.spx.gex so the api binary's NATS subscriber can
// pick it up and serve it via REST/WS.
package main

import (
	"fmt"
	"os"

	"github.com/nats-io/nats.go"
)

func main() {
	url := os.Getenv("NATS_URL")
	if url == "" {
		url = nats.DefaultURL
	}
	nc, err := nats.Connect(url)
	if err != nil {
		fmt.Fprintln(os.Stderr, "connect:", err)
		os.Exit(1)
	}
	defer nc.Close()

	payload := `{"ts_ns":1764111000000000000,"symbol":1,"spot":5810.5,"net_gex":-2800000000,"zero_gamma":5805,"call_wall":5840,"put_wall":5775,"expected_mv":0.42,"regime":1,"dpi":{"composite":78,"net_gamma_sign":88,"charm_velocity":72,"vanna_sensitivity":58,"time_to_close_decay":81,"flow_concentration":64},"flow_pulse":{"gamma":0.4,"charm":-1.7,"vanna":0.9,"total":-0.4},"charm_zone":3,"strikes":[]}`
	if err := nc.Publish("state.spx.gex", []byte(payload)); err != nil {
		fmt.Fprintln(os.Stderr, "publish:", err)
		os.Exit(1)
	}
	if err := nc.Flush(); err != nil {
		fmt.Fprintln(os.Stderr, "flush:", err)
		os.Exit(1)
	}
	fmt.Println("published state.spx.gex")
}
