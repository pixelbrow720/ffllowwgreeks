# 06 · Dashboard Spec

> **What every panel MUST show, what data it consumes, and what semantic decisions belong to design vs data.**
> Read this BEFORE you redesign. The data contracts are stable; the chrome is up to you.

## Layout grid (current, baseline)

Desktop only, 1920×1080.

```
┌──────────────────────────────────────────────────────────────────────┐
│   RegimeStrip (h:56) — fixed topbar across full width                │
├──────┬──────────────────────────────────────┬────────────────────────┤
│      │                                      │                        │
│ Rail │       SpotChart (h:460)              │   KeyLevels (h:260)    │
│ Nav  │                                      │                        │
│ (56) ├──────────────────────────────────────┼────────────────────────┤
│      │       DPILive (h:260)                │   PinPanel (h:460)     │
│      │                                      │                        │
│      │ ─── center column 1224 wide ───      │  right rail 360 wide   │
├──────┴──────────────────────────────────────┴────────────────────────┤
│  DPI Timeline (left, fr 1.4)  │  Signal Log (right, fr 1)            │
│  height 280                   │  height 280                          │
└──────────────────────────────────────────────────────────────────────┘
```

Total: 56 (top) + 720 (hero row) + 280 (bottom) = 1056. Plus 24 chrome slack = 1080 budget.

**Caveat:** the swap of "GEXProfile vs DPILive" was experimental. The current commit puts GEX in left rail and DPILive in main column. Brow has flagged this as ugly. Your call on rebuild.

## Per-panel data contract

### RegimeStrip (top, 56px)
**Source:** `useSnapshot(symbol)`, `useSocketStatus`, `useSpotHistory(symbol)`.

**Must show:**
- Brandmark + WS live indicator (1.5×1.5 dot, brand color when connected, ink-faint when offline).
- Symbol toggle: SPX ⇄ NDX. Active state = subtle brand-tinted pill.
- Spot price (font-display 26px, tabular).
- Δ session (delta + percent vs first WS sample, accent-long if up, accent-short if down).
- Regime label (LONG γ / SHORT γ / NEUTRAL, accent-colored).
- Zero γ price.
- Net GEX (compact `±$X.XB` notation, accent-colored).
- DPI composite (number + tier label; FORCED tier gets warn ambient + brand-tint background overlay).
- Pin status: `<strike> · <prob>%` if active, `—` otherwise.
- Local time (HH:MM:SS, updates 1Hz).
- Pipeline status (LIVE/REPLAY/OFFLINE, accent-long when live).

**Must NOT show:** historical chart, mini sparkline, multi-symbol simultaneously.

### RailNav (left, 56px wide)
**Source:** none (static).

**Must show:** vertical icon list. Current route highlighted. Hover popover with full label + status (e.g., "· soon"). 11 slots — only 1 (Overview) currently routes; rest are placeholders.

### SpotChart (center, 460h)
**Source:** `useSnapshot(symbol)`, `useSpotHistory(symbol)` (8h backfill + live).

**Must show:**
- Hero number (current spot, font-display 34px).
- Δ session next to it (signed, accent-colored).
- Subtitle: `<futures sym> basis <basis_smooth> · <N> samples`.
- Trend pill (▲/▼ + percent).
- Regime pill.
- Area chart (monochrome ink-high stroke, gradient fill).
- Reference lines: Call Wall (accent-long), Put Wall (accent-short), Zero γ (ink-muted dashed), Pin (accent-warn dotted, only if active).
- **When call_wall == put_wall**: render single neutral "Call/Put Wall <K>" label.
- Empty state when series < 1 sample.

**Must NOT show:** candle bars, indicators (RSI/MACD/etc), multi-line overlay, brand-pink anywhere on data lines.

### GEXProfile (left rail, 720h × 280w in current layout — or wherever you put it)
**Source:** `useSnapshot(symbol)`.

**Must show:**
- Vertical strike-ladder, top-of-spot to bottom.
- 23 strikes around spot, filtered to ±5% of spot (drop far-OTM LEAPS that backend top-N picker can promote).
- Per row: strike label, side hint, GEX bar (accent-long for positive net γ, accent-short for negative).
- ATM band visual amplification (subtle bg-card tint).
- Wall labels inline: cw/pw/pin tags next to strike.
- Spot crosshair: thin dashed line at the y-position between bracketing strikes, with white ink chip showing exact spot.
- Footer: `spot <S>` and `walls <call> / <put>`.

**Must NOT show:** scientific notation, > 50 strikes (unreadable), brand pink on bars.

### DPILive (panel)
**Source:** `useSnapshot(symbol)`.

**Must show:**
- Hero composite (font-display 48px, tabular). When DPI ≥ 75: `text-gradient-brand` for the numeral itself.
- Tier label (STABLE/BUILDING/ELEVATED/FORCED) + scale legend.
- 0-100 horizontal bar, with 50/75 tick marks. Bar fill: ink-base if <75, accent-warn if ≥75.
- 5 component rows: Net γ sign (chip — direction from regime, magnitude from component), Charm velocity, Vanna sens., TTC decay, Flow conc. Each: bar (Math.abs(raw) ÷ 100 × width) + numeric value.
- Flow pulse footer: γ / Charm / Vanna mini bars.
- Total flow row (signed, accent-colored).
- Charm velocity rate (`fmtRate(value)`).
- Expected move row (`±X.XX`).
- Basis row (`X.XX <fut sym>`).

**Must NOT show:** raw component values multiplied by 100 (already 0-100), brand-pink on bars.

### KeyLevels (right rail, 260h)
**Source:** `useSnapshot(symbol)`.

**Must show:**
- 5 rows: RES (Call Wall), Zero γ (FLIP), NOW (Spot, highlighted), PIN (Pin Strike if active), SUP (Put Wall).
- Per row: dot + label + price (tabular) + distance percent from spot.
- **Dot colors per data semantics:**
  - RES = `accent-short` (call wall = short-side dealer pressure)
  - SUP = `accent-long` (put wall = long-side dealer pressure)
  - FLIP = `ink-muted`
  - NOW = `ink-high`
  - PIN = `accent-warn`
- Subtitle pill: `±<expected_mv> band` or `—` placeholder.

### PinPanel (right rail, 460h)
**Source:** `useSnapshot(symbol)`.

**Must show:**
- Section "Pin candidate": strike (font-display 34px) + distance percent + probability (font-display 28px, accent-warn if hot). Sub-grid: γ str. / dist / flow. Em-dash placeholders when pin engine inactive.
- Section "Expected move (1σ to close)": ±X.XX band + spot bracket display (low / spot / high).
- Section "Charm zone": large zone label (PEAK/PIN warn, FADING ink-muted, RISING ink-base, WEAK/UNKNOWN ink-ghost), velocity rate, 4-tab segmented bar showing zone progression.
- Section "Forced-flow proxy": net pulse + γ/charm/vanna PulseBars (log-scale width).

**Must NOT show:** brand pink on data labels.

### DPITimelineLive (bottom-left, 280h)
**Source:** `getHistory()` for backfill + WS live deltas.

**Must show:**
- Composite line over session (8h backfill + live, 1 sample / minute).
- Reference lines at 50 (ELEVATED) and 75 (FORCED, accent-warn).
- Y-axis 0-100, x-axis HH:MM time.
- Hero text: "now <DPI>".

**Must NOT show:** 3-component multi-line overlay (audit confirmed it was unreadable).

### SignalLog (bottom-right, 280h)
**Source:** `useAlertLog(symbol)` (WS-only — alerts are emitted only on rule trigger).

**Must show:**
- Header columns: Time, Sev, Rule, Detail.
- Per row: timestamp (HH:MM:SS), severity chip (CRIT accent-short, WARN accent-warn, INFO neutral), rule kind, formatted message.
- Empty state: "listening for triggers" / "waiting for backend".
- Newest row at top, highlighted.
- **Format scientific notation** (e.g., `-6.3e+10` → `-63.0B`) via `formatAlertMessage` util.

## Wire shapes (data the panels must consume)

See [`reference/snapshot-spx-sample.json`](reference/snapshot-spx-sample.json) for a real payload.

Key fields:

```ts
interface Snapshot {
  ts_ns: number;
  symbol: "SPX" | "NDX";
  spot: number;
  basis_smooth: number;
  fut_front_sym: string;        // "ESH6"

  net_gex: number;              // signed dollars
  zero_gamma: number;
  call_wall: number;
  put_wall: number;
  expected_mv: number;          // ±$ to close

  regime: "LONG_GAMMA" | "SHORT_GAMMA" | "NEUTRAL";

  charm_zone: "WEAK" | "RISING" | "PEAK" | "FADING" | "PIN" | "UNKNOWN";
  charm_velocity_raw: number;   // raw rate, magnitude can be 1e6+

  dpi: {
    composite: number;          // 0-100
    net_gamma_sign: number;     // 0-100 magnitude (direction from regime)
    charm_velocity: number;     // 0-100
    vanna_sensitivity: number;  // 0-100
    time_to_close_decay: number;// 0-100
    flow_concentration: number; // 0-100
  };

  flow_pulse: {
    gamma: number;              // signed dollars
    charm: number;
    vanna: number;
    total: number;
  };

  pin: {
    active: boolean;
    top_strike: number;
    top_probability: number;    // 0-1
    candidates: Array<{ strike: number; gamma_strength: number; distance_factor: number; flow_persistence: number }>;
  };

  strikes: Array<{ strike: number; side: "C" | "P"; gex_notional: number; dealer_pos: number; iv: number; gamma: number; ... }>;
  strike_count_total: number;
  strike_count_returned: number;
}
```

## Update cadences

| Source | Cadence |
|---|---|
| Backend compute aggregator | 1 Hz (hard-coded `aggregatorTick`) |
| Backend NATS publish | 1 Hz |
| Frontend snapshot store | Throttled to **1 update / minute** |
| Frontend regime/zone/pin transitions | Force-flush through throttle |
| Spot history accumulator | Dedupe by minute |
| DPI Timeline | Dedupe by minute, 8h backfill on mount |
| RegimeStrip clock | 1 Hz local |

## What you should NOT change without writing a reason

- The 5-component DPI breakdown stays 5. Don't add a 6th unless math says so.
- Charm zone enum: WEAK/RISING/PEAK/FADING/PIN/UNKNOWN. Don't rename.
- Pin engine output shape (active/top_strike/top_probability/candidates). Don't reshape.
- Wall convention: call_wall = max-|GEX| call strike, put_wall = max-|GEX| put strike. Don't redefine.
- Latency budget. Don't loosen.
