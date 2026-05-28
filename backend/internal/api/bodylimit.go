package api

import (
	"net/http"
)

// MaxRequestBodyBytes is the global cap on inbound HTTP request body
// size for non-WS routes. The auth handlers already wrap their reads
// in io.LimitReader at 4KiB and the alerts upsert at 16KiB, but a
// caller hitting a route without an explicit limit still has the cap
// enforced at the connection layer.
//
// 1 MiB is generous for the JSON payloads any current handler accepts
// (largest is alerts.Rule, well under 16KiB) but small enough that a
// hostile uploader can't pin server memory.
const MaxRequestBodyBytes = 1 << 20 // 1 MiB

// BodyLimit caps r.Body to MaxRequestBodyBytes. Reads beyond the limit
// return *http.MaxBytesError, which the request handler can surface
// as 413 RequestEntityTooLarge when it occurs during decode.
//
// Applied as a global chi middleware so even routes that forget to
// LimitReader inherit the cap. WebSocket upgrades use chunked frame
// reads via coder/websocket and aren't affected by this — they rely
// on Conn.SetReadLimit instead (see ws.go for /ws/live + /ws/replay).
func BodyLimit(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Body != nil {
			r.Body = http.MaxBytesReader(w, r.Body, MaxRequestBodyBytes)
		}
		next.ServeHTTP(w, r)
	})
}
