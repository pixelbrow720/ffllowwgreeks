// Bootstrap pre-populates the OPRA instrument registry from the Databento
// Historical API before the live stream starts.
//
// Why: OPRA's live gateway does NOT broadcast SymbolMappingMsg for parent
// symbology subscriptions (unlike GLBX). It only emits Cmbp1Msg / Mbp0Msg
// records carrying instrument_id, expecting the client to already know
// the (expiry, strike, side) for each id. Without bootstrap every live
// record is dropped because the visitor's meta cache is empty.
//
// The Python reference implementation (databento_live.py
// `_bootstrap_registry`) uses the same pattern: pull definition schema
// for the previous trading day's parent symbols, build instrument_id ->
// metadata map, then start the live stream.

package databento

import (
	"bytes"
	"fmt"
	"strings"
	"time"

	"flowgreeks/internal/feed"

	dbn "github.com/NimbleMarkets/dbn-go"
	dbn_hist "github.com/NimbleMarkets/dbn-go/hist"
)

// bootstrapDataset fetches the last 48h of definition records for the
// given parent symbols and returns the instrument_id -> instrumentMeta
// mapping.
//
// Returns an empty map (no error) if the dataset returns no rows for the
// requested window — useful for fresh weekend deployments where the most
// recent trading day is more than a day stale.
func bootstrapDataset(apiKey string, dataset feed.Dataset, parentSymbols []string) (map[uint32]instrumentMeta, error) {
	if apiKey == "" {
		return nil, fmt.Errorf("bootstrap %s: empty api key", dataset)
	}
	if len(parentSymbols) == 0 {
		return nil, nil
	}

	// Half-closed range: end is exclusive. We pull the last 48 hours so
	// weekend / holiday rolls still pick up Friday's session.
	end := time.Now().UTC().Add(-30 * time.Minute)
	start := end.Add(-48 * time.Hour)

	params := dbn_hist.SubmitJobParams{
		Dataset:     string(dataset),
		Symbols:     strings.Join(parentSymbols, ","),
		Schema:      dbn.Schema_Definition,
		DateRange:   dbn_hist.DateRange{Start: start, End: end},
		StypeIn:     dbn.SType_Parent,
		StypeOut:    dbn.SType_InstrumentId,
		Encoding:    dbn.Encoding_Dbn,
		Compression: dbn.Compress_None,
	}

	data, err := dbn_hist.GetRange(apiKey, params)
	if err != nil {
		return nil, fmt.Errorf("bootstrap %s GetRange: %w", dataset, err)
	}
	if len(data) == 0 {
		return map[uint32]instrumentMeta{}, nil
	}

	out := make(map[uint32]instrumentMeta, 4096)
	scanner := dbn.NewDbnScanner(bytes.NewReader(data))
	for scanner.Next() {
		header, err := scanner.GetLastHeader()
		if err != nil {
			continue
		}
		if header.RType != dbn.RType_InstrumentDef {
			continue
		}
		def, err := scanner.DecodeInstrumentDefMsg()
		if err != nil {
			continue
		}
		raw := dbnFixedString(def.RawSymbol[:])
		m, ok := resolveSymbol(raw)
		if !ok {
			continue
		}
		out[def.Header.InstrumentID] = m
	}
	if err := scanner.Error(); err != nil {
		return nil, fmt.Errorf("bootstrap %s scan: %w", dataset, err)
	}
	return out, nil
}

// dbnFixedString trims a fixed-size DBN byte buffer to the first NUL.
func dbnFixedString(b []byte) string {
	if i := bytes.IndexByte(b, 0); i >= 0 {
		return string(b[:i])
	}
	return string(b)
}
