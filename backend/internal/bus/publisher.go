package bus

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"math"
	"sync"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"

	"flowgreeks/internal/feed"
)

// EncodedTickSize is the on-wire size in bytes of a single feed.Tick produced
// by EncodeTick. The layout is fixed and little-endian; see encodeTickInto.
const EncodedTickSize = 90

// tickBufPool reuses 90-byte scratch buffers for the publish hot path so that
// encoding does not allocate per call. Buffers are returned after the
// synchronous JetStream publish completes (the data is consumed before the
// call returns).
var tickBufPool = sync.Pool{
	New: func() any {
		b := make([]byte, EncodedTickSize)
		return &b
	},
}

// EncodeTick serialises t into a freshly-allocated EncodedTickSize-byte
// buffer. Hot-path callers should prefer the publisher's pooled path.
func EncodeTick(t feed.Tick) []byte {
	b := make([]byte, EncodedTickSize)
	encodeTickInto(b, t)
	return b
}

// encodeTickInto writes t into b. b must be at least EncodedTickSize bytes.
func encodeTickInto(b []byte, t feed.Tick) {
	_ = b[EncodedTickSize-1] // bounds-check hint for the compiler
	le := binary.LittleEndian
	le.PutUint64(b[0:8], t.TsEvent)
	le.PutUint64(b[8:16], t.TsRecv)
	b[16] = byte(t.Symbol)
	b[17] = byte(t.AssetClass)
	b[18] = byte(t.TickType)
	b[19] = byte(t.Side)
	le.PutUint32(b[20:24], t.Expiry)
	le.PutUint32(b[24:28], t.Strike)
	copy(b[28:40], t.FuturesContract[:])
	le.PutUint64(b[40:48], math.Float64bits(t.Price))
	le.PutUint32(b[48:52], t.Size)
	b[52] = byte(t.Aggressor)
	b[53] = t.Exchange
	le.PutUint32(b[54:58], t.BidSize)
	le.PutUint32(b[58:62], t.AskSize)
	le.PutUint32(b[62:66], t.OpenInterest)
	le.PutUint64(b[66:74], math.Float64bits(t.Bid))
	le.PutUint64(b[74:82], math.Float64bits(t.Ask))
	le.PutUint64(b[82:90], t.InstrumentID)
}

// Decode parses a fixed-layout encoded Tick from b. b must contain at least
// EncodedTickSize bytes; trailing bytes are ignored so the same routine can
// read from a larger backing buffer.
func Decode(b []byte) (feed.Tick, error) {
	if len(b) < EncodedTickSize {
		return feed.Tick{}, fmt.Errorf("bus: decode tick: short buffer (%d bytes, need %d)", len(b), EncodedTickSize)
	}
	le := binary.LittleEndian
	t := feed.Tick{
		TsEvent:      le.Uint64(b[0:8]),
		TsRecv:       le.Uint64(b[8:16]),
		Symbol:       feed.Symbol(b[16]),
		AssetClass:   feed.AssetClass(b[17]),
		TickType:     feed.TickType(b[18]),
		Side:         feed.Side(b[19]),
		Expiry:       le.Uint32(b[20:24]),
		Strike:       le.Uint32(b[24:28]),
		Price:        math.Float64frombits(le.Uint64(b[40:48])),
		Size:         le.Uint32(b[48:52]),
		Aggressor:    feed.Aggressor(b[52]),
		Exchange:     b[53],
		BidSize:      le.Uint32(b[54:58]),
		AskSize:      le.Uint32(b[58:62]),
		OpenInterest: le.Uint32(b[62:66]),
		Bid:          math.Float64frombits(le.Uint64(b[66:74])),
		Ask:          math.Float64frombits(le.Uint64(b[74:82])),
		InstrumentID: le.Uint64(b[82:90]),
	}
	copy(t.FuturesContract[:], b[28:40])
	return t, nil
}

// Publisher wraps a NATS JetStream connection and publishes typed feed.Tick
// events onto the canonical subject hierarchy declared in subjects.go.
//
// The publisher owns its underlying nats.Conn — Close drains it. Construction
// is the only fatal point; per-publish errors are returned to the caller so
// upstream pipelines can decide how to react (drop, retry, alert).
type Publisher struct {
	nc *nats.Conn
	js jetstream.JetStream
}

// NewPublisher dials NATS at natsURL, ensures the TICKS stream exists with
// the configuration declared in docs/DATA_MODEL.md (24h retention, 10GB max,
// file storage), and returns a ready-to-use Publisher. ctx is used for the
// stream-creation API call.
func NewPublisher(ctx context.Context, natsURL string) (*Publisher, error) {
	nc, err := nats.Connect(natsURL,
		nats.Name("flowgreeks-bus-publisher"),
		nats.ReconnectWait(2*time.Second),
		nats.MaxReconnects(-1),
	)
	if err != nil {
		return nil, fmt.Errorf("bus: connect %q: %w", natsURL, err)
	}

	js, err := jetstream.New(nc)
	if err != nil {
		nc.Close()
		return nil, fmt.Errorf("bus: init jetstream: %w", err)
	}

	cfg := jetstream.StreamConfig{
		Name:      StreamTicks,
		Subjects:  []string{"ticks.>"},
		Retention: jetstream.LimitsPolicy,
		Discard:   jetstream.DiscardOld,
		MaxAge:    24 * time.Hour,
		MaxBytes:  10 * 1024 * 1024 * 1024,
		Storage:   jetstream.FileStorage,
	}
	if _, err := js.CreateStream(ctx, cfg); err != nil && !errors.Is(err, jetstream.ErrStreamNameAlreadyInUse) {
		nc.Close()
		return nil, fmt.Errorf("bus: create stream %q: %w", StreamTicks, err)
	}

	return &Publisher{nc: nc, js: js}, nil
}

// Publish encodes t and publishes it synchronously to the subject derived
// from its asset class and tick type. Returns an error for unsupported
// tick types or when the JetStream publish fails.
func (p *Publisher) Publish(ctx context.Context, t feed.Tick) error {
	subject, err := p.subjectFor(t)
	if err != nil {
		return err
	}

	bufp := tickBufPool.Get().(*[]byte)
	buf := *bufp
	encodeTickInto(buf, t)

	_, pubErr := p.js.Publish(ctx, subject, buf)
	tickBufPool.Put(bufp)

	if pubErr != nil {
		return fmt.Errorf("bus: publish %s: %w", subject, pubErr)
	}
	return nil
}

// Close drains in-flight publishes and closes the underlying NATS connection.
// Subsequent calls are no-ops.
func (p *Publisher) Close() error {
	if p == nil || p.nc == nil {
		return nil
	}
	nc := p.nc
	p.nc = nil
	if err := nc.Drain(); err != nil {
		return fmt.Errorf("bus: drain: %w", err)
	}
	return nil
}

// subjectFor maps a tick to its NATS subject. Splits on AssetClass first
// (futures vs options) then on TickType for options.
func (p *Publisher) subjectFor(t feed.Tick) (string, error) {
	switch t.AssetClass {
	case feed.AssetClassFuture:
		contract := trimNullBytes(t.FuturesContract[:])
		if contract == "" {
			return "", fmt.Errorf("bus: publish: future tick missing FuturesContract")
		}
		return SubjectFutureTick(t.Symbol, contract), nil

	case feed.AssetClassOption:
		switch t.TickType {
		case feed.TickTypeQuote:
			return SubjectTickQuote(t.Symbol, t.Expiry, t.Strike, t.Side), nil
		case feed.TickTypeTrade:
			return SubjectTickTrade(t.Symbol, t.Expiry, t.Strike, t.Side), nil
		}
	}
	return "", fmt.Errorf("bus: publish: unsupported tick type %d (asset class %d)", t.TickType, t.AssetClass)
}

// trimNullBytes returns b up to the first NUL byte as a string. Used to
// recover the printable contract code from the fixed-size FuturesContract
// array.
func trimNullBytes(b []byte) string {
	for i, c := range b {
		if c == 0 {
			return string(b[:i])
		}
	}
	return string(b)
}
