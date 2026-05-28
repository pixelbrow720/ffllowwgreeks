// Package apikey provides API-key authentication for the FlowGreeks
// REST + WebSocket surface.
//
// Auth model: FlowGreeks runs as an add-on inside flowjob.id. The
// parent site owns user accounts, billing, and add-on activation.
// When a user enables the FlowGreeks add-on, the parent site provisions
// an API key on their behalf and hands it to the client. From this
// binary's perspective there is no signup, no password, no refresh
// token, no per-account lockout — just opaque API keys with a hash
// stored at rest, a per-key rate limit, and revoke-on-demand.
//
// Secret storage: we never store the raw secret. Provision flow returns
// the secret once at creation time; only its SHA-256 digest persists.
// A snapshot of the api_keys table is therefore useless on its own.
//
// Wire format: clients send the secret either as a Bearer token
// (Authorization: Bearer <secret>) or as X-API-Key. Both paths land
// at the same Middleware which looks the hash up, verifies the row is
// not revoked / not expired, and attaches the resolved APIKey to the
// request context.
package apikey

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"time"
)

// LookupTimeout caps DB calls from the middleware. Authentication is on
// the hot path of every protected request, so the budget is tight.
const LookupTimeout = 2 * time.Second

// SecretBytes is the length of a freshly minted API-key secret in
// bytes. 32 bytes → 64 hex chars after encoding; collision probability
// across the entire fleet is negligible.
const SecretBytes = 32

// APIKey is the persisted record. Hash is the SHA-256 of the secret;
// the secret itself only ever exists in client memory after Generate
// returns it.
type APIKey struct {
	ID            int64
	Name          string
	Hash          []byte
	ParentUserID  string
	RateLimitRPS  float64
	RateBurst     int
	RevokedAt     *time.Time
	CreatedAt     time.Time
	LastUsedAt    *time.Time
	ExpiresAt     *time.Time
}

// IsActive reports whether the key may authenticate at `now`. Both
// conditions must hold: not revoked AND not past expiry (when set).
func (k APIKey) IsActive(now time.Time) bool {
	if k.RevokedAt != nil {
		return false
	}
	if k.ExpiresAt != nil && !now.Before(*k.ExpiresAt) {
		return false
	}
	return true
}

// Errors surfaced by the auth surface. Middleware translates these
// into HTTP 401 / 403; tests assert on identity.
var (
	ErrNoCredentials  = errors.New("apikey: no credentials presented")
	ErrUnknownKey     = errors.New("apikey: unknown key")
	ErrRevokedKey     = errors.New("apikey: key revoked")
	ErrExpiredKey     = errors.New("apikey: key expired")
	ErrLookupFailed   = errors.New("apikey: lookup failed")
	ErrTooMany        = errors.New("apikey: rate limit exceeded")
)

// Generate mints a fresh secret and returns (secret, hash). The caller
// hands the secret to the client (one-shot) and stores the hash.
func Generate() (secret string, hash []byte, err error) {
	var b [SecretBytes]byte
	if _, err = rand.Read(b[:]); err != nil {
		return "", nil, err
	}
	secret = hex.EncodeToString(b[:])
	hash = HashSecret(secret)
	return
}

// HashSecret returns the SHA-256 digest of secret. Used by Generate
// when minting and by middleware on every auth lookup.
func HashSecret(secret string) []byte {
	h := sha256.Sum256([]byte(secret))
	return h[:]
}

// ctxKey is the context key under which the resolved APIKey lives.
type ctxKey struct{}

// FromContext returns the resolved APIKey installed by Middleware.
// Returns ok=false when the request did not pass through Middleware
// or auth was not enabled.
func FromContext(ctx context.Context) (APIKey, bool) {
	v := ctx.Value(ctxKey{})
	if v == nil {
		return APIKey{}, false
	}
	k, ok := v.(APIKey)
	return k, ok
}

// withAPIKey installs k onto ctx so downstream handlers can read it
// via FromContext.
func withAPIKey(ctx context.Context, k APIKey) context.Context {
	return context.WithValue(ctx, ctxKey{}, k)
}
