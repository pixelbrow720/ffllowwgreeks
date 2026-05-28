package bus

import (
	"reflect"
	"testing"

	"flowgreeks/internal/feed"
)

func TestEncodeDecodeRoundTripOption(t *testing.T) {
	t.Parallel()
	in := feed.Tick{
		TsEvent:      1716640328123_000_000,
		TsRecv:       1716640328123_500_000,
		Symbol:       feed.SymbolSPX,
		AssetClass:   feed.AssetClassOption,
		TickType:     feed.TickTypeQuote,
		Expiry:       20260620,
		Strike:       5810500,
		Side:         feed.SideCall,
		Price:        12.345,
		Size:         42,
		Aggressor:    feed.AggressorBuy,
		Bid:          12.30,
		Ask:          12.40,
		BidSize:      111,
		AskSize:      222,
		OpenInterest: 9999,
		Exchange:     7,
		InstrumentID: 0xDEADBEEFCAFEBABE,
	}

	enc := EncodeTick(in)
	if got, want := len(enc), EncodedTickSize; got != want {
		t.Fatalf("encoded length = %d, want %d", got, want)
	}

	out, err := Decode(enc)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if !reflect.DeepEqual(in, out) {
		t.Fatalf("round-trip mismatch:\n in: %#v\nout: %#v", in, out)
	}
}

func TestEncodeDecodeRoundTripFuture(t *testing.T) {
	t.Parallel()
	in := feed.Tick{
		TsEvent:      1716640400000_000_000,
		TsRecv:       1716640400001_000_000,
		Symbol:       feed.SymbolNDX,
		AssetClass:   feed.AssetClassFuture,
		TickType:     feed.TickTypeTrade,
		Price:        20415.75,
		Size:         3,
		Aggressor:    feed.AggressorSell,
		Bid:          20415.50,
		Ask:          20416.00,
		BidSize:      4,
		AskSize:      6,
		Exchange:     1,
		InstrumentID: 4242,
	}
	copy(in.FuturesContract[:], "NQM6")

	enc := EncodeTick(in)
	out, err := Decode(enc)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if !reflect.DeepEqual(in, out) {
		t.Fatalf("round-trip mismatch:\n in: %#v\nout: %#v", in, out)
	}
}

func TestDecodeShortBuffer(t *testing.T) {
	t.Parallel()
	if _, err := Decode(make([]byte, EncodedTickSize-1)); err == nil {
		t.Fatal("expected error on short buffer, got nil")
	}
}

func TestSubjectFor(t *testing.T) {
	t.Parallel()
	p := &Publisher{}

	cases := []struct {
		name    string
		tick    feed.Tick
		want    string
		wantErr bool
	}{
		{
			name: "option quote",
			tick: feed.Tick{
				Symbol:     feed.SymbolSPX,
				AssetClass: feed.AssetClassOption,
				TickType:   feed.TickTypeQuote,
				Expiry:     20260620,
				Strike:     5810000,
				Side:       feed.SideCall,
			},
			want: "ticks.spx.quote.20260620.5810000.C",
		},
		{
			name: "option trade",
			tick: feed.Tick{
				Symbol:     feed.SymbolNDX,
				AssetClass: feed.AssetClassOption,
				TickType:   feed.TickTypeTrade,
				Expiry:     20260620,
				Strike:     20400000,
				Side:       feed.SidePut,
			},
			want: "ticks.ndx.trade.20260620.20400000.P",
		},
		{
			name: "future tick",
			tick: futureTick(feed.SymbolSPX, "ESM6"),
			want: "ticks.spx.future.ESM6",
		},
		{
			name: "option oi unsupported",
			tick: feed.Tick{
				Symbol:     feed.SymbolSPX,
				AssetClass: feed.AssetClassOption,
				TickType:   feed.TickTypeOI,
			},
			wantErr: true,
		},
		{
			name: "unknown asset class",
			tick: feed.Tick{
				Symbol:     feed.SymbolSPX,
				AssetClass: feed.AssetClassUnknown,
				TickType:   feed.TickTypeQuote,
			},
			wantErr: true,
		},
		{
			name: "future missing contract",
			tick: feed.Tick{
				Symbol:     feed.SymbolSPX,
				AssetClass: feed.AssetClassFuture,
				TickType:   feed.TickTypeTrade,
			},
			wantErr: true,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := p.subjectFor(tc.tick)
			if tc.wantErr {
				if err == nil {
					t.Fatalf("expected error, got subject %q", got)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if got != tc.want {
				t.Fatalf("subject = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestTrimNullBytes(t *testing.T) {
	t.Parallel()
	cases := []struct {
		in   []byte
		want string
	}{
		{[]byte("ESM6\x00\x00\x00\x00"), "ESM6"},
		{[]byte("NQM6"), "NQM6"},
		{[]byte("\x00\x00\x00\x00"), ""},
		{[]byte{}, ""},
	}
	for _, tc := range cases {
		if got := trimNullBytes(tc.in); got != tc.want {
			t.Fatalf("trimNullBytes(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func futureTick(sym feed.Symbol, contract string) feed.Tick {
	t := feed.Tick{
		Symbol:     sym,
		AssetClass: feed.AssetClassFuture,
		TickType:   feed.TickTypeTrade,
	}
	copy(t.FuturesContract[:], contract)
	return t
}

func BenchmarkEncodeTickInto(b *testing.B) {
	tick := feed.Tick{
		TsEvent:    1716640328123_000_000,
		TsRecv:     1716640328123_500_000,
		Symbol:     feed.SymbolSPX,
		AssetClass: feed.AssetClassOption,
		TickType:   feed.TickTypeQuote,
		Expiry:     20260620,
		Strike:     5810500,
		Side:       feed.SideCall,
		Bid:        12.30,
		Ask:        12.40,
		BidSize:    111,
		AskSize:    222,
	}
	buf := make([]byte, EncodedTickSize)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		encodeTickInto(buf, tick)
	}
}
