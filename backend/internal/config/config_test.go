package config

import (
	"strings"
	"testing"
)

func TestValidateProduction_RejectsDevDefaults(t *testing.T) {
	cases := []struct {
		name    string
		mutate  func(*Config)
		wantErr string
	}{
		{
			name:    "dev_password",
			mutate:  func(c *Config) {},
			wantErr: "POSTGRES_PASSWORD is the dev default",
		},
		{
			name: "missing_cors",
			mutate: func(c *Config) {
				c.Postgres.Password = "real-secret"
				c.APIKey.Enabled = true
				c.API.CORSOrigins = nil
			},
			wantErr: "API_CORS_ORIGINS must be set",
		},
		{
			name: "debug_log",
			mutate: func(c *Config) {
				c.Postgres.Password = "real-secret"
				c.APIKey.Enabled = true
				c.API.CORSOrigins = []string{"https://flowgreeks.com"}
				c.Log.Level = "debug"
			},
			wantErr: "LOG_LEVEL=debug",
		},
		{
			name: "apikey_gate_off",
			mutate: func(c *Config) {
				c.Postgres.Password = "real-secret"
				c.API.CORSOrigins = []string{"https://flowgreeks.com"}
				c.APIKey.Enabled = false
			},
			wantErr: "APIKEY_ENABLED must be true",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c := baselineProdConfig()
			tc.mutate(c)
			err := c.validate()
			if err == nil {
				t.Fatal("expected error, got nil")
			}
			if !strings.Contains(err.Error(), tc.wantErr) {
				t.Errorf("error %q does not contain %q", err.Error(), tc.wantErr)
			}
		})
	}
}

func TestValidateProduction_AcceptsClean(t *testing.T) {
	c := baselineProdConfig()
	c.Postgres.Password = "very-real-secret"
	c.API.CORSOrigins = []string{"https://flowgreeks.com"}
	c.APIKey.Enabled = true
	c.Log.Level = "info"
	if err := c.validate(); err != nil {
		t.Fatalf("clean prod config rejected: %v", err)
	}
}

func TestValidate_DevModeSkipsProductionGuard(t *testing.T) {
	c := baselineProdConfig()
	c.AppEnv = "development"
	// Dev defaults — would explode under production, fine in dev.
	c.Postgres.Password = "flowgreeks_dev_only"
	c.API.CORSOrigins = nil
	c.Log.Level = "debug"
	c.APIKey.Enabled = false
	if err := c.validate(); err != nil {
		t.Fatalf("dev mode rejected: %v", err)
	}
}

// baselineProdConfig returns a Config that passes the basic missing-
// env-var check so each test only varies the production-guard surface.
func baselineProdConfig() *Config {
	return &Config{
		AppEnv: "production",
		Postgres: PostgresConfig{
			User:     "flowgreeks",
			Password: "flowgreeks_dev_only",
			Database: "flowgreeks",
			Host:     "postgres",
			Port:     5432,
		},
		Log: LogConfig{Level: "info", Format: "json"},
		API: APIConfig{
			ListenAddr:  ":8080",
			CORSOrigins: []string{"https://flowgreeks.com"},
		},
	}
}
