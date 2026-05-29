// Package config loads typed configuration from environment variables.
//
// Loading happens once at process start. Missing required keys cause hard
// startup failure — we never run with degraded config silently.
package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// Config is the fully-typed runtime configuration for any FlowGreeks binary.
type Config struct {
	AppEnv string

	Databento DatabentoConfig
	Postgres  PostgresConfig
	Redis     RedisConfig
	NATS      NATSConfig
	API       APIConfig
	Log       LogConfig
	APIKey    APIKeyConfig
	Admin     AdminConfig
}

// APIKeyConfig controls the API-key auth surface. FlowGreeks runs as
// an add-on inside flowjob.id — there are no user accounts here.
// Inbound requests carry an opaque key provisioned by the parent site.
//
// When Enabled=true the protected REST + WS surface requires a valid
// key; when false the routes are open (development).
type APIKeyConfig struct {
	Enabled bool
}

// AdminConfig controls the operator-only admin surface. The admin
// listener is a separate http.Server bound by default to loopback.
// flowjob.id (the parent product) reaches it via tunnel/SSH/internal
// mesh — never over the public internet — to list and revoke API keys.
//
// Token gates every request via Authorization: Bearer <token> with a
// constant-time compare. Empty Token disables the admin server entirely
// so dev / tests don't have to set anything.
type AdminConfig struct {
	ListenAddr string
	Token      string
}

type DatabentoConfig struct {
	APIKey string
}

type PostgresConfig struct {
	Host     string
	Port     int
	User     string
	Password string
	Database string

	// Pool tuning. Defaults match pgx's own defaults but are exposed
	// per-binary via env so api / ingest / compute can right-size
	// independently. Empty string = pgx default.
	MaxConns        int32         // POSTGRES_MAX_CONNS
	MinConns        int32         // POSTGRES_MIN_CONNS
	MaxConnLifetime time.Duration // POSTGRES_MAX_CONN_LIFETIME (e.g. "1h")
	MaxConnIdleTime time.Duration // POSTGRES_MAX_CONN_IDLE_TIME (e.g. "30m")
}

func (p PostgresConfig) DSN() string {
	dsn := fmt.Sprintf("postgres://%s:%s@%s:%d/%s?sslmode=disable",
		p.User, p.Password, p.Host, p.Port, p.Database)
	// pgxpool reads pool tuning from these query params when present,
	// so we encode them into the DSN. Empty values are skipped so the
	// pgx defaults apply.
	parts := []string{}
	if p.MaxConns > 0 {
		parts = append(parts, fmt.Sprintf("pool_max_conns=%d", p.MaxConns))
	}
	if p.MinConns > 0 {
		parts = append(parts, fmt.Sprintf("pool_min_conns=%d", p.MinConns))
	}
	if p.MaxConnLifetime > 0 {
		parts = append(parts, fmt.Sprintf("pool_max_conn_lifetime=%s", p.MaxConnLifetime.String()))
	}
	if p.MaxConnIdleTime > 0 {
		parts = append(parts, fmt.Sprintf("pool_max_conn_idle_time=%s", p.MaxConnIdleTime.String()))
	}
	if len(parts) > 0 {
		dsn += "&" + strings.Join(parts, "&")
	}
	return dsn
}

type RedisConfig struct {
	Host     string
	Port     int
	Password string
}

func (r RedisConfig) Addr() string {
	return fmt.Sprintf("%s:%d", r.Host, r.Port)
}

type NATSConfig struct {
	URL string
}

type APIConfig struct {
	ListenAddr     string
	CORSOrigins    []string
	TrustedProxies []string // CIDR list; XFF only honoured when RemoteAddr matches one of these

	// MetricsAddr is the bind address for the /metrics endpoint. When
	// empty (default), /metrics is mounted on the public router at
	// ListenAddr — fine for local dev. In production set this to a
	// localhost-only or admin-network address (e.g. "127.0.0.1:9100"
	// or "10.0.0.1:9100") so per-key auth-failure rate, subscriber
	// counts, and queue lag are not leaked to public scrapers.
	MetricsAddr string
}

type LogConfig struct {
	Level  string
	Format string
}

// Load reads env vars and returns a Config. Returns error on missing required
// vars or invalid values. Does NOT fall back to defaults for credentials —
// only for non-secret operational settings.
func Load() (*Config, error) {
	c := &Config{
		AppEnv: getEnv("APP_ENV", "development"),
		Databento: DatabentoConfig{
			APIKey: os.Getenv("DATABENTO_API_KEY"),
		},
		Postgres: PostgresConfig{
			Host:     getEnv("POSTGRES_HOST", "localhost"),
			User:     os.Getenv("POSTGRES_USER"),
			Password: os.Getenv("POSTGRES_PASSWORD"),
			Database: os.Getenv("POSTGRES_DB"),
		},
		Redis: RedisConfig{
			Host:     getEnv("REDIS_HOST", "localhost"),
			Password: os.Getenv("REDIS_PASSWORD"),
		},
		NATS: NATSConfig{
			URL: getEnv("NATS_URL", "nats://localhost:4222"),
		},
		API: APIConfig{
			ListenAddr:     getEnv("API_LISTEN_ADDR", ":8080"),
			CORSOrigins:    splitCSV(getEnv("API_CORS_ORIGINS", "http://localhost:3000")),
			TrustedProxies: splitCSV(os.Getenv("API_TRUSTED_PROXIES")),
			MetricsAddr:    os.Getenv("API_METRICS_ADDR"),
		},
		Log: LogConfig{
			Level:  getEnv("LOG_LEVEL", "info"),
			Format: getEnv("LOG_FORMAT", "json"),
		},
		APIKey: APIKeyConfig{
			Enabled: getEnv("APIKEY_ENABLED", "false") == "true",
		},
		Admin: AdminConfig{
			ListenAddr: getEnv("ADMIN_LISTEN_ADDR", "127.0.0.1:9090"),
			Token:      os.Getenv("ADMIN_TOKEN"),
		},
	}

	pgPort, err := strconv.Atoi(getEnv("POSTGRES_PORT", "5432"))
	if err != nil {
		return nil, fmt.Errorf("invalid POSTGRES_PORT: %w", err)
	}
	c.Postgres.Port = pgPort

	if v := os.Getenv("POSTGRES_MAX_CONNS"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 {
			return nil, fmt.Errorf("invalid POSTGRES_MAX_CONNS: %s", v)
		}
		c.Postgres.MaxConns = int32(n)
	}
	if v := os.Getenv("POSTGRES_MIN_CONNS"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 0 {
			return nil, fmt.Errorf("invalid POSTGRES_MIN_CONNS: %s", v)
		}
		c.Postgres.MinConns = int32(n)
	}
	if v := os.Getenv("POSTGRES_MAX_CONN_LIFETIME"); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil {
			return nil, fmt.Errorf("invalid POSTGRES_MAX_CONN_LIFETIME: %w", err)
		}
		c.Postgres.MaxConnLifetime = d
	}
	if v := os.Getenv("POSTGRES_MAX_CONN_IDLE_TIME"); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil {
			return nil, fmt.Errorf("invalid POSTGRES_MAX_CONN_IDLE_TIME: %w", err)
		}
		c.Postgres.MaxConnIdleTime = d
	}

	redisPort, err := strconv.Atoi(getEnv("REDIS_PORT", "6379"))
	if err != nil {
		return nil, fmt.Errorf("invalid REDIS_PORT: %w", err)
	}
	c.Redis.Port = redisPort

	if err := c.validate(); err != nil {
		return nil, err
	}
	return c, nil
}

func (c *Config) validate() error {
	var missing []string
	if c.Postgres.User == "" {
		missing = append(missing, "POSTGRES_USER")
	}
	if c.Postgres.Password == "" {
		missing = append(missing, "POSTGRES_PASSWORD")
	}
	if c.Postgres.Database == "" {
		missing = append(missing, "POSTGRES_DB")
	}
	if len(missing) > 0 {
		return fmt.Errorf("required env vars missing: %s", strings.Join(missing, ", "))
	}

	// Production guard: refuse to boot with weak defaults that are
	// fine in dev but disastrous on a public host. APP_ENV=production
	// triggers strict checks; any other value (development, staging,
	// test, ci) skips them.
	if c.AppEnv == "production" {
		if err := c.validateProduction(); err != nil {
			return err
		}
	}
	return nil
}

// validateProduction enforces hardening rules that only apply on
// public deployments. Each rule traces back to a specific failure mode:
//
//   - dev DB password leaks via .env.example into a prod env file
//   - empty CORS origin list = wildcard, anyone hits the api
//   - debug log level leaks tokens / payloads to disk
//   - api-key gate accidentally left off after a deploy
func (c *Config) validateProduction() error {
	var problems []string

	// DB credentials must not be the dev defaults from .env.example.
	devDefaults := map[string]string{
		"POSTGRES_PASSWORD": "flowgreeks_dev_only",
		"POSTGRES_USER":     "flowgreeks",
	}
	if c.Postgres.Password == devDefaults["POSTGRES_PASSWORD"] {
		problems = append(problems, "POSTGRES_PASSWORD is the dev default")
	}

	// API-key gate must be ON in production. Otherwise the protected
	// surface is open to the world.
	if !c.APIKey.Enabled {
		problems = append(problems, "APIKEY_ENABLED must be true in production")
	}

	// CORS: empty list = allow any origin in our middleware. Fine for
	// dev, terrible for prod.
	if len(c.API.CORSOrigins) == 0 {
		problems = append(problems, "API_CORS_ORIGINS must be set explicitly in production")
	}

	// Log level: debug logs request bodies / Authorization headers in
	// some upstream libraries. Refuse to start with that on prod.
	if strings.EqualFold(c.Log.Level, "debug") {
		problems = append(problems, "LOG_LEVEL=debug is unsafe in production")
	}

	// Metrics endpoint: must be on its own bind address in production
	// so per-key auth-failure rate, subscriber counts, and queue lag
	// can't be scraped from the public surface. Local-only or
	// admin-network only.
	if c.API.MetricsAddr == "" {
		problems = append(problems, "API_METRICS_ADDR must be set in production (e.g. 127.0.0.1:9100) so /metrics is not on the public listener")
	}

	if len(problems) > 0 {
		return fmt.Errorf("production config rejected: %s", strings.Join(problems, "; "))
	}
	return nil
}

func getEnv(key, fallback string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return fallback
}

func splitCSV(s string) []string {
	if s == "" {
		return nil
	}
	parts := strings.Split(s, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}
