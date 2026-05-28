package greeks

import (
	"math"
	"testing"
)

func TestPhi_KnownValues(t *testing.T) {
	// Reference values: Φ at canonical points, accurate to 1e-9.
	cases := []struct {
		x, want float64
	}{
		{0, 0.5},
		{1, 0.8413447460685429},
		{-1, 0.15865525393145707},
		{1.96, 0.9750021048517795},
		{-1.96, 0.024997895148220435},
		{2.5758293, 0.99500000017},
		{0.35, 0.6368306511756191},
	}
	for _, c := range cases {
		got := phi(c.x)
		if math.Abs(got-c.want) > 1e-9 {
			t.Errorf("phi(%v) = %.12f, want %.12f", c.x, got, c.want)
		}
	}
}

func TestPhi_Symmetry(t *testing.T) {
	for _, x := range []float64{0.1, 0.5, 1, 2, 3, 5} {
		if d := phi(x) + phi(-x) - 1; math.Abs(d) > 1e-12 {
			t.Errorf("phi(%v)+phi(-%v) = 1+%.2e", x, x, d)
		}
	}
}

func TestPhid_KnownValues(t *testing.T) {
	cases := []struct {
		x, want float64
	}{
		{0, 0.3989422804014327},
		{1, 0.24197072451914337},
		{-1, 0.24197072451914337},
		{2, 0.05399096651318806},
	}
	for _, c := range cases {
		got := phid(c.x)
		if math.Abs(got-c.want) > 1e-12 {
			t.Errorf("phid(%v) = %.12f, want %.12f", c.x, got, c.want)
		}
	}
}

func TestClampTail(t *testing.T) {
	if clampTail(10) != 8 {
		t.Errorf("clampTail(10) != 8")
	}
	if clampTail(-10) != -8 {
		t.Errorf("clampTail(-10) != -8")
	}
	if clampTail(3.5) != 3.5 {
		t.Errorf("clampTail(3.5) != 3.5")
	}
}

func BenchmarkPhi(b *testing.B) {
	for i := 0; i < b.N; i++ {
		_ = phi(0.35)
	}
}

func BenchmarkPhid(b *testing.B) {
	for i := 0; i < b.N; i++ {
		_ = phid(0.35)
	}
}
