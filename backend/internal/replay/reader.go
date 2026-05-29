// Package replay reads historical ticks from TimescaleDB and re-emits
// them into NATS at a configurable wall-clock speed. The downstream
// compute service consumes them through the same `ticks.<symbol>.>`
// subject hierarchy as live ingest, so M2/M3 components can be exercised
// against real historical data without needing the live vendor gateway.
package replay

import (
	"context"
	"fmt"
	"time"

	"flowgreeks/internal/feed"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Reader streams Tick rows out of Postgres for the requested symbol +
// time range, ordered by ts ascending. Backed by a single pgx query that
// returns its rows lazily — the caller paces consumption.
type Reader struct {
	pool *pgxpool.Pool
}

func NewReader(pool *pgxpool.Pool) *Reader {
	return &Reader{pool: pool}
}

// Range describes a replay window for one symbol.
type Range struct {
	Symbol feed.Symbol
	Start  time.Time
	End    time.Time
}

// Stream opens a server-side cursor and emits decoded ticks via the
// returned channel. The channel is closed when the result set is
// exhausted or ctx is cancelled. Errors land on the errs channel; one
// fatal error closes both channels.
//
// Tick ordering is by ts ascending; ties broken by recv_ts. Caller is
// responsible for pacing (see Runner).
func (r *Reader) Stream(ctx context.Context, rng Range) (<-chan feed.Tick, <-chan error) {
	out := make(chan feed.Tick, 4096)
	errs := make(chan error, 1)

	go func() {
		defer close(out)
		defer close(errs)

		const sql = `
			SELECT ts, recv_ts, symbol, expiry, strike, side, tick_type,
			       price, size, bid, ask, bid_size, ask_size, open_interest,
			       aggressor, exchange, instrument_id
			FROM ticks
			WHERE symbol = $1
			  AND ts >= $2
			  AND ts <  $3
			ORDER BY ts ASC, recv_ts ASC
		`
		rows, err := r.pool.Query(ctx, sql, int16(rng.Symbol), rng.Start, rng.End)
		if err != nil {
			errs <- fmt.Errorf("replay query: %w", err)
			return
		}
		defer rows.Close()

		for rows.Next() {
			t, err := scanTick(rows)
			if err != nil {
				errs <- fmt.Errorf("replay scan: %w", err)
				return
			}
			select {
			case <-ctx.Done():
				return
			case out <- t:
			}
		}
		if err := rows.Err(); err != nil {
			errs <- fmt.Errorf("replay rows: %w", err)
		}
	}()

	return out, errs
}

// scanTick decodes one row into a feed.Tick. NULLable columns (expiry,
// strike, side, ...) are handled via pgx scan targets that accept zeros.
func scanTick(rows pgx.Rows) (feed.Tick, error) {
	var (
		ts, recvTs    time.Time
		sym, ttype    int16
		expiry        *time.Time
		strike        *int32
		side          *int16
		price, bid, ask *float64
		size          *int32
		bidSize, askSize *int32
		oi            *int32
		aggr, exch    *int16
		instrumentID  *int64
	)
	if err := rows.Scan(&ts, &recvTs, &sym, &expiry, &strike, &side, &ttype,
		&price, &size, &bid, &ask, &bidSize, &askSize, &oi, &aggr, &exch, &instrumentID); err != nil {
		return feed.Tick{}, err
	}

	t := feed.Tick{
		TsEvent:  uint64(ts.UnixNano()),
		TsRecv:   uint64(recvTs.UnixNano()),
		Symbol:   feed.Symbol(sym),
		TickType: feed.TickType(ttype),
	}

	switch {
	case expiry == nil:
		t.AssetClass = feed.AssetClassFuture
		// The ticks hypertable doesn't carry futures_contract; reconstruct
		// the front-month CME symbol from (sym, ts) so the bus publisher
		// can route the tick (it rejects futures ticks with empty
		// FuturesContract). Convention: SPX→ES, NDX→NQ, front month = next
		// quarterly H/M/U/Z whose third-Friday expiry is after ts.
		if contract := FrontMonthContract(t.Symbol, ts); contract != "" {
			copy(t.FuturesContract[:], contract)
		}
	default:
		t.AssetClass = feed.AssetClassOption
		y, m, d := expiry.Date()
		t.Expiry = uint32(y*10000 + int(m)*100 + d)
	}
	if strike != nil {
		t.Strike = uint32(*strike)
	}
	if side != nil {
		t.Side = feed.Side(*side)
	}
	if price != nil {
		t.Price = *price
	}
	if size != nil {
		t.Size = uint32(*size)
	}
	if bid != nil {
		t.Bid = *bid
	}
	if ask != nil {
		t.Ask = *ask
	}
	if bidSize != nil {
		t.BidSize = uint32(*bidSize)
	}
	if askSize != nil {
		t.AskSize = uint32(*askSize)
	}
	if oi != nil {
		t.OpenInterest = uint32(*oi)
	}
	if aggr != nil {
		t.Aggressor = feed.Aggressor(*aggr)
	}
	if exch != nil {
		t.Exchange = uint8(*exch)
	}
	if instrumentID != nil {
		t.InstrumentID = uint64(*instrumentID)
	}
	return t, nil
}
