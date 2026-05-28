package greeks

import "math"

// sqrt2 is √2, used by phi via the erf identity.
const sqrt2 = 1.4142135623730951

// invSqrt2Pi is 1/√(2π), the standard-normal pdf normalizer.
const invSqrt2Pi = 0.3989422804014327

// clampTail bounds an argument to [-8, 8] before phi/phid evaluation. The
// standard-normal cdf at |x|=8 is within ~6e-16 of {0,1}, so clamping
// preserves IEEE-754 representable precision while avoiding subnormal /
// extreme-tail anomalies in the solver. See COMPUTE_MODEL.md §10.
func clampTail(x float64) float64 {
	if x > 8 {
		return 8
	}
	if x < -8 {
		return -8
	}
	return x
}

// phi is the standard normal cumulative distribution function. Computed via
// the erf identity Φ(x) = ½(1 + erf(x/√2)). math.Erf is implemented in the
// Go runtime via Cody-style rational approximation; on amd64 this is a few
// nanoseconds and faster than maintaining a custom table.
func phi(x float64) float64 {
	return 0.5 * (1 + math.Erf(clampTail(x)/sqrt2))
}

// phid is the standard normal probability density function.
func phid(x float64) float64 {
	xc := clampTail(x)
	return invSqrt2Pi * math.Exp(-0.5*xc*xc)
}
