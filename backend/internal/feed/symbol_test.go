package feed

import "testing"

func TestEncodeDecodeStrike(t *testing.T) {
	cases := []struct {
		in   float64
		want uint32
	}{
		{5810.0, 5810000},
		{5810.5, 5810500},
		{5810.25, 5810250},
		{0, 0},
		{-1, 0},
	}
	for _, c := range cases {
		got := EncodeStrike(c.in)
		if got != c.want {
			t.Errorf("EncodeStrike(%v) = %d, want %d", c.in, got, c.want)
		}
		if c.want != 0 {
			back := DecodeStrike(got)
			if back != c.in {
				t.Errorf("round-trip mismatch: in=%v back=%v", c.in, back)
			}
		}
	}
}

func TestEncodeExpiry(t *testing.T) {
	cases := []struct {
		in   string
		want uint32
	}{
		{"2026-06-20", 20260620},
		{"20260620", 20260620},
		{"bad", 0},
		{"", 0},
	}
	for _, c := range cases {
		got := EncodeExpiry(c.in)
		if got != c.want {
			t.Errorf("EncodeExpiry(%q) = %d, want %d", c.in, got, c.want)
		}
	}
}

func TestEncodeDecodeFuturesContract(t *testing.T) {
	cases := []string{"ESM6", "NQU6", "ES2026M"}
	for _, c := range cases {
		b := EncodeFuturesContract(c)
		got := DecodeFuturesContract(b)
		if got != c {
			t.Errorf("round-trip mismatch: in=%q out=%q", c, got)
		}
	}
}

func TestParseSymbol(t *testing.T) {
	cases := []struct {
		in   string
		want Symbol
	}{
		{"SPX", SymbolSPX},
		{"SPXW", SymbolSPX},
		{"NDX", SymbolNDX},
		{"NDXP", SymbolNDX},
		{"AAPL", SymbolUnknown},
		{"", SymbolUnknown},
	}
	for _, c := range cases {
		got := ParseSymbol(c.in)
		if got != c.want {
			t.Errorf("ParseSymbol(%q) = %v, want %v", c.in, got, c.want)
		}
	}
}
