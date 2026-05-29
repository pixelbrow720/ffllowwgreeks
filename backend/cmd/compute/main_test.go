package main

import (
	"testing"

	"flowgreeks/internal/dealer"
	"flowgreeks/internal/feed"
)

func TestTopStrikesByDealerPos(t *testing.T) {
	t.Parallel()

	rows := []dealer.StrikeRow{
		{Expiry: 20260213, Strike: 6900_000, Side: feed.SideCall, DealerPos: 5},
		{Expiry: 20260213, Strike: 7000_000, Side: feed.SideCall, DealerPos: -100},
		{Expiry: 20260213, Strike: 6950_000, Side: feed.SidePut, DealerPos: 50},
		{Expiry: 20260213, Strike: 6800_000, Side: feed.SideCall, DealerPos: -25},
		{Expiry: 20260213, Strike: 7100_000, Side: feed.SidePut, DealerPos: 75},
	}

	t.Run("n larger than len returns same slice", func(t *testing.T) {
		out := topStrikesByDealerPos(rows, 100)
		if len(out) != len(rows) {
			t.Fatalf("len = %d, want %d", len(out), len(rows))
		}
	})

	t.Run("zero or negative n returns same slice", func(t *testing.T) {
		out := topStrikesByDealerPos(rows, 0)
		if len(out) != len(rows) {
			t.Fatalf("zero: len = %d, want %d", len(out), len(rows))
		}
		out = topStrikesByDealerPos(rows, -3)
		if len(out) != len(rows) {
			t.Fatalf("negative: len = %d, want %d", len(out), len(rows))
		}
	})

	t.Run("trims to n highest |dealer_pos|", func(t *testing.T) {
		out := topStrikesByDealerPos(rows, 3)
		if len(out) != 3 {
			t.Fatalf("len = %d, want 3", len(out))
		}
		// Expected order by |DealerPos| desc: 100, 75, 50.
		got := []int64{out[0].DealerPos, out[1].DealerPos, out[2].DealerPos}
		want := []int64{-100, 75, 50}
		for i := range got {
			if got[i] != want[i] {
				t.Errorf("rank %d: DealerPos = %d, want %d", i, got[i], want[i])
			}
		}
	})

	t.Run("does not mutate source slice", func(t *testing.T) {
		input := make([]dealer.StrikeRow, len(rows))
		copy(input, rows)
		_ = topStrikesByDealerPos(input, 2)
		for i := range input {
			if input[i] != rows[i] {
				t.Errorf("source mutated at %d: got %+v, want %+v", i, input[i], rows[i])
			}
		}
	})

	t.Run("deterministic on |DealerPos| ties", func(t *testing.T) {
		ties := []dealer.StrikeRow{
			{Expiry: 20260213, Strike: 7000_000, Side: feed.SideCall, DealerPos: 10},
			{Expiry: 20260213, Strike: 6900_000, Side: feed.SidePut, DealerPos: -10},
			{Expiry: 20260213, Strike: 7000_000, Side: feed.SidePut, DealerPos: 10},
		}
		out := topStrikesByDealerPos(ties, 2)
		// Tie-break order: (Expiry asc, Strike asc, Side asc).
		// SideCall = 0, SidePut = 1.
		// Expected ranks: (6900 P), (7000 C).
		if out[0].Strike != 6900_000 || out[0].Side != feed.SidePut {
			t.Errorf("rank 0 = (%d, %v), want (6900000, P)", out[0].Strike, out[0].Side)
		}
		if out[1].Strike != 7000_000 || out[1].Side != feed.SideCall {
			t.Errorf("rank 1 = (%d, %v), want (7000000, C)", out[1].Strike, out[1].Side)
		}
	})
}
