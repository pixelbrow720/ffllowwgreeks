package apikey

import (
	"hash/fnv"
	"net"
	"net/http"
	"sync"
	"time"
)

// rateLimiterShards is the number of independent token-bucket maps.
// Each shard has its own mutex, so M parallel callers contend only when
// their keys hash to the same shard. Sized to be greater than typical
// API server CPU count so contention is rare even under burst.
const rateLimiterShards = 32

// RateLimiter is a per-key token-bucket limiter. Each APIKey carries
// its own RateLimitRPS + RateBurst from the api_keys row, so the
// parent site can provision different tiers (e.g. quant tier gets
// 10 rps / 60 burst; recon tier gets 1 rps / 30 burst).
//
// Keys are looked up by APIKey.ID (int64) for O(1) state — no need
// to hash the secret again here. Anonymous fall-through (no Claims in
// context) keys by IP so unauthenticated traffic is still capped.
//
// Memory: buckets are evicted via a janitor goroutine so a churning
// key population doesn't grow the map without bound.
//
// Concurrency: sharded across rateLimiterShards independent maps so
// one hot tenant's bucket lookup doesn't serialize every other
// authenticated request through a global mutex.
type RateLimiter struct {
	ttl time.Duration

	shards [rateLimiterShards]rlShard

	stop chan struct{}
}

type rlShard struct {
	mu      sync.Mutex
	buckets map[string]*bucket
}

type bucket struct {
	tokens   float64
	rate     float64
	burst    float64
	updated  time.Time
	lastSeen time.Time
}

// NewRateLimiter constructs a limiter with a 1h inactivity TTL.
func NewRateLimiter() *RateLimiter {
	rl := &RateLimiter{
		ttl:  time.Hour,
		stop: make(chan struct{}),
	}
	for i := range rl.shards {
		rl.shards[i].buckets = make(map[string]*bucket, 64)
	}
	go rl.janitor()
	return rl
}

// shardFor picks an independent bucket map for key. FNV-1a is cheap
// and well-distributed; we don't need crypto strength here.
func (rl *RateLimiter) shardFor(key string) *rlShard {
	h := fnv.New32a()
	_, _ = h.Write([]byte(key))
	return &rl.shards[h.Sum32()%rateLimiterShards]
}

// Close stops the janitor goroutine. Safe to call multiple times.
func (rl *RateLimiter) Close() {
	select {
	case <-rl.stop:
	default:
		close(rl.stop)
	}
}

// Allow returns (true, 0) if the request is within budget for the
// given key, otherwise (false, retryAfter). rate / burst come from the
// APIKey row so different tiers use different budgets.
func (rl *RateLimiter) Allow(key string, rate, burst float64) (bool, time.Duration) {
	now := time.Now()
	s := rl.shardFor(key)
	s.mu.Lock()
	defer s.mu.Unlock()
	b, ok := s.buckets[key]
	if !ok {
		b = &bucket{tokens: burst, rate: rate, burst: burst, updated: now}
		s.buckets[key] = b
	} else {
		// Per-key tier may have changed since the bucket was minted —
		// honour the current rate/burst on every call.
		b.rate, b.burst = rate, burst
	}
	elapsed := now.Sub(b.updated).Seconds()
	if elapsed > 0 {
		b.tokens += elapsed * b.rate
		if b.tokens > b.burst {
			b.tokens = b.burst
		}
		b.updated = now
	}
	b.lastSeen = now
	if b.tokens >= 1 {
		b.tokens--
		return true, 0
	}
	deficit := 1 - b.tokens
	if b.rate <= 0 {
		return false, time.Hour // misconfigured; back off hard
	}
	retry := time.Duration(deficit/b.rate*float64(time.Second)) + 100*time.Millisecond
	return false, retry
}

// Middleware wraps next with the limiter. Consumes the resolved APIKey
// from context (installed by Middleware.Handler) — without that, falls
// back to per-IP keying so anonymous traffic is still throttled.
//
// Returns 429 with Retry-After when the bucket is empty.
func (rl *RateLimiter) Middleware(audit AuditSink) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			key, rate, burst := bucketParams(r)
			ok, retry := rl.Allow(key, rate, burst)
			if !ok {
				w.Header().Set("Retry-After", retrySeconds(retry))
				recordRateLimited()
				if audit != nil {
					var keyID int64
					if k, ok := FromContext(r.Context()); ok {
						keyID = k.ID
					}
					audit.Emit(r.Context(), AuditEvent{
						Kind:       AuditAuthRateLimited,
						KeyID:      keyID,
						IP:         clientIP(r),
						UserAgent:  r.UserAgent(),
						OccurredAt: time.Now(),
					})
				}
				writeErr(w, http.StatusTooManyRequests, ErrTooMany.Error())
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// IPMiddleware throttles every request by client IP, regardless of
// auth state. Sits at the root router BEFORE the apikey Middleware so
// bad-key floods (which fail auth before the per-key Middleware ever
// runs) are bounded. Conservative defaults appropriate for a public
// REST surface — authenticated callers get a fresh per-key budget on
// top of this.
//
// Returns 429 + Retry-After when the bucket is empty. No audit emission
// here (anonymous, pre-auth) — Prometheus counter only.
func (rl *RateLimiter) IPMiddleware(rate, burst float64) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			key := "ip:" + clientIP(r)
			ok, retry := rl.Allow(key, rate, burst)
			if !ok {
				w.Header().Set("Retry-After", retrySeconds(retry))
				recordRateLimited()
				writeErr(w, http.StatusTooManyRequests, ErrTooMany.Error())
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// bucketParams returns (key, rate, burst) for the request. Authenticated
// requests use APIKey.ID + APIKey.RateLimitRPS / RateBurst; anonymous
// fall through to per-IP at a conservative 1 rps / 30 burst.
func bucketParams(r *http.Request) (string, float64, float64) {
	if k, ok := FromContext(r.Context()); ok {
		rate := k.RateLimitRPS
		if rate <= 0 {
			rate = 1.0
		}
		burst := float64(k.RateBurst)
		if burst <= 0 {
			burst = 30
		}
		return "k:" + strNum(k.ID), rate, burst
	}
	return "ip:" + remoteIP(r), 1.0, 30.0
}

func remoteIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

func (rl *RateLimiter) janitor() {
	t := time.NewTicker(5 * time.Minute)
	defer t.Stop()
	for {
		select {
		case <-rl.stop:
			return
		case now := <-t.C:
			for i := range rl.shards {
				s := &rl.shards[i]
				s.mu.Lock()
				for k, b := range s.buckets {
					if now.Sub(b.lastSeen) > rl.ttl {
						delete(s.buckets, k)
					}
				}
				s.mu.Unlock()
			}
		}
	}
}

func retrySeconds(d time.Duration) string {
	s := int(d.Seconds() + 0.5)
	if s < 1 {
		s = 1
	}
	return strNum(int64(s))
}

func strNum(n int64) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}
