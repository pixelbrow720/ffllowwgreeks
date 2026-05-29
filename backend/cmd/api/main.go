// Package main runs the FlowGreeks API server.
//
// Responsibilities:
//   - REST endpoints: /api/snapshot/{symbol}, /api/levels/{symbol}, /api/simulate, /api/alerts/*, /api/backtest/run
//   - WebSocket fanout: /ws/live, /ws/replay/{id}
//   - Subscribes to NATS `state.>` stream and serves cached + live data
//   - /health, /health/live, /health/ready, /metrics
//
// FlowGreeks runs as an add-on inside flowjob.id — the parent site
// owns user accounts and billing. This binary authenticates inbound
// traffic via opaque API keys provisioned by the parent site.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"sync/atomic"
	"syscall"
	"time"

	"flowgreeks/internal/alerts"
	"flowgreeks/internal/api"
	"flowgreeks/internal/apikey"
	"flowgreeks/internal/bus"
	"flowgreeks/internal/config"
	"flowgreeks/internal/logger"
	"flowgreeks/internal/replay"
	"flowgreeks/internal/trace"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/nats-io/nats.go"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

const serviceName = "api"

func main() {
	cfg, err := config.Load()
	if err != nil {
		_, _ = os.Stderr.WriteString("config load failed: " + err.Error() + "\n")
		os.Exit(1)
	}

	log := logger.New(cfg.Log.Format, cfg.Log.Level).With(
		"service", serviceName,
		"env", cfg.AppEnv,
	)

	rootCtx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// NATS connection for live state stream.
	nc, err := nats.Connect(cfg.NATS.URL,
		nats.Name("flowgreeks-api"),
		nats.MaxReconnects(-1),
		nats.ReconnectWait(time.Second),
	)
	if err != nil {
		log.Error("nats connect failed", "err", err)
		os.Exit(1)
	}
	defer nc.Close()
	log.Info("nats connected", "url", cfg.NATS.URL)

	cache := api.NewCache()
	broker := api.NewBroker()
	if err := api.SubscribeNATS(rootCtx, nc, cache, broker); err != nil {
		log.Error("nats state subscribe failed", "err", err)
		os.Exit(1)
	}

	// Single shared pgxpool. auth, replay, and backtest all hit the
	// same Postgres so independent pools just stack default
	// MaxConns=4×CPU connections × 3 = wasted limits without any
	// isolation benefit. Pool tuning lives in config.PostgresConfig.
	// Best-effort: if Postgres is unreachable at boot, dependent
	// surfaces (/auth/*, /ws/replay, /api/backtest/run) degrade
	// individually; the rest of the api stays up.
	var sharedPool *pgxpool.Pool
	if pool, err := pgxpool.New(rootCtx, cfg.Postgres.DSN()); err == nil {
		pingCtx, pingCancel := context.WithTimeout(rootCtx, 5*time.Second)
		if pingErr := pool.Ping(pingCtx); pingErr != nil {
			log.Warn("postgres ping failed; auth/replay/backtest disabled", "err", pingErr)
			pool.Close()
		} else {
			sharedPool = pool
			go func() {
				<-rootCtx.Done()
				pool.Close()
			}()
			log.Info("postgres pool ready", "dsn_host", cfg.Postgres.Host)
		}
		pingCancel()
	} else {
		log.Warn("postgres connect failed; auth/replay/backtest disabled", "err", err)
	}

	r := chi.NewRouter()
	r.Use(middleware.RequestID)
	r.Use(api.TrustAwareRealIP(cfg.API.TrustedProxies))
	r.Use(middleware.Recoverer)
	r.Use(traceMiddleware)
	r.Use(requestLogger(log))
	r.Use(api.SecurityHeaders)
	r.Use(corsMiddleware(cfg.API.CORSOrigins))
	r.Use(api.BodyLimit)
	r.Use(api.MetricsMiddleware)

	r.Get("/health", healthHandler)
	r.Get("/health/live", healthHandler)
	var draining atomic.Bool
	r.Get("/health/ready", readinessHandler(nc, cfg, &draining))

	// /metrics is mounted on the public router only when no separate
	// metrics listener is configured (development). When API_METRICS_ADDR
	// is set, /metrics moves to that listener so per-key auth-failure
	// rate, subscriber counts, and queue lag are not visible to
	// unauth clients on the main port.
	if cfg.API.MetricsAddr == "" {
		r.Method(http.MethodGet, "/metrics", promhttp.Handler())
	} else {
		metricsSrv := startMetricsListener(cfg.API.MetricsAddr, log)
		defer func() {
			if metricsSrv != nil {
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				_ = metricsSrv.Shutdown(ctx)
			}
		}()
	}

	// API-key auth setup. Returns the configured middleware (or nil
	// when no Postgres pool is available — protected routes degrade
	// to "open" in that case so dev still works without a database).
	apiKeyMW, apiKeyLimiter, apiKeyAudit := setupAPIKey(rootCtx, cfg, log, sharedPool)

	// Per-IP throttle at root scope, BEFORE auth. Caps bad-key floods
	// and unrate-limited public endpoint hammering (snapshot, levels)
	// so a single attacker can't saturate the pgxpool with failing
	// LookupByHash calls or DoS the public REST surface. Per-key tier
	// limits still apply on top inside the protected router.
	if apiKeyLimiter != nil {
		r.Use(apiKeyLimiter.IPMiddleware(30, 60))
	}

	handlers := &api.Handlers{Cache: cache, Broker: broker}
	handlers.MountPublic(r)

	// Alerts engine: subscribes to state.>, evaluates rules, delivers
	// triggers to the broker as StateKindAlert (so /ws/live carries
	// them) plus an in-process fanout sink for diagnostic /admin use.
	alertEng := alerts.NewEngine()
	alertEng.AddSink("broker", &api.BrokerSink{Broker: broker})
	if err := api.SubscribeAlertsToNATS(rootCtx, nc, alertEng); err != nil {
		log.Error("alerts subscribe failed", "err", err)
		os.Exit(1)
	}

	// Protected surface: simulate, alerts CRUD, backtest run. When
	// the api-key gate is on, every route inside this group requires
	// a valid API key in Authorization: Bearer <secret> or X-API-Key.
	protected := chi.NewRouter()
	if cfg.APIKey.Enabled && apiKeyMW != nil {
		protected.Use(apiKeyMW.Handler)
		log.Info("api-key gate ON — /api/simulate, /api/alerts, /api/backtest, /ws/live, /ws/replay require valid key")
	} else {
		log.Info("api-key gate OFF — protected routes are open")
	}
	// Per-key rate limit on the protected surface. /api/simulate and
	// /api/backtest each carry a 30s deadline server-side, so without
	// a per-key gate a single client could fan out N concurrent
	// requests and saturate compute. Each APIKey carries its own RPS
	// + burst so the parent site can provision tier-specific budgets.
	if apiKeyLimiter != nil {
		protected.Use(apiKeyLimiter.Middleware(apiKeyAudit))
	}
	handlers.MountProtected(protected)
	(&api.AlertHandlers{Engine: alertEng, Audit: apiKeyAudit}).Mount(protected)
	r.Mount("/", protected)

	// /ws/live and /ws/replay/* mount on `protected` so APIKEY_ENABLED=true
	// gates them the same way it gates the REST surface. The api-key
	// middleware is a normal http.Handler middleware that runs before
	// the websocket Accept upgrade, so the upgrade still completes
	// cleanly once auth passes. When APIKEY_ENABLED=false the protected
	// router has no auth middleware and the WS endpoints stay open for
	// local dev — same behaviour as the REST routes.
	live := &api.LiveHandler{
		Broker:  broker,
		Cache:   cache,
		Log:     log,
		Origins: cfg.API.CORSOrigins,
	}
	protected.Method(http.MethodGet, "/ws/live", live)

	// Replay WS handler — best-effort. If the shared pool is missing
	// or the publisher fails to construct, the rest of the api stays
	// up; only /ws/replay returns 503.
	if mgr := setupReplayManager(rootCtx, cfg, log, sharedPool); mgr != nil {
		rh := &replay.WSHandler{Manager: mgr, Log: log, Origins: cfg.API.CORSOrigins}
		protected.Method(http.MethodGet, "/ws/replay/*", rh)
		log.Info("replay manager ready")
	} else {
		log.Warn("replay manager unavailable; /ws/replay disabled")
	}

	// Backtest endpoint — requires Postgres for the historical
	// dealer_state_1s table compute writes. Mounted inside the
	// protected group so APIKEY_ENABLED=true gates it too.
	if sharedPool != nil {
		(&api.BacktestHandlers{Pool: sharedPool}).Mount(protected)
		log.Info("backtest endpoint mounted")
	} else {
		log.Warn("backtest disabled (no postgres pool)")
	}

	srv := &http.Server{
		Addr:              cfg.API.ListenAddr,
		Handler:           r,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       30 * time.Second,
		WriteTimeout:      0, // 0 for streaming + WS upgrade
		IdleTimeout:       120 * time.Second,
	}

	// Admin listener: separate http.Server bound by default to loopback
	// so it never lands on the public mux. Started only when ADMIN_TOKEN
	// is set so dev / CI don't have to provision a token. flowjob.id
	// reaches it via tunnel/SSH/internal mesh.
	var adminSrv *http.Server
	if cfg.Admin.Token != "" && sharedPool != nil {
		ar := chi.NewRouter()
		ar.Use(middleware.RequestID)
		ar.Use(middleware.Recoverer)
		(&api.Admin{
			Store: apikey.NewPgStore(sharedPool),
			Token: cfg.Admin.Token,
			Audit: apiKeyAudit,
		}).Mount(ar)
		adminSrv = &http.Server{
			Addr:              cfg.Admin.ListenAddr,
			Handler:           ar,
			ReadHeaderTimeout: 5 * time.Second,
			ReadTimeout:       10 * time.Second,
			WriteTimeout:      10 * time.Second,
			IdleTimeout:       60 * time.Second,
		}
		go func() {
			log.Info("admin listening", "addr", cfg.Admin.ListenAddr)
			if err := adminSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
				log.Error("admin listener failed", "addr", cfg.Admin.ListenAddr, "err", err)
			}
		}()
	} else if cfg.Admin.Token == "" {
		log.Info("admin server disabled (ADMIN_TOKEN unset)")
	} else {
		log.Warn("admin server disabled (no postgres pool)")
	}

	serverErr := make(chan error, 1)
	go func() {
		log.Info("api listening", "addr", cfg.API.ListenAddr)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			serverErr <- err
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)

	select {
	case err := <-serverErr:
		log.Error("server crashed", "err", err)
		os.Exit(1)
	case sig := <-stop:
		log.Info("shutdown signal received", "signal", sig.String())
	}

	// Phase 1: flip readiness to draining and wait the configured grace
	// so load balancers / k8s pull this instance from rotation BEFORE
	// any in-flight request gets cut. Default 5s — covers a 1-2s probe
	// interval with margin. Skipped when SHUTDOWN_DRAIN_DELAY=0.
	draining.Store(true)
	drainDelay := 5 * time.Second
	if v := os.Getenv("SHUTDOWN_DRAIN_DELAY"); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			drainDelay = d
		}
	}
	if drainDelay > 0 {
		log.Info("draining: /health/ready now reports 503", "delay", drainDelay.String())
		time.Sleep(drainDelay)
	}

	// Phase 2: stop accepting new connections, finish in-flight ones.
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer shutdownCancel()
	if adminSrv != nil {
		// Drain admin first; it's loopback-only and short-lived so a
		// strict 5s budget is plenty.
		adminCtx, adminCancel := context.WithTimeout(context.Background(), 5*time.Second)
		if err := adminSrv.Shutdown(adminCtx); err != nil {
			log.Warn("admin graceful shutdown failed", "err", err)
		}
		adminCancel()
	}
	if err := srv.Shutdown(shutdownCtx); err != nil {
		log.Error("graceful shutdown failed", "err", err)
		os.Exit(1)
	}
	log.Info("api stopped")
}

func healthHandler(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(`{"status":"ok","service":"api"}`))
}

// readinessHandler reports whether the api binary is ready to serve
// real traffic. Checks NATS reachability and Postgres reachability.
// Returns 200 with per-dependency status when all green, 503 otherwise.
//
// Liveness (/health) only signals "process up". Readiness signals
// "dependencies up" — the orchestrator should withhold traffic until
// this returns 200. Using this instead of /health for k8s readinessProbe
// avoids restart loops when Postgres is briefly slow.
//
// During graceful shutdown, draining flips to true; readiness returns
// 503 with status="draining" so the load balancer pulls this instance
// from rotation before in-flight requests time out.
func readinessHandler(nc *nats.Conn, cfg *config.Config, draining *atomic.Bool) http.HandlerFunc {
	return readinessHandlerForState(
		draining,
		func() (bool, string) {
			if nc != nil && nc.IsConnected() {
				return true, ""
			}
			// Don't expose NATS internal status / last-error verbatim.
			// Log it; return generic.
			if nc != nil {
				if le := nc.LastError(); le != nil {
					slog.Default().Warn("readiness nats not connected", "err", le)
				} else {
					slog.Default().Warn("readiness nats status", "status", nc.Status().String())
				}
			}
			return false, "unreachable"
		},
		func(ctx context.Context) (bool, string) {
			pool, err := pgxpool.New(ctx, cfg.Postgres.DSN())
			if err != nil {
				// Don't echo the raw error: pgx errors carry host, port,
				// user, dial diagnostics that fingerprint the internal DB
				// topology. Log internally, return generic to caller.
				slog.Default().Warn("readiness pgx pool failed", "err", err)
				return false, "unreachable"
			}
			defer pool.Close()
			if perr := pool.Ping(ctx); perr != nil {
				slog.Default().Warn("readiness pgx ping failed", "err", perr)
				return false, "unreachable"
			}
			return true, ""
		},
	)
}

// readinessHandlerForState is the testable form. draining can be nil
// (always-ready except for dep failure); pass a real flag in production
// so shutdown flips it.
func readinessHandlerForState(draining *atomic.Bool,
	natsCheck func() (bool, string),
	postgresCheck func(context.Context) (bool, string)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		type depStatus struct {
			OK    bool   `json:"ok"`
			Error string `json:"error,omitempty"`
		}
		out := struct {
			Status  string               `json:"status"`
			Service string               `json:"service"`
			Deps    map[string]depStatus `json:"deps"`
		}{
			Service: "api",
			Deps:    make(map[string]depStatus, 2),
		}
		if draining != nil && draining.Load() {
			out.Status = "draining"
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusServiceUnavailable)
			_ = json.NewEncoder(w).Encode(out)
			return
		}
		allOK := true

		if ok, msg := natsCheck(); ok {
			out.Deps["nats"] = depStatus{OK: true}
		} else {
			allOK = false
			out.Deps["nats"] = depStatus{Error: msg}
		}

		pingCtx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		if ok, msg := postgresCheck(pingCtx); ok {
			out.Deps["postgres"] = depStatus{OK: true}
		} else {
			allOK = false
			out.Deps["postgres"] = depStatus{Error: msg}
		}

		w.Header().Set("Content-Type", "application/json")
		if allOK {
			out.Status = "ready"
			w.WriteHeader(http.StatusOK)
		} else {
			out.Status = "not_ready"
			w.WriteHeader(http.StatusServiceUnavailable)
		}
		_ = json.NewEncoder(w).Encode(out)
	}
}

// readinessHandlerFor is kept as the back-compat entry point used by
// existing tests — always reports ready / not_ready with no draining.
func readinessHandlerFor(natsCheck func() (bool, string),
	postgresCheck func(context.Context) (bool, string)) http.HandlerFunc {
	return readinessHandlerForState(nil, natsCheck, postgresCheck)
}

// startMetricsListener boots a separate HTTP listener that serves only
// /metrics + /health (for self-checks). Used in production to keep the
// Prometheus surface off the public port — operators bind this to a
// localhost or admin-network address (e.g. 127.0.0.1:9100) and scrape
// over that interface.
func startMetricsListener(addr string, log *slog.Logger) *http.Server {
	mr := chi.NewRouter()
	mr.Method(http.MethodGet, "/metrics", promhttp.Handler())
	mr.Get("/health", healthHandler)
	srv := &http.Server{
		Addr:              addr,
		Handler:           mr,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	go func() {
		log.Info("metrics listener", "addr", addr)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("metrics listener failed", "addr", addr, "err", err)
		}
	}()
	return srv
}

// setupAPIKey wires the API-key auth surface against the shared pool.
// Returns (middleware, limiter, audit sink) — any may be nil when
// pool isn't available, in which case the protected routes degrade
// to "open".
//
// The middleware looks up keys against the api_keys table; the
// limiter consumes the resolved APIKey from request context for
// per-key budgets; the audit sink emits structured slog records for
// every auth attempt.
func setupAPIKey(ctx context.Context, cfg *config.Config, log *slog.Logger, pool *pgxpool.Pool) (*apikey.Middleware, *apikey.RateLimiter, apikey.AuditSink) {
	audit := apikey.NewSlogAuditSink(log.With("component", "audit"))
	limiter := apikey.NewRateLimiter()
	go func() {
		<-ctx.Done()
		limiter.Close()
	}()
	if pool == nil {
		log.Warn("api-key middleware disabled (no postgres pool)")
		return nil, limiter, audit
	}
	store := apikey.NewPgStore(pool)
	mw := apikey.NewMiddleware(store, audit)
	log.Info("api-key wired",
		"enabled_gate", cfg.APIKey.Enabled,
	)
	return mw, limiter, audit
}

// setupReplayManager wires the dependencies a replay session needs:
// the shared pgxpool for the historical reader and a bus.Publisher to
// re-emit ticks onto the same NATS subjects compute consumes from.
// Returns nil if either dependency is missing — the api binary stays
// up without the replay surface in that case. Pool lifetime is owned
// by main; the publisher's Close runs on ctx done.
func setupReplayManager(ctx context.Context, cfg *config.Config, log *slog.Logger, pool *pgxpool.Pool) *replay.Manager {
	if pool == nil {
		return nil
	}
	pub, err := bus.NewPublisher(ctx, cfg.NATS.URL)
	if err != nil {
		log.Warn("replay: nats publisher init failed", "err", err)
		return nil
	}
	go func() {
		<-ctx.Done()
		_ = pub.Close()
	}()

	rd := replay.NewReader(pool)
	return replay.NewManager(rd, pub, log.With("component", "replay"), replay.ManagerOpts{})
}

// corsMiddleware emits permissive CORS headers for the configured origins.
// Empty list = allow any origin (development).
func corsMiddleware(origins []string) func(http.Handler) http.Handler {
	allowAny := len(origins) == 0
	allowed := make(map[string]struct{}, len(origins))
	for _, o := range origins {
		allowed[o] = struct{}{}
	}
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			origin := r.Header.Get("Origin")
			if origin != "" {
				if allowAny {
					w.Header().Set("Access-Control-Allow-Origin", origin)
				} else if _, ok := allowed[origin]; ok {
					w.Header().Set("Access-Control-Allow-Origin", origin)
				}
				w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
				w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
				w.Header().Set("Access-Control-Max-Age", "300")
			}
			if r.Method == http.MethodOptions {
				w.WriteHeader(http.StatusNoContent)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

func requestLogger(log interface {
	Info(msg string, args ...any)
}) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			ww := middleware.NewWrapResponseWriter(w, r.ProtoMajor)
			next.ServeHTTP(ww, r)
			log.Info("http",
				"method", r.Method,
				"path", r.URL.Path,
				"status", ww.Status(),
				"bytes", ww.BytesWritten(),
				"duration_ms", time.Since(start).Milliseconds(),
				"req_id", middleware.GetReqID(r.Context()),
				"trace_id", trace.FromContext(r.Context()),
			)
		})
	}
}

// traceMiddleware extracts an upstream trace id from X-Trace-ID, falling
// back to chi's request id, otherwise generates a fresh one. The id is
// stashed on the request context (via trace.WithID) and echoed back on
// the response so clients can correlate.
func traceMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := trace.FromHTTP(r)
		if id == "" {
			if reqID := middleware.GetReqID(r.Context()); reqID != "" {
				id = reqID
			} else {
				id = trace.NewID()
			}
		}
		w.Header().Set(trace.HeaderName, id)
		ctx := trace.WithID(r.Context(), id)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}
