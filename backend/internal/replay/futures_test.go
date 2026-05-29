package replay

import (
	"testing"
	"time"

	"flowgreeks/internal/feed"
)

func TestFrontMonthContract(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name string
		sym  feed.Symbol
		ts   time.Time
		want string
	}{
		// 2026 quarterly third Fridays:
		//   Mar 20 (H), Jun 19 (M), Sep 18 (U), Dec 18 (Z)
		{
			name: "spx mid-feb -> ESH6",
			sym:  feed.SymbolSPX,
			ts:   time.Date(2026, 2, 12, 14, 30, 0, 0, time.UTC),
			want: "ESH6",
		},
		{
			name: "ndx mid-feb -> NQH6",
			sym:  feed.SymbolNDX,
			ts:   time.Date(2026, 2, 12, 14, 30, 0, 0, time.UTC),
			want: "NQH6",
		},
		{
			name: "spx day before march expiry -> ESH6",
			sym:  feed.SymbolSPX,
			ts:   time.Date(2026, 3, 19, 23, 59, 0, 0, time.UTC),
			want: "ESH6",
		},
		{
			name: "spx on march expiry day -> ESM6 (rollover)",
			sym:  feed.SymbolSPX,
			ts:   time.Date(2026, 3, 20, 12, 0, 0, 0, time.UTC),
			want: "ESM6",
		},
		{
			name: "spx april -> ESM6",
			sym:  feed.SymbolSPX,
			ts:   time.Date(2026, 4, 1, 0, 0, 0, 0, time.UTC),
			want: "ESM6",
		},
		{
			name: "spx august -> ESU6",
			sym:  feed.SymbolSPX,
			ts:   time.Date(2026, 8, 15, 0, 0, 0, 0, time.UTC),
			want: "ESU6",
		},
		{
			name: "spx december-after-expiry -> ESH7 (year rollover)",
			sym:  feed.SymbolSPX,
			ts:   time.Date(2026, 12, 31, 23, 59, 0, 0, time.UTC),
			want: "ESH7",
		},
		{
			name: "spx january 2027 -> ESH7",
			sym:  feed.SymbolSPX,
			ts:   time.Date(2027, 1, 5, 0, 0, 0, 0, time.UTC),
			want: "ESH7",
		},
		{
			name: "unknown symbol -> empty",
			sym:  feed.SymbolUnknown,
			ts:   time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC),
			want: "",
		},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := FrontMonthContract(c.sym, c.ts)
			if got != c.want {
				t.Errorf("FrontMonthContract(%s, %s) = %q, want %q",
					c.sym, c.ts.Format(time.RFC3339), got, c.want)
			}
		})
	}
}

func TestThirdFridayUTC(t *testing.T) {
	t.Parallel()

	cases := []struct {
		year  int
		month time.Month
		want  time.Time
	}{
		{2026, time.March, time.Date(2026, 3, 20, 0, 0, 0, 0, time.UTC)},
		{2026, time.June, time.Date(2026, 6, 19, 0, 0, 0, 0, time.UTC)},
		{2026, time.September, time.Date(2026, 9, 18, 0, 0, 0, 0, time.UTC)},
		{2026, time.December, time.Date(2026, 12, 18, 0, 0, 0, 0, time.UTC)},
		{2027, time.March, time.Date(2027, 3, 19, 0, 0, 0, 0, time.UTC)},
	}
	for _, c := range cases {
		got := thirdFridayUTC(c.year, c.month)
		if !got.Equal(c.want) {
			t.Errorf("thirdFridayUTC(%d, %s) = %s, want %s",
				c.year, c.month, got.Format(time.RFC3339), c.want.Format(time.RFC3339))
		}
	}
}
