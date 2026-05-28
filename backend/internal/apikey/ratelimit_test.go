package apikey

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestRateLimiter_AllowsBurstThenBlocks(t *testing.T) {
	rl := NewRateLimiter()
	defer rl.Close()
	for i := 0; i < 5; i++ {
		ok, _ := rl.Allow("k:1", 1.0, 5)
		if !ok {
			t.Fatalf("burst hit %d should pass", i)
		}
	}
	ok, retry := rl.Allow("k:1", 1.0, 5)
	if ok {
		t.Fatal("6th call should be denied")
	}
	if retry <= 0 {
		t.Fatalf("expected positive retry, got %s", retry)
	}
}

func TestRateLimiter_PerKeyIsolation(t *testing.T) {
	rl := NewRateLimiter()
	defer rl.Close()
	for i := 0; i < 2; i++ {
		if ok, _ := rl.Allow("k:1", 1.0, 2); !ok {
			t.Fatalf("hit %d on key 1 should pass", i)
		}
	}
	if ok, _ := rl.Allow("k:1", 1.0, 2); ok {
		t.Fatal("3rd call should be denied")
	}
	if ok, _ := rl.Allow("k:2", 1.0, 2); !ok {
		t.Fatal("different key should not share budget")
	}
}

func TestRateLimiter_Refills(t *testing.T) {
	rl := NewRateLimiter()
	defer rl.Close()
	if ok, _ := rl.Allow("k:1", 100.0, 1); !ok {
		t.Fatal("first call should pass")
	}
	if ok, _ := rl.Allow("k:1", 100.0, 1); ok {
		t.Fatal("second call within ms should be denied")
	}
	time.Sleep(15 * time.Millisecond)
	if ok, _ := rl.Allow("k:1", 100.0, 1); !ok {
		t.Fatal("after refill window, call should pass")
	}
}

func TestRateLimiter_TierChangePicksUpNewBudget(t *testing.T) {
	rl := NewRateLimiter()
	defer rl.Close()
	// First seed bucket with low burst.
	if ok, _ := rl.Allow("k:1", 1.0, 1); !ok {
		t.Fatal("seed call should pass")
	}
	if ok, _ := rl.Allow("k:1", 1.0, 1); ok {
		t.Fatal("second call should be denied at burst=1")
	}
	// Tier upgraded — burst is now 5. Bucket starts replenishing at the
	// new rate, so within a refill interval we should get throughput.
	time.Sleep(20 * time.Millisecond)
	if ok, _ := rl.Allow("k:1", 100.0, 5); !ok {
		t.Fatal("upgraded burst should permit a request")
	}
}

func TestRateLimiterMiddleware_429WithRetryAfter(t *testing.T) {
	rl := NewRateLimiter()
	defer rl.Close()
	store := NewMemoryStore()
	secret, hash, _ := Generate()
	if _, err := store.Create(context.Background(), APIKey{
		Name:         "tight",
		Hash:         hash,
		RateLimitRPS: 0.01, // ~100s refill
		RateBurst:    1,
	}); err != nil {
		t.Fatalf("seed: %v", err)
	}
	authMW := NewMiddleware(store, NoopAuditSink{})
	limiter := rl.Middleware(NoopAuditSink{})

	called := 0
	chain := authMW.Handler(limiter(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		called++
		w.WriteHeader(http.StatusOK)
	})))

	mk := func() *http.Request {
		r := httptest.NewRequest(http.MethodGet, "/api/simulate/SPX", nil)
		r.Header.Set("Authorization", "Bearer "+secret)
		r.RemoteAddr = "9.9.9.9:1"
		return r
	}

	w1 := httptest.NewRecorder()
	chain.ServeHTTP(w1, mk())
	if w1.Code != http.StatusOK {
		t.Fatalf("first call %d, want 200", w1.Code)
	}
	w2 := httptest.NewRecorder()
	chain.ServeHTTP(w2, mk())
	if w2.Code != http.StatusTooManyRequests {
		t.Fatalf("second call %d, want 429", w2.Code)
	}
	if w2.Header().Get("Retry-After") == "" {
		t.Error("Retry-After header missing")
	}
	if called != 1 {
		t.Errorf("downstream called %d times, want 1", called)
	}
}

func TestRateLimiterMiddleware_AnonymousFallsBackToIP(t *testing.T) {
	rl := NewRateLimiter()
	defer rl.Close()
	limiter := rl.Middleware(NoopAuditSink{})
	called := 0
	h := limiter(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		called++
		w.WriteHeader(http.StatusOK)
	}))

	// Anonymous (no APIKey in context) → keyed by IP at 1 rps / 30 burst.
	mk := func(ip string) *http.Request {
		r := httptest.NewRequest(http.MethodGet, "/api/levels/spx", nil)
		r.RemoteAddr = ip + ":1"
		return r
	}
	for i := 0; i < 30; i++ {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, mk("1.1.1.1"))
		if w.Code != http.StatusOK {
			t.Fatalf("burst hit %d on ip 1.1.1.1: status %d", i, w.Code)
		}
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, mk("1.1.1.1"))
	if w.Code != http.StatusTooManyRequests {
		t.Fatalf("31st on same ip: %d, want 429", w.Code)
	}
	// Different IP starts fresh.
	w = httptest.NewRecorder()
	h.ServeHTTP(w, mk("2.2.2.2"))
	if w.Code != http.StatusOK {
		t.Fatalf("fresh ip: %d, want 200", w.Code)
	}
	if called != 31 {
		t.Errorf("downstream called %d, want 31 (30 on ip1 + 1 on ip2)", called)
	}
}
