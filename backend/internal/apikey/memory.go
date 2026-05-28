package apikey

import (
	"context"
	"sync"
	"time"
)

// MemoryStore is an in-memory Store for tests.
type MemoryStore struct {
	mu     sync.Mutex
	rows   []APIKey
	nextID int64
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{nextID: 1}
}

func (s *MemoryStore) LookupByHash(_ context.Context, hash []byte) (APIKey, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, k := range s.rows {
		if equalBytes(k.Hash, hash) {
			return k, nil
		}
	}
	return APIKey{}, ErrUnknownKey
}

func (s *MemoryStore) Create(_ context.Context, k APIKey) (APIKey, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	id := s.nextID
	s.nextID++
	if k.RateLimitRPS <= 0 {
		k.RateLimitRPS = 1.0
	}
	if k.RateBurst <= 0 {
		k.RateBurst = 30
	}
	k.ID = id
	k.CreatedAt = time.Now()
	k.Hash = append([]byte(nil), k.Hash...)
	s.rows = append(s.rows, k)
	return k, nil
}

func (s *MemoryStore) Revoke(_ context.Context, id int64) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	for i := range s.rows {
		if s.rows[i].ID == id && s.rows[i].RevokedAt == nil {
			now := time.Now()
			s.rows[i].RevokedAt = &now
		}
	}
	return nil
}

func (s *MemoryStore) TouchLastUsed(_ context.Context, id int64) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	for i := range s.rows {
		if s.rows[i].ID == id {
			now := time.Now()
			s.rows[i].LastUsedAt = &now
			return nil
		}
	}
	return nil
}

func equalBytes(a, b []byte) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
