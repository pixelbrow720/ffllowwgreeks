package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestReadiness_AllGreen(t *testing.T) {
	h := readinessHandlerFor(
		func() (bool, string) { return true, "" },
		func(_ context.Context) (bool, string) { return true, "" },
	)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/health/ready", nil))
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", w.Code)
	}
	var got struct {
		Status string                       `json:"status"`
		Deps   map[string]map[string]any    `json:"deps"`
	}
	if err := json.NewDecoder(w.Body).Decode(&got); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if got.Status != "ready" {
		t.Errorf("status = %q, want ready", got.Status)
	}
	for k, v := range got.Deps {
		if ok, _ := v["ok"].(bool); !ok {
			t.Errorf("dep %s not ok: %v", k, v)
		}
	}
}

func TestReadiness_PostgresDown_503(t *testing.T) {
	h := readinessHandlerFor(
		func() (bool, string) { return true, "" },
		func(_ context.Context) (bool, string) {
			return false, "ping: " + errors.New("connection refused").Error()
		},
	)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/health/ready", nil))
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", w.Code)
	}
	var got struct {
		Status string                       `json:"status"`
		Deps   map[string]map[string]any    `json:"deps"`
	}
	if err := json.NewDecoder(w.Body).Decode(&got); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if got.Status != "not_ready" {
		t.Errorf("status = %q, want not_ready", got.Status)
	}
	if natsOK, _ := got.Deps["nats"]["ok"].(bool); !natsOK {
		t.Error("nats should still be ok")
	}
	if pgOK, _ := got.Deps["postgres"]["ok"].(bool); pgOK {
		t.Error("postgres should not be ok")
	}
	if msg, _ := got.Deps["postgres"]["error"].(string); msg == "" {
		t.Error("postgres error message should be present")
	}
}

func TestReadiness_NATSDown_503(t *testing.T) {
	h := readinessHandlerFor(
		func() (bool, string) { return false, "not connected" },
		func(_ context.Context) (bool, string) { return true, "" },
	)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/health/ready", nil))
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", w.Code)
	}
}

func TestReadiness_BothDown_503(t *testing.T) {
	h := readinessHandlerFor(
		func() (bool, string) { return false, "nats gone" },
		func(_ context.Context) (bool, string) { return false, "pg gone" },
	)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/health/ready", nil))
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", w.Code)
	}
	var got struct {
		Deps map[string]map[string]any `json:"deps"`
	}
	if err := json.NewDecoder(w.Body).Decode(&got); err != nil {
		t.Fatalf("decode: %v", err)
	}
	for _, name := range []string{"nats", "postgres"} {
		if ok, _ := got.Deps[name]["ok"].(bool); ok {
			t.Errorf("dep %s reported ok but should be down", name)
		}
	}
}
