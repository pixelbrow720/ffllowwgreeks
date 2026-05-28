// Package databento implements feed.Feed against the Databento Live API
// using NimbleMarkets/dbn-go.
//
// Each Databento dataset (OPRA.PILLAR, GLBX.MDP3, ...) requires its own TCP
// connection, so a single Client may hold multiple underlying LiveClients.
// Subscriptions are routed to the appropriate dataset connection; ticks from
// every connection are funneled into one channel for downstream consumers.
package databento

import (
	"context"
	"errors"
	"fmt"
	"io"
	"strings"
	"sync"
	"time"

	dbn "github.com/NimbleMarkets/dbn-go"
	dbn_live "github.com/NimbleMarkets/dbn-go/live"

	"flowgreeks/internal/feed"
)

// Config configures a Databento adapter.
//
// Gateway is reserved for future override; dbn-go derives its TCP gateway
// host from the dataset string (DatasetToHostname + ".lsg.databento.com"),
// so this field is currently informational.
type Config struct {
	APIKey     string
	Gateway    string // informational; dbn-go selects gateway per dataset
	BufferSize int    // tick channel buffer; defaults to 4096
	Diagnostic bool   // enable verbose RType counter + symbol mapping logging
}

const defaultBufferSize = 4096

// Client implements feed.Feed using dbn-go for the Databento Live API.
type Client struct {
	cfg Config

	mu       sync.Mutex
	clients  map[feed.Dataset]*dbn_live.LiveClient
	subs     map[feed.Dataset][]pendingSub
	// bootstrapMeta is the pre-populated instrument_id -> contract map per
	// dataset, sourced from the Historical definition schema. Required for
	// OPRA where the live gateway does not broadcast SymbolMappingMsg.
	bootstrapMeta map[feed.Dataset]map[uint32]instrumentMeta
	connOnce      bool
	started       bool
	stopped       bool

	ticks chan feed.Tick
	errs  chan error
	wg    sync.WaitGroup
}

// pendingSub captures one Subscribe call so the adapter can re-emit the
// equivalent dbn-go SubscriptionRequestMsg(s) once the underlying client
// for the dataset is open.
type pendingSub struct {
	schema  feed.Schema
	symbol  feed.Symbol
}

// New constructs a Client. APIKey must be set; other fields default.
func New(cfg Config) (*Client, error) {
	if strings.TrimSpace(cfg.APIKey) == "" {
		return nil, errors.New("databento: APIKey is required")
	}
	if cfg.BufferSize <= 0 {
		cfg.BufferSize = defaultBufferSize
	}
	return &Client{
		cfg:           cfg,
		clients:       make(map[feed.Dataset]*dbn_live.LiveClient),
		subs:          make(map[feed.Dataset][]pendingSub),
		bootstrapMeta: make(map[feed.Dataset]map[uint32]instrumentMeta),
		ticks:         make(chan feed.Tick, cfg.BufferSize),
		errs:          make(chan error, 64),
	}, nil
}

// Connect marks the adapter ready. Underlying TCP connections are opened
// lazily per-dataset on the first Subscribe to that dataset; this matches
// dbn-go's per-dataset client model and avoids reserving a connection for
// a dataset we never use.
func (c *Client) Connect(ctx context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.stopped {
		return errors.New("databento: client stopped")
	}
	c.connOnce = true
	return nil
}

// Subscribe registers interest. Multiple calls are additive. Each call may
// open a new dbn-go LiveClient if its dataset hasn't been seen yet.
//
// For datasets that don't broadcast SymbolMappingMsg over the live wire
// (notably OPRA.PILLAR), Subscribe also pulls a definition snapshot from
// the Historical API and seeds the per-dataset registry. Without this,
// the live visitor would drop every record because instrument_id can't
// be resolved.
func (c *Client) Subscribe(ctx context.Context, subs []feed.Subscription) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.connOnce {
		return errors.New("databento: Connect not called")
	}
	if c.started {
		return errors.New("databento: cannot Subscribe after Start")
	}

	// Group requested parent symbols per dataset so we can fetch a single
	// definition batch per dataset instead of per Subscribe call.
	parents := make(map[feed.Dataset]map[string]struct{})
	for _, s := range subs {
		client, err := c.ensureClient(s.Dataset)
		if err != nil {
			return fmt.Errorf("dbn ensure %s: %w", s.Dataset, err)
		}
		req, err := buildRequest(s)
		if err != nil {
			return fmt.Errorf("dbn build sub: %w", err)
		}
		if err := client.Subscribe(req); err != nil {
			return fmt.Errorf("dbn subscribe %s/%s: %w", s.Dataset, s.Schema, err)
		}
		c.subs[s.Dataset] = append(c.subs[s.Dataset], pendingSub{
			schema: s.Schema,
			symbol: s.Symbol,
		})
		if needsBootstrap(s.Dataset) {
			set, ok := parents[s.Dataset]
			if !ok {
				set = make(map[string]struct{})
				parents[s.Dataset] = set
			}
			for _, p := range req.Symbols {
				set[p] = struct{}{}
			}
		}
	}

	for ds, set := range parents {
		list := make([]string, 0, len(set))
		for p := range set {
			list = append(list, p)
		}
		meta, err := bootstrapDataset(c.cfg.APIKey, ds, list)
		if err != nil {
			c.pushErr(fmt.Errorf("bootstrap %s skipped: %w", ds, err))
			continue
		}
		c.bootstrapMeta[ds] = meta
	}

	return nil
}

// needsBootstrap reports whether a dataset's live gateway omits
// SymbolMappingMsg and therefore requires a Historical pre-fetch.
//
// OPRA.PILLAR: parent symbology subscriptions get instrument records
// directly without a mapping. Confirmed empirically (see PROGRESS.md
// 2026-05-25 OPRA diagnostic) and by the Python reference implementation
// at FLOWGREEKS/backend/app/ingestion/databento_live.py.
//
// GLBX.MDP3: gateway broadcasts SymbolMappingMsg on Subscribe — no
// bootstrap needed.
func needsBootstrap(ds feed.Dataset) bool {
	return ds == feed.DatasetOPRAPillar
}

// ensureClient lazily creates and authenticates a LiveClient for a dataset.
// Caller must hold c.mu.
func (c *Client) ensureClient(ds feed.Dataset) (*dbn_live.LiveClient, error) {
	if cl, ok := c.clients[ds]; ok {
		return cl, nil
	}
	cl, err := dbn_live.NewLiveClient(dbn_live.LiveConfig{
		ApiKey:               c.cfg.APIKey,
		Dataset:              string(ds),
		Encoding:             dbn.Encoding_Dbn,
		VersionUpgradePolicy: dbn.VersionUpgradePolicy_Upgrade,
	})
	if err != nil {
		return nil, fmt.Errorf("new live client: %w", err)
	}
	if _, err := cl.Authenticate(c.cfg.APIKey); err != nil {
		_ = cl.Stop()
		return nil, fmt.Errorf("authenticate: %w", err)
	}
	c.clients[ds] = cl
	return cl, nil
}

// buildRequest maps a feed.Subscription to a dbn-go SubscriptionRequestMsg.
// Uses parent symbology so a single subscription captures every contract for
// the underlying.
func buildRequest(s feed.Subscription) (dbn_live.SubscriptionRequestMsg, error) {
	syms, err := vendorSymbols(s.Dataset, s.Symbol)
	if err != nil {
		return dbn_live.SubscriptionRequestMsg{}, err
	}
	return dbn_live.SubscriptionRequestMsg{
		Schema:  string(s.Schema),
		StypeIn: dbn.SType_Parent,
		Symbols: syms,
	}, nil
}

// vendorSymbols returns the parent-symbology symbols for our internal Symbol
// on the given dataset.
//
//	OPRA.PILLAR + SPX → SPX.OPT, SPXW.OPT
//	OPRA.PILLAR + NDX → NDX.OPT, NDXP.OPT
//	GLBX.MDP3   + SPX → ES.FUT
//	GLBX.MDP3   + NDX → NQ.FUT
func vendorSymbols(ds feed.Dataset, sym feed.Symbol) ([]string, error) {
	switch ds {
	case feed.DatasetOPRAPillar:
		switch sym {
		case feed.SymbolSPX:
			return []string{"SPX.OPT", "SPXW.OPT"}, nil
		case feed.SymbolNDX:
			return []string{"NDX.OPT", "NDXP.OPT"}, nil
		}
	case feed.DatasetCMEGlobex:
		switch sym {
		case feed.SymbolSPX:
			return []string{"ES.FUT"}, nil
		case feed.SymbolNDX:
			return []string{"NQ.FUT"}, nil
		}
	}
	return nil, fmt.Errorf("unsupported dataset/symbol: %s/%s", ds, sym)
}

// Start begins streaming. Spawns one reader goroutine per dataset client.
func (c *Client) Start(ctx context.Context) error {
	c.mu.Lock()
	if c.started {
		c.mu.Unlock()
		return errors.New("databento: already started")
	}
	if len(c.clients) == 0 {
		c.mu.Unlock()
		return errors.New("databento: no subscriptions")
	}
	for ds, cl := range c.clients {
		if err := cl.Start(); err != nil {
			c.mu.Unlock()
			return fmt.Errorf("dbn start %s: %w", ds, err)
		}
	}
	c.started = true
	clients := make(map[feed.Dataset]*dbn_live.LiveClient, len(c.clients))
	for k, v := range c.clients {
		clients[k] = v
	}
	c.mu.Unlock()

	for ds, cl := range clients {
		c.wg.Add(1)
		go c.run(ctx, ds, cl)
	}
	return nil
}

// run is the per-client read loop. Recovers from panics so a malformed
// message can't take down the ingest service.
func (c *Client) run(ctx context.Context, ds feed.Dataset, cl *dbn_live.LiveClient) {
	defer c.wg.Done()
	defer func() {
		if r := recover(); r != nil {
			c.pushErr(fmt.Errorf("dbn reader %s panic: %v", ds, r))
		}
	}()

	scanner := cl.GetDbnScanner()
	if scanner == nil {
		c.pushErr(fmt.Errorf("dbn %s: nil scanner", ds))
		return
	}

	v := &visitor{
		client:    c,
		dataset:   ds,
		meta:      make(map[uint32]instrumentMeta),
		rtypeHits: make(map[uint8]uint64),
	}

	// Seed the visitor's instrument map from the Historical bootstrap
	// snapshot (if any). For OPRA this is required — the live gateway
	// won't broadcast mappings for parent subscriptions.
	c.mu.Lock()
	if pre, ok := c.bootstrapMeta[ds]; ok {
		for k, m := range pre {
			v.meta[k] = m
		}
	}
	c.mu.Unlock()
	if len(v.meta) > 0 {
		c.pushErr(fmt.Errorf("dbn %s: registry seeded from historical (%d instruments)", ds, len(v.meta)))
	}

	// Periodic diagnostic dump per dataset.
	if c.cfg.Diagnostic {
		c.wg.Add(1)
		go v.diagnosticLoop(ctx)
	}

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}
		if !scanner.Next() {
			if err := scanner.Error(); err != nil && !errors.Is(err, io.EOF) {
				c.pushErr(fmt.Errorf("dbn %s scan: %w", ds, err))
			}
			return
		}
		if err := scanner.Visit(v); err != nil {
			c.pushErr(fmt.Errorf("dbn %s visit: %w", ds, err))
		}
	}
}

// Ticks returns the channel of normalized ticks. Closed by Stop.
func (c *Client) Ticks() <-chan feed.Tick { return c.ticks }

// Errors returns the channel of non-fatal errors.
func (c *Client) Errors() <-chan error { return c.errs }

// Stop closes connections and channels. Idempotent.
func (c *Client) Stop() error {
	c.mu.Lock()
	if c.stopped {
		c.mu.Unlock()
		return nil
	}
	c.stopped = true
	clients := c.clients
	c.clients = nil
	c.mu.Unlock()

	var firstErr error
	for ds, cl := range clients {
		if err := cl.Stop(); err != nil && firstErr == nil {
			firstErr = fmt.Errorf("dbn stop %s: %w", ds, err)
		}
	}
	c.wg.Wait()
	close(c.ticks)
	close(c.errs)
	return firstErr
}

// pushErr non-blockingly sends to the error channel; drops on overflow so a
// stalled consumer can't wedge the ingest path.
func (c *Client) pushErr(err error) {
	select {
	case c.errs <- err:
	default:
	}
}

// pushTick non-blockingly sends a tick. Drops on overflow — consumer is
// expected to drain promptly per the Feed contract.
func (c *Client) pushTick(t feed.Tick) {
	select {
	case c.ticks <- t:
	default:
	}
}

// ─── visitor ──────────────────────────────────────────────────────────────

// visitor implements dbn.Visitor. Holds the per-client instrument-id cache
// so symbol resolution is O(1) on the hot path.
type visitor struct {
	dbn.NullVisitor
	client    *Client
	dataset   feed.Dataset
	meta      map[uint32]instrumentMeta

	// Diagnostic counters (always populated; cheap).
	mu           sync.Mutex
	rtypeHits    map[uint8]uint64
	mappingsSeen uint64
	mappingsBad  uint64
	sampleSyms   []string // first 10 symbols seen via mapping
}

func (v *visitor) bumpRType(rt uint8) {
	v.mu.Lock()
	v.rtypeHits[rt]++
	v.mu.Unlock()
}

// diagnosticLoop logs message-type counters every 10s when Diagnostic is on.
func (v *visitor) diagnosticLoop(ctx context.Context) {
	defer v.client.wg.Done()
	t := time.NewTicker(10 * time.Second)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			v.mu.Lock()
			snap := make(map[uint8]uint64, len(v.rtypeHits))
			for k, n := range v.rtypeHits {
				snap[k] = n
			}
			mapSeen := v.mappingsSeen
			mapBad := v.mappingsBad
			samples := append([]string(nil), v.sampleSyms...)
			v.mu.Unlock()
			v.client.pushErr(fmt.Errorf("DIAG %s: rtype_hits=%v mappings=%d unresolved=%d sample_syms=%v",
				v.dataset, snap, mapSeen, mapBad, samples))
		}
	}
}

func (v *visitor) OnSymbolMappingMsg(r *dbn.SymbolMappingMsg) error {
	v.bumpRType(uint8(r.RType()))
	raw := r.StypeOutSymbol
	v.mu.Lock()
	v.mappingsSeen++
	if len(v.sampleSyms) < 10 {
		v.sampleSyms = append(v.sampleSyms, fmt.Sprintf("in=%q out=%q stype_in=%d stype_out=%d",
			r.StypeInSymbol, raw, r.StypeIn, r.StypeOut))
	}
	v.mu.Unlock()
	if m, ok := resolveSymbol(raw); ok {
		v.meta[r.Header.InstrumentID] = m
		return nil
	}
	v.mu.Lock()
	v.mappingsBad++
	v.mu.Unlock()
	v.client.pushErr(fmt.Errorf("unresolved symbol mapping %s: in=%q out=%q (instrument_id=%d)",
		v.dataset, r.StypeInSymbol, raw, r.Header.InstrumentID))
	return nil
}

func (v *visitor) OnSystemMsg(r *dbn.SystemMsg) error {
	v.bumpRType(uint8(r.RType()))
	if r.IsHeartbeat() {
		return nil
	}
	msg := strings.TrimRight(string(r.Message[:]), "\x00")
	v.client.pushErr(fmt.Errorf("dbn %s system: code=%d msg=%q", v.dataset, r.Code, msg))
	return nil
}

// OnInstrumentDefMsg handles definition records arriving over the live
// wire. New strikes listed mid-session land here so the registry stays
// fresh without a re-bootstrap.
func (v *visitor) OnInstrumentDefMsg(r *dbn.InstrumentDefMsg) error {
	v.bumpRType(uint8(r.RType()))
	raw := dbnFixedString(r.RawSymbol[:])
	m, ok := resolveSymbol(raw)
	if !ok {
		return nil
	}
	v.meta[r.Header.InstrumentID] = m
	return nil
}

func (v *visitor) OnMbp1(r *dbn.Mbp1Msg) error {
	v.bumpRType(uint8(r.RType()))
	meta, ok := v.meta[r.Header.InstrumentID]
	if !ok {
		return nil
	}
	var t feed.Tick
	convertMbp1(&t, r, meta, uint64(time.Now().UnixNano()))
	v.client.pushTick(t)
	return nil
}

func (v *visitor) OnCmbp1(r *dbn.Cmbp1Msg) error {
	v.bumpRType(uint8(r.RType()))
	meta, ok := v.meta[r.Header.InstrumentID]
	if !ok {
		return nil
	}
	var t feed.Tick
	convertCmbp1(&t, r, meta, uint64(time.Now().UnixNano()))
	v.client.pushTick(t)
	return nil
}

func (v *visitor) OnMbp0(r *dbn.Mbp0Msg) error {
	v.bumpRType(uint8(r.RType()))
	meta, ok := v.meta[r.Header.InstrumentID]
	if !ok {
		return nil
	}
	var t feed.Tick
	convertTrade(&t, r, meta, uint64(time.Now().UnixNano()))
	v.client.pushTick(t)
	return nil
}

func (v *visitor) OnErrorMsg(r *dbn.ErrorMsg) error {
	v.bumpRType(uint8(r.RType()))
	msg := strings.TrimRight(string(r.Error[:]), "\x00")
	v.client.pushErr(fmt.Errorf("dbn gateway %s: %s", v.dataset, msg))
	return nil
}

// Compile-time assertion: *Client satisfies feed.Feed.
var _ feed.Feed = (*Client)(nil)
