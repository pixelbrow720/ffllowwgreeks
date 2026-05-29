"use client";

// Spot + alert history accumulators. Spot history hybrid-loads from
// /api/history (backfill on mount) plus the live WS stream (real-time
// updates after first paint). Alert log is WS-only — alerts are only
// fanned out when an evaluation actually triggers, so there's no
// historical record to backfill.

import { useEffect, useSyncExternalStore } from "react";
import { useLiveSocket } from "../ws/useLiveSocket";
import type { Channel } from "../ws/client";
import { getHistory } from "./client";
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

// 480 = 8 hours @ 1/min after dedupe. Comfortable headroom for a full
// RTH session (6.5h) plus pre-market overlap. Backend chart query is
// already downsampled, so memory cost is bounded.
const SPOT_MAX = 480;

// RTH-only filter. User asked for the chart to start near 23:00 WIB
// (= 16:00 UTC) but unpaced replay slows once the strike cache passes
// ~1500 entries. We drop the cutoff to 15:30 UTC (= 22:30 WIB) so the
// dashboard has data to render immediately. The backend still ingests
// the full window from 11:29 UTC (OI seed) so the position tracker is
// fully populated by the time anything renders.
const RTH_START_MIN_UTC = 15 * 60 + 30;

function isInRTH(tsNs: number): boolean {
  const d = new Date(Math.floor(tsNs / 1e6));
  const minOfDay = d.getUTCHours() * 60 + d.getUTCMinutes();
  return minOfDay >= RTH_START_MIN_UTC;
}

interface SpotEntry {
  series: SpotPoint[];
  listeners: Set<() => void>;
  backfillStarted: boolean;
}

const spotStore = new Map<Symbol, SpotEntry>();

function ensureSpot(symbol: Symbol): SpotEntry {
  let e = spotStore.get(symbol);
  if (!e) {
    e = { series: [], listeners: new Set(), backfillStarted: false };
    spotStore.set(symbol, e);
  }
  return e;
}

function pushSpot(symbol: Symbol, ts_ns: number, spot: number) {
  if (!Number.isFinite(spot) || spot <= 0) return;
  if (!isInRTH(ts_ns)) return;
  const e = ensureSpot(symbol);
  const last = e.series[e.series.length - 1];
  // De-dupe on (HH:MM) so 60 1Hz samples per minute collapse to one
  // point. Keeps the chart legible across multi-hour windows.
  const date = new Date(Math.floor(ts_ns / 1e6));
  const t = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  if (last && last.t === t) {
    e.series = [...e.series.slice(0, -1), { ts_ns, t, spot }];
  } else if (last && last.ts_ns > ts_ns) {
    // Out-of-order arrival (rare): insert in correct position so the
    // chart stays monotonic. Backfill from REST happens after the WS
    // has already pushed the live tail; this catches it.
    const merged = [...e.series, { ts_ns, t, spot }].sort((a, b) => a.ts_ns - b.ts_ns);
    e.series = dedupeByMinute(merged);
    if (e.series.length > SPOT_MAX) e.series = e.series.slice(-SPOT_MAX);
  } else {
    e.series = [...e.series, { ts_ns, t, spot }];
    if (e.series.length > SPOT_MAX) e.series = e.series.slice(-SPOT_MAX);
  }
  e.listeners.forEach((l) => l());
}

function dedupeByMinute(arr: SpotPoint[]): SpotPoint[] {
  // Last-wins per minute. Walk in order so the final entry per (t)
  // bucket is the latest by ts_ns.
  const map = new Map<string, SpotPoint>();
  for (const p of arr) map.set(p.t, p);
  return Array.from(map.values()).sort((a, b) => a.ts_ns - b.ts_ns);
}

// Backfill spot history from /api/history. Called once per symbol on
// first hook mount. Window covers the past 8h so a fresh page-load
// after lunch still shows the morning session.
async function backfillSpot(symbol: Symbol): Promise<void> {
  const e = ensureSpot(symbol);
  if (e.backfillStarted) return;
  e.backfillStarted = true;
  try {
    const to = new Date();
    const from = new Date(to.getTime() - 8 * 60 * 60 * 1000);
    const resp = await getHistory(symbol, { from, to, max: 480 });
    for (const s of resp.samples) {
      pushSpot(symbol, s.ts_ns, s.spot);
    }
  } catch (err) {
    // Backfill is best-effort. WS will populate live; user just won't
    // see history until a fresh sample lands.
    // eslint-disable-next-line no-console
    console.warn("[flowgreeks] history backfill failed", err);
    e.backfillStarted = false; // allow a retry on remount
  }
}

export function useSpotHistory(symbol: Symbol): SpotPoint[] {
  const sock = useLiveSocket();
  useEffect(() => {
    void backfillSpot(symbol);
  }, [symbol]);
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
