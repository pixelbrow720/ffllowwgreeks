"use client";

// Lightweight in-memory accumulators that build session history from
// the live WS stream. There are no REST endpoints for spot history or
// alert log, so panels that need either subscribe here.

import { useEffect, useSyncExternalStore } from "react";
import { useLiveSocket } from "../ws/useLiveSocket";
import type { Channel } from "../ws/client";
import type { Symbol } from "./types";

// useSyncExternalStore's third arg (the SSR snapshot) MUST return a
// stable reference — returning `[]` inline creates a fresh array on
// every read, which React detects as a state change and triggers an
// infinite re-render. Module-level frozen empties give us reference
// equality across the lifetime of the page.

export interface SpotPoint {
  ts_ns: number;
  t: string; // HH:MM
  spot: number;
}

const EMPTY_SPOT_SERIES: ReadonlyArray<SpotPoint> = Object.freeze([]);

const SPOT_MAX = 120;

// RTH-only filter. User asked the dashboard to drop pre-RTH samples so
// the chart only shows the regular session. SPX cash opens 09:30 ET; in
// Feb 2026 (EST) that maps to 14:30 UTC. The operator asked for 20:30
// WIB (= 13:30 UTC) as their cutoff — keep 13:30 UTC here. Both
// timestamps are below the OI seed at 11:30 UTC, so the backend still
// gets a full position seed; only the front-end chart hides the early
// noise.
const RTH_START_MIN_UTC = 13 * 60 + 30;

function isInRTH(tsNs: number): boolean {
  const d = new Date(Math.floor(tsNs / 1e6));
  const minOfDay = d.getUTCHours() * 60 + d.getUTCMinutes();
  return minOfDay >= RTH_START_MIN_UTC;
}

interface SpotEntry {
  series: SpotPoint[];
  listeners: Set<() => void>;
}

const spotStore = new Map<Symbol, SpotEntry>();

function ensureSpot(symbol: Symbol): SpotEntry {
  let e = spotStore.get(symbol);
  if (!e) {
    e = { series: [], listeners: new Set() };
    spotStore.set(symbol, e);
  }
  return e;
}

function pushSpot(symbol: Symbol, ts_ns: number, spot: number) {
  if (!Number.isFinite(spot) || spot <= 0) return;
  if (!isInRTH(ts_ns)) return;
  const e = ensureSpot(symbol);
  const last = e.series[e.series.length - 1];
  // De-dupe ts (compute publishes every second; only keep the latest
  // sample inside the same minute to keep the chart legible). React's
  // strict-mode and any object the snapshot store may have shipped to
  // a memo'd consumer can be frozen, so we always replace by allocating
  // a fresh point rather than mutating in place.
  const date = new Date(Math.floor(ts_ns / 1e6));
  const t = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  if (last && last.t === t) {
    e.series = [...e.series.slice(0, -1), { ts_ns, t, spot }];
  } else {
    e.series = [...e.series, { ts_ns, t, spot }];
    if (e.series.length > SPOT_MAX) e.series = e.series.slice(-SPOT_MAX);
  }
  e.listeners.forEach((l) => l());
}

export function useSpotHistory(symbol: Symbol): SpotPoint[] {
  const sock = useLiveSocket();
  useEffect(() => {
    if (!sock) return;
    const channel = `${symbol.toLowerCase()}:gex` as Channel;
    return sock.subscribe(channel, (ev) => {
      if (!ev.snapshot) return;
      pushSpot(symbol, ev.snapshot.ts_ns, ev.snapshot.spot);
    });
  }, [sock, symbol]);
  return useSyncExternalStore(
    (cb) => {
      const e = ensureSpot(symbol);
      e.listeners.add(cb);
      return () => {
        e.listeners.delete(cb);
      };
    },
    () => ensureSpot(symbol).series,
    () => EMPTY_SPOT_SERIES as SpotPoint[],
  );
}

// ---------- alert log ----------

export type AlertKind =
  | "dpi_above"
  | "dpi_below"
  | "charm_zone"
  | "regime"
  | "pin_prob_above"
  | "net_gex_above"
  | "net_gex_below"
  | string;

export type AlertSeverity = "crit" | "warn" | "info";

export interface AlertEntry {
  ts_ns: number;
  ts: string; // HH:MM:SS
  kind: AlertKind;
  message: string;
  severity: AlertSeverity;
  symbol: Symbol;
}

const EMPTY_ALERT_LOG: ReadonlyArray<AlertEntry> = Object.freeze([]);

interface RawTrigger {
  rule_id?: string;
  symbol?: string;
  kind?: string;
  ts_ns?: number;
  text?: string;
  refs?: Record<string, unknown>;
}

const ALERT_MAX = 120;

function severityOf(kind: string): AlertSeverity {
  switch (kind) {
    case "net_gex_above":
    case "net_gex_below":
      return "crit";
    case "dpi_above":
    case "dpi_below":
    case "charm_zone":
    case "pin_prob_above":
      return "warn";
    default:
      return "info";
  }
}

interface AlertEntryStore {
  log: AlertEntry[];
  listeners: Set<() => void>;
}

const alertStore = new Map<Symbol, AlertEntryStore>();

function ensureAlerts(symbol: Symbol): AlertEntryStore {
  let e = alertStore.get(symbol);
  if (!e) {
    e = { log: [], listeners: new Set() };
    alertStore.set(symbol, e);
  }
  return e;
}

function pushAlert(symbol: Symbol, t: RawTrigger) {
  if (!t || !t.kind) return;
  const e = ensureAlerts(symbol);
  const ts_ns = t.ts_ns ?? Date.now() * 1e6;
  const date = new Date(Math.floor(ts_ns / 1e6));
  const ts = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}:${String(date.getSeconds()).padStart(2, "0")}`;
  const entry: AlertEntry = {
    ts_ns,
    ts,
    kind: t.kind,
    message: t.text ?? t.kind,
    severity: severityOf(t.kind),
    symbol,
  };
  e.log = [entry, ...e.log].slice(0, ALERT_MAX);
  e.listeners.forEach((l) => l());
}

export function useAlertLog(symbol: Symbol): AlertEntry[] {
  const sock = useLiveSocket();
  useEffect(() => {
    if (!sock) return;
    const channel = `${symbol.toLowerCase()}:alert` as Channel;
    return sock.subscribe(channel, (ev) => {
      pushAlert(symbol, ev.raw as RawTrigger);
    });
  }, [sock, symbol]);
  return useSyncExternalStore(
    (cb) => {
      const e = ensureAlerts(symbol);
      e.listeners.add(cb);
      return () => {
        e.listeners.delete(cb);
      };
    },
    () => ensureAlerts(symbol).log,
    () => EMPTY_ALERT_LOG as AlertEntry[],
  );
}
