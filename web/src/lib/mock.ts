/**
 * Mock data shaped after flowgreeks `docs/openapi.yaml` schemas:
 * StateSnapshot, DPIBreakdown, FlowPulse, StrikeRow, PinResult, LevelsResponse.
 * Numbers chosen to look believable for an SPX 0DTE session near close.
 */

export type CharmZone = "WEAK" | "RISING" | "PEAK" | "FADING" | "PIN";
export type Regime = "SHORT_GAMMA" | "LONG_GAMMA" | "NEUTRAL";

export interface DPIBreakdown {
  composite: number;
  net_gamma_sign: number;
  charm_velocity: number;
  vanna_sensitivity: number;
  time_to_close_decay: number;
  flow_concentration: number;
}

export interface FlowPulse {
  gamma: number;
  charm: number;
  vanna: number;
  total: number;
}

export interface PinCandidate {
  strike: number;
  probability: number;
  gamma_strength: number;
  distance_factor: number;
  flow_persistence: number;
  time_factor: number;
}

export interface StrikeRow {
  expiry: number;
  strike: number;
  side: "C" | "P";
  dealer_pos: number;
  iv: number;
  gamma: number;
  charm: number;
  vanna: number;
  gex_notional: number;
}

export interface FlowEvent {
  ts: string;
  symbol: string;
  side: "C" | "P";
  strike: number;
  qty: number;
  price: number;
  premium: number;
  aggressor: "BUY" | "SELL";
  tag: "SWEEP" | "BLOCK" | "OPENING" | "REPEAT";
}

export interface Alert {
  ts: string;
  kind: string;
  message: string;
  severity: "info" | "warn" | "crit";
}

export interface Snapshot {
  ts_ns: number;
  symbol: "SPX" | "NDX";
  spot: number;
  basis_smooth: number;
  fut_front_sym: string;
  net_gex: number;
  zero_gamma: number;
  call_wall: number;
  put_wall: number;
  expected_mv: number;
  regime: Regime;
  charm_zone: CharmZone;
  charm_velocity_raw: number;
  dpi: DPIBreakdown;
  flow_pulse: FlowPulse;
  pin: {
    active: boolean;
    window_mins: number;
    top_strike: number;
    top_probability: number;
    candidates: PinCandidate[];
  };
  strikes: StrikeRow[];
}

export const SNAPSHOT: Snapshot = {
  ts_ns: Date.now() * 1e6,
  symbol: "SPX",
  spot: 5847.62,
  basis_smooth: 4.18,
  fut_front_sym: "ESH6",
  net_gex: -2_140_000_000,
  zero_gamma: 5862.5,
  call_wall: 5900,
  put_wall: 5800,
  expected_mv: 28.4,
  regime: "SHORT_GAMMA",
  charm_zone: "PEAK",
  charm_velocity_raw: 0.0184,
  dpi: {
    composite: 78.4,
    net_gamma_sign: -1,
    charm_velocity: 0.82,
    vanna_sensitivity: 0.61,
    time_to_close_decay: 0.73,
    flow_concentration: 0.88,
  },
  flow_pulse: {
    gamma: 0.71,
    charm: 0.84,
    vanna: 0.42,
    total: 0.74,
  },
  pin: {
    active: true,
    window_mins: 42,
    top_strike: 5850,
    top_probability: 0.47,
    candidates: [
      { strike: 5850, probability: 0.47, gamma_strength: 0.92, distance_factor: 0.98, flow_persistence: 0.81, time_factor: 0.66 },
      { strike: 5825, probability: 0.21, gamma_strength: 0.74, distance_factor: 0.71, flow_persistence: 0.55, time_factor: 0.66 },
      { strike: 5875, probability: 0.18, gamma_strength: 0.69, distance_factor: 0.62, flow_persistence: 0.48, time_factor: 0.66 },
      { strike: 5800, probability: 0.09, gamma_strength: 0.51, distance_factor: 0.42, flow_persistence: 0.39, time_factor: 0.66 },
    ],
  },
  strikes: [
    { expiry: 20251128, strike: 5750, side: "P", dealer_pos: -1840000, iv: 0.184, gamma: 0.0042, charm: -0.012, vanna: 0.18, gex_notional: -180_000_000 },
    { expiry: 20251128, strike: 5800, side: "P", dealer_pos: -3120000, iv: 0.171, gamma: 0.0098, charm: -0.024, vanna: 0.31, gex_notional: -520_000_000 },
    { expiry: 20251128, strike: 5825, side: "P", dealer_pos: -2680000, iv: 0.162, gamma: 0.0146, charm: -0.041, vanna: 0.39, gex_notional: -610_000_000 },
    { expiry: 20251128, strike: 5850, side: "C", dealer_pos: -4210000, iv: 0.155, gamma: 0.0192, charm: -0.058, vanna: 0.44, gex_notional: -840_000_000 },
    { expiry: 20251128, strike: 5850, side: "P", dealer_pos: -3940000, iv: 0.155, gamma: 0.0188, charm: 0.054, vanna: 0.41, gex_notional: -790_000_000 },
    { expiry: 20251128, strike: 5875, side: "C", dealer_pos: -2110000, iv: 0.149, gamma: 0.0134, charm: -0.039, vanna: 0.36, gex_notional: -440_000_000 },
    { expiry: 20251128, strike: 5900, side: "C", dealer_pos: 1820000, iv: 0.146, gamma: 0.0091, charm: -0.022, vanna: 0.27, gex_notional: 380_000_000 },
    { expiry: 20251128, strike: 5925, side: "C", dealer_pos: 980000, iv: 0.142, gamma: 0.0048, charm: -0.011, vanna: 0.19, gex_notional: 210_000_000 },
    { expiry: 20251128, strike: 5950, side: "C", dealer_pos: 410000, iv: 0.138, gamma: 0.0023, charm: -0.005, vanna: 0.12, gex_notional: 95_000_000 },
  ],
};

export const DPI_HISTORY: { t: string; composite: number; charm: number; vanna: number; gamma: number }[] = [
  { t: "09:30", composite: 32, charm: 18, vanna: 28, gamma: 41 },
  { t: "10:00", composite: 38, charm: 22, vanna: 31, gamma: 44 },
  { t: "10:30", composite: 41, charm: 28, vanna: 33, gamma: 47 },
  { t: "11:00", composite: 45, charm: 35, vanna: 36, gamma: 49 },
  { t: "11:30", composite: 52, charm: 44, vanna: 40, gamma: 54 },
  { t: "12:00", composite: 49, charm: 51, vanna: 42, gamma: 55 },
  { t: "12:30", composite: 54, charm: 58, vanna: 47, gamma: 58 },
  { t: "13:00", composite: 61, charm: 66, vanna: 51, gamma: 62 },
  { t: "13:30", composite: 67, charm: 72, vanna: 55, gamma: 66 },
  { t: "14:00", composite: 71, charm: 78, vanna: 58, gamma: 69 },
  { t: "14:30", composite: 74, charm: 82, vanna: 60, gamma: 70 },
  { t: "15:00", composite: 76, charm: 84, vanna: 61, gamma: 71 },
  { t: "15:30", composite: 78, charm: 84, vanna: 61, gamma: 71 },
];

export const SPOT_HISTORY: { t: string; spot: number; zero_gamma: number; call_wall: number; put_wall: number }[] = [
  { t: "09:30", spot: 5831.20, zero_gamma: 5862, call_wall: 5900, put_wall: 5800 },
  { t: "10:00", spot: 5834.55, zero_gamma: 5862, call_wall: 5900, put_wall: 5800 },
  { t: "10:30", spot: 5829.10, zero_gamma: 5862, call_wall: 5900, put_wall: 5800 },
  { t: "11:00", spot: 5837.40, zero_gamma: 5862, call_wall: 5900, put_wall: 5800 },
  { t: "11:30", spot: 5842.85, zero_gamma: 5862, call_wall: 5900, put_wall: 5800 },
  { t: "12:00", spot: 5840.60, zero_gamma: 5862, call_wall: 5900, put_wall: 5800 },
  { t: "12:30", spot: 5845.10, zero_gamma: 5862.5, call_wall: 5900, put_wall: 5800 },
  { t: "13:00", spot: 5849.20, zero_gamma: 5862.5, call_wall: 5900, put_wall: 5800 },
  { t: "13:30", spot: 5851.40, zero_gamma: 5862.5, call_wall: 5900, put_wall: 5800 },
  { t: "14:00", spot: 5848.95, zero_gamma: 5862.5, call_wall: 5900, put_wall: 5800 },
  { t: "14:30", spot: 5846.80, zero_gamma: 5862.5, call_wall: 5900, put_wall: 5800 },
  { t: "15:00", spot: 5847.20, zero_gamma: 5862.5, call_wall: 5900, put_wall: 5800 },
  { t: "15:30", spot: 5847.62, zero_gamma: 5862.5, call_wall: 5900, put_wall: 5800 },
];

export const FLOW_TAPE: FlowEvent[] = [
  { ts: "15:31:42", symbol: "SPX", side: "P", strike: 5825, qty: 1840, price: 4.20, premium: 772800, aggressor: "BUY", tag: "SWEEP" },
  { ts: "15:31:38", symbol: "SPX", side: "C", strike: 5850, qty: 2210, price: 7.80, premium: 1723800, aggressor: "SELL", tag: "BLOCK" },
  { ts: "15:31:35", symbol: "SPX", side: "P", strike: 5850, qty: 920, price: 6.15, premium: 565800, aggressor: "BUY", tag: "OPENING" },
  { ts: "15:31:31", symbol: "SPX", side: "C", strike: 5875, qty: 540, price: 3.95, premium: 213300, aggressor: "BUY", tag: "REPEAT" },
  { ts: "15:31:28", symbol: "SPX", side: "P", strike: 5800, qty: 3120, price: 2.45, premium: 764400, aggressor: "BUY", tag: "SWEEP" },
  { ts: "15:31:24", symbol: "SPX", side: "C", strike: 5900, qty: 1640, price: 1.20, premium: 196800, aggressor: "SELL", tag: "BLOCK" },
  { ts: "15:31:19", symbol: "SPX", side: "P", strike: 5850, qty: 1820, price: 6.20, premium: 1128400, aggressor: "BUY", tag: "SWEEP" },
  { ts: "15:31:15", symbol: "SPX", side: "C", strike: 5850, qty: 2410, price: 7.75, premium: 1867750, aggressor: "BUY", tag: "BLOCK" },
  { ts: "15:31:11", symbol: "SPX", side: "P", strike: 5825, qty: 740, price: 4.15, premium: 307100, aggressor: "SELL", tag: "REPEAT" },
  { ts: "15:31:06", symbol: "SPX", side: "C", strike: 5825, qty: 1290, price: 23.40, premium: 3018600, aggressor: "BUY", tag: "OPENING" },
];

export const ALERTS: Alert[] = [
  { ts: "15:31:40", kind: "DPI_ABOVE", message: "DPI composite crossed 75 (now 78.4)", severity: "crit" },
  { ts: "15:30:12", kind: "CHARM_ZONE", message: "Charm zone transitioned RISING → PEAK", severity: "warn" },
  { ts: "15:28:55", kind: "PIN_PROB", message: "Pin probability 5850 > 45% (47% live)", severity: "warn" },
  { ts: "15:22:18", kind: "NET_GEX", message: "Net GEX flipped negative — short-gamma regime", severity: "crit" },
  { ts: "15:18:04", kind: "FLOW_CONC", message: "Flow concentration at 5850 = 88% (3σ above avg)", severity: "info" },
  { ts: "14:58:30", kind: "WALL_SHIFT", message: "Call wall 5910 → 5900 (size decay)", severity: "info" },
  { ts: "14:42:11", kind: "REGIME_FLIP", message: "Regime LONG_GAMMA → NEUTRAL (3rd in session)", severity: "info" },
];

export const CHARM_CLOCK_HOURS = Array.from({ length: 24 }, (_, i) => {
  const hour = i;
  // simulate intraday charm curve: 0 at 9:30, peaks at ~15:00, decays by close
  const tradingHour = hour - 9.5;
  const intensity = tradingHour > 0 && tradingHour < 6.5
    ? Math.sin((tradingHour / 6.5) * Math.PI) * 0.9 + Math.random() * 0.08
    : 0.05 + Math.random() * 0.05;
  return { hour, intensity };
});

export const KEY_LEVELS = [
  { label: "Call Wall", price: 5900, dist: +0.90, type: "resistance" as const, strength: 0.91 },
  { label: "Volatility Trigger", price: 5872, dist: +0.42, type: "neutral" as const, strength: 0.62 },
  { label: "Zero Gamma", price: 5862.5, dist: +0.25, type: "flip" as const, strength: 0.78 },
  { label: "Pin Strike", price: 5850, dist: +0.04, type: "pin" as const, strength: 0.92 },
  { label: "Spot", price: 5847.62, dist: 0.0, type: "spot" as const, strength: 1.0 },
  { label: "Pivot Down", price: 5840, dist: -0.13, type: "neutral" as const, strength: 0.41 },
  { label: "Put Wall", price: 5800, dist: -0.81, type: "support" as const, strength: 0.86 },
  { label: "Crash Pivot", price: 5780, dist: -1.16, type: "support" as const, strength: 0.55 },
];

export const FORCED_FLOW_SCENARIOS = [
  { label: "Spot +0.5% in 30m", forced_notional: -1_840_000_000, charm_aid: 280_000_000, net_pressure: -1_560_000_000 },
  { label: "Spot +1.0% in 60m", forced_notional: -3_420_000_000, charm_aid: 510_000_000, net_pressure: -2_910_000_000 },
  { label: "Spot -0.5% in 30m", forced_notional: +1_280_000_000, charm_aid: 280_000_000, net_pressure: +1_560_000_000 },
  { label: "Vol +1pt in 60m", forced_notional: -640_000_000, charm_aid: 510_000_000, net_pressure: -130_000_000 },
];
