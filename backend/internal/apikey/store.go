package apikey

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Store is the persistence interface the auth surface depends on.
// PgStore is the production implementation; MemoryStore covers tests.
type Store interface {
	// LookupByHash returns the row whose key_hash matches. Returns
	// ErrUnknownKey when no row exists. Active and revoked rows both
	// return successfully — caller decides what to do via APIKey.IsActive.
	LookupByHash(ctx context.Context, hash []byte) (APIKey, error)

	// Create inserts a fresh key record. The caller is responsible for
	// minting the secret + hash via Generate; the secret never reaches
	// the store layer.
	Create(ctx context.Context, k APIKey) (APIKey, error)

	// Revoke marks a key revoked. Idempotent.
	Revoke(ctx context.Context, id int64) error

	// TouchLastUsed updates last_used_at to now. Best-effort; failure
	// must never block the auth path.
	TouchLastUsed(ctx context.Context, id int64) error
}

// PgStore is a pgxpool-backed implementation against the api_keys
// table created by migration 0008.
type PgStore struct {
	Pool *pgxpool.Pool
}

func NewPgStore(p *pgxpool.Pool) *PgStore { return &PgStore{Pool: p} }

func (s *PgStore) LookupByHash(ctx context.Context, hash []byte) (APIKey, error) {
	const q = `
		SELECT id, name, key_hash, parent_user_id,
		       rate_limit_rps, rate_burst,
		       revoked_at, created_at, last_used_at, expires_at
		FROM api_keys
		WHERE key_hash = $1
	`
	var k APIKey
	var parent *string
	err := s.Pool.QueryRow(ctx, q, hash).Scan(
		&k.ID, &k.Name, &k.Hash, &parent,
		&k.RateLimitRPS, &k.RateBurst,
		&k.RevokedAt, &k.CreatedAt, &k.LastUsedAt, &k.ExpiresAt,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return APIKey{}, ErrUnknownKey
		}
		return APIKey{}, fmt.Errorf("apikey lookup: %w", err)
	}
	if parent != nil {
		k.ParentUserID = *parent
	}
	return k, nil
}

func (s *PgStore) Create(ctx context.Context, k APIKey) (APIKey, error) {
	const q = `
		INSERT INTO api_keys (name, key_hash, parent_user_id,
		                      rate_limit_rps, rate_burst, expires_at)
		VALUES ($1, $2, $3, $4, $5, $6)
		RETURNING id, created_at
	`
	var parent *string
	if k.ParentUserID != "" {
		s := k.ParentUserID
		parent = &s
	}
	rateRPS := k.RateLimitRPS
	if rateRPS <= 0 {
		rateRPS = 1.0
	}
	burst := k.RateBurst
	if burst <= 0 {
		burst = 30
	}
	if err := s.Pool.QueryRow(ctx, q,
		k.Name, k.Hash, parent, rateRPS, burst, k.ExpiresAt,
	).Scan(&k.ID, &k.CreatedAt); err != nil {
		return APIKey{}, fmt.Errorf("apikey create: %w", err)
	}
	k.RateLimitRPS = rateRPS
	k.RateBurst = burst
	return k, nil
}

func (s *PgStore) Revoke(ctx context.Context, id int64) error {
	const q = `UPDATE api_keys SET revoked_at = NOW() WHERE id = $1 AND revoked_at IS NULL`
	_, err := s.Pool.Exec(ctx, q, id)
	return err
}

func (s *PgStore) TouchLastUsed(ctx context.Context, id int64) error {
	const q = `UPDATE api_keys SET last_used_at = NOW() WHERE id = $1`
	_, err := s.Pool.Exec(ctx, q, id)
	return err
}

// touchInterval guards the LastUsed write so we don't hammer the DB
// on every request from a busy key. We coalesce to one update per
// minute per key — coarse enough that audit is happy, cheap enough
// that hot keys don't dominate Postgres write load.
const touchInterval = 1 * time.Minute

// shouldTouch reports whether last_used_at is stale enough to bump.
func shouldTouch(last *time.Time, now time.Time) bool {
	if last == nil {
		return true
	}
	return now.Sub(*last) > touchInterval
}
