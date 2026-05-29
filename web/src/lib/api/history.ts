"use client";

// Lightweight in-memory accumulators that build session history from
// the live WS stream. There are no REST endpoints for spot history or
// alert log, so panels that need either subscribe here.

import { useEffect, useSyncExternalStore } from "react";
import { useLiveSocket } from "../ws/useLiveSocket";
import type { Channel } from "../ws/client";
import type { Symbol } from "./types";

// ---------- spot history ----------

export interface SpotPoint {
  ts_ns: number;
  t: string; // HH:MM
  spot: number;
}

const SPOT_MAX = 120;

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
  const e = ensureSpot(symbol);
  const last = e.series[e.series.length - 1];
  // De-dupe ts (compute publishes every second; only keep the latest
  // sample inside the same minute to keep the chart legible).
  const date = new Date(Math.floor(ts_ns / 1e6));
  const t = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  if (last && last.t === t) {
    last.ts_ns = ts_ns;
    last.spot = spot;
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
    () => [],
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
    () => [],
  );
}
