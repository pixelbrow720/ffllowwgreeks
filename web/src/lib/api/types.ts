// Domain types used by the dashboard. The on-the-wire shape (see
// schema.ts / openapi.yaml) encodes symbol, regime, charm_zone, side
// as integers and strike × 1000. Panels work in human-readable strings
// and natural strike units, so we decode at the API boundary and treat
// schema.ts as the contract, not the in-app type.

import type { components } from "./schema";

export type WireSnapshot = components["schemas"]["StateSnapshot"];
export type WireStrikeRow = components["schemas"]["StrikeRow"];
export type WireLevels = components["schemas"]["LevelsResponse"];

export type Symbol = "SPX" | "NDX";
export type Regime = "UNKNOWN" | "SHORT_GAMMA" | "LONG_GAMMA" | "NEUTRAL";
export type CharmZone = "UNKNOWN" | "WEAK" | "RISING" | "PEAK" | "FADING" | "PIN";
export type Side = "C" | "P";

export interface StrikeRow {
  expiry: number;
  strike: number;
  side: Side;
  dealer_pos: number;
  iv: number;
  gamma: number;
  charm: number;
  vanna: number;
  gex_notional: number;
}

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

export interface PinResult {
  active: boolean;
  window_mins: number;
  top_strike: number;
  top_probability: number;
  candidates: PinCandidate[];
}

export interface Snapshot {
  ts_ns: number;
  symbol: Symbol;
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
  pin: PinResult;
  strikes: StrikeRow[];
}

export interface Levels {
  ts_ns: number;
  spot: number;
  zero_gamma: number;
  call_wall: number;
  put_wall: number;
  expected_mv: number;
  net_gex: number;
  regime: Regime;
}

export function decodeSymbol(n: number | undefined): Symbol {
  return n === 2 ? "NDX" : "SPX";
}

export function decodeRegime(n: number | undefined): Regime {
  switch (n) {
    case 1: return "SHORT_GAMMA";
    case 2: return "LONG_GAMMA";
    case 3: return "NEUTRAL";
    default: return "UNKNOWN";
  }
}

export function decodeCharmZone(n: number | undefined): CharmZone {
  switch (n) {
    case 1: return "WEAK";
    case 2: return "RISING";
    case 3: return "PEAK";
    case 4: return "FADING";
    case 5: return "PIN";
    default: return "UNKNOWN";
  }
}

export function decodeSide(n: number | undefined): Side {
  return n === 2 ? "P" : "C";
}

function decodeStrike(row: WireStrikeRow): StrikeRow {
  return {
    expiry: row.expiry ?? 0,
    strike: (row.strike ?? 0) / 1000,
    side: decodeSide(row.side),
    dealer_pos: row.dealer_pos ?? 0,
    iv: row.iv ?? 0,
    gamma: row.gamma ?? 0,
    charm: row.charm ?? 0,
    vanna: row.vanna ?? 0,
    gex_notional: row.gex_notional ?? 0,
  };
}

export function decodeSnapshot(w: WireSnapshot): Snapshot {
  return {
    ts_ns: w.ts_ns ?? 0,
    symbol: decodeSymbol(w.symbol),
    spot: w.spot ?? 0,
    basis_smooth: w.basis_smooth ?? 0,
    fut_front_sym: w.fut_front_sym ?? "",
    net_gex: w.net_gex ?? 0,
    zero_gamma: w.zero_gamma ?? 0,
    call_wall: w.call_wall ?? 0,
    put_wall: w.put_wall ?? 0,
    expected_mv: w.expected_mv ?? 0,
    regime: decodeRegime(w.regime),
    charm_zone: decodeCharmZone(w.charm_zone),
    charm_velocity_raw: w.charm_velocity_raw ?? 0,
    dpi: {
      composite: w.dpi?.composite ?? 0,
      net_gamma_sign: w.dpi?.net_gamma_sign ?? 0,
      charm_velocity: w.dpi?.charm_velocity ?? 0,
      vanna_sensitivity: w.dpi?.vanna_sensitivity ?? 0,
      time_to_close_decay: w.dpi?.time_to_close_decay ?? 0,
      flow_concentration: w.dpi?.flow_concentration ?? 0,
    },
    flow_pulse: {
      gamma: w.flow_pulse?.gamma ?? 0,
      charm: w.flow_pulse?.charm ?? 0,
      vanna: w.flow_pulse?.vanna ?? 0,
      total: w.flow_pulse?.total ?? 0,
    },
    pin: {
      active: w.pin?.active ?? false,
      window_mins: w.pin?.window_mins ?? 0,
      top_strike: w.pin?.top_strike ?? 0,
      top_probability: w.pin?.top_probability ?? 0,
      candidates: (w.pin?.candidates ?? []).map((c) => ({
        strike: c.strike ?? 0,
        probability: c.probability ?? 0,
        gamma_strength: c.gamma_strength ?? 0,
        distance_factor: c.distance_factor ?? 0,
        flow_persistence: c.flow_persistence ?? 0,
        time_factor: c.time_factor ?? 0,
      })),
    },
    strikes: (w.strikes ?? []).map(decodeStrike),
  };
}

export function decodeLevels(w: WireLevels): Levels {
  return {
    ts_ns: w.ts_ns ?? 0,
    spot: w.spot ?? 0,
    zero_gamma: w.zero_gamma ?? 0,
    call_wall: w.call_wall ?? 0,
    put_wall: w.put_wall ?? 0,
    expected_mv: w.expected_mv ?? 0,
    net_gex: w.net_gex ?? 0,
    regime: decodeRegime(w.regime),
  };
}
