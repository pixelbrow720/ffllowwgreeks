// Package feed (continued) — symbol helpers.
package feed

import (
	"strconv"
	"strings"
)

// EncodeStrike packs a strike price float into our uint32 representation
// (multiplied by 1000). Returns 0 if price is negative or NaN.
func EncodeStrike(price float64) uint32 {
	if price <= 0 {
		return 0
	}
	return uint32(price*1000 + 0.5) // round to nearest
}

// DecodeStrike unpacks our uint32 strike representation back to float.
func DecodeStrike(s uint32) float64 {
	return float64(s) / 1000.0
}

// EncodeExpiry packs a YYYYMMDD date string ("2026-06-20" or "20260620")
// into our uint32 representation. Returns 0 on parse failure.
func EncodeExpiry(s string) uint32 {
	s = strings.ReplaceAll(s, "-", "")
	if len(s) != 8 {
		return 0
	}
	v, err := strconv.ParseUint(s, 10, 32)
	if err != nil {
		return 0
	}
	return uint32(v)
}

// EncodeFuturesContract packs a contract symbol ("ESM6", "NQU6") into our
// fixed [12]byte buffer. Truncates if longer than 12 bytes.
func EncodeFuturesContract(sym string) [12]byte {
	var b [12]byte
	copy(b[:], sym)
	return b
}

// DecodeFuturesContract unpacks our fixed buffer back to string, trimming
// trailing zero bytes.
func DecodeFuturesContract(b [12]byte) string {
	end := len(b)
	for i, c := range b {
		if c == 0 {
			end = i
			break
		}
	}
	return string(b[:end])
}
