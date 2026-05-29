"use client";

// Module-level snapshot store. One entry per symbol; subscribers reuse
// the same fetched seed and live deltas. No external state lib —
// `useSyncExternalStore` reads directly from the in-module Map.

import { useEffect, useSyncExternalStore } from "react";
import { ApiClientError, getSnapshot as restGetSnapshot } from "./client";
import type { Snapshot, Symbol } from "./types";
import { useLiveSocket } from "../ws/useLiveSocket";
import type { Channel } from "../ws/client";

export type SnapshotStatus = "idle" | "loading" | "ready" | "error";

export interface SnapshotState {
  snapshot: Snapshot | null;
  status: SnapshotStatus;
  error: ApiClientError | null;
}

interface Entry {
  // Frozen view returned from the store. Replaced on every state change
  // so `useSyncExternalStore`'s reference equality short-circuits when
  // nothing relevant has flipped.
  view: SnapshotState;
  // Only the first hook instance triggers the REST seed.
  seedStarted: boolean;
  // Last wall-clock millisecond the live snapshot was forwarded to
  // subscribers. Used to throttle the 1Hz state firehose down to a
  // human-readable cadence — operators don't need 60 redraws/min on a
  // dashboard that displays 6-figure dollar amounts. See `LIVE_THROTTLE_MS`.
  lastForwardedAt: number;
  listeners: Set<() => void>;
}

// Live snapshot updates are throttled to 1 update per minute by default.
// Backend still publishes at 1Hz, but the dashboard re-renders at most
// once a minute so values remain stable enough to read. Pin / regime /
// charm-zone flips are pushed through immediately regardless (see the
// "force" branch in the WS handler) so important transitions never wait.
const LIVE_THROTTLE_MS = 60_000;

const EMPTY_VIEW: SnapshotState = {
  snapshot: null,
  status: "idle",
  error: null,
};

const store = new Map<Symbol, Entry>();

function ensure(symbol: Symbol): Entry {
  let e = store.get(symbol);
  if (!e) {
    e = {
      view: EMPTY_VIEW,
      seedStarted: false,
      lastForwardedAt: 0,
      listeners: new Set(),
    };
    store.set(symbol, e);
  }
  return e;
}

function update(symbol: Symbol, next: Partial<SnapshotState>) {
  const e = ensure(symbol);
  e.view = { ...e.view, ...next };
  e.listeners.forEach((l) => l());
}

async function seed(symbol: Symbol): Promise<void> {
  const e = ensure(symbol);
  if (e.seedStarted) return;
  e.seedStarted = true;
  update(symbol, { status: "loading" });
  try {
    const snap = await restGetSnapshot(symbol);
    const cur = ensure(symbol).view.snapshot;
    if (!cur || snap.ts_ns > cur.ts_ns) {
      update(symbol, { snapshot: snap, status: "ready", error: null });
    } else {
      // A WS delta arrived first — keep it, just clear loading.
      update(symbol, { status: "ready", error: null });
    }
  } catch (err) {
    const apiErr = err instanceof ApiClientError ? err : new ApiClientError(0, "FETCH_FAILED", String(err));
    const cur = ensure(symbol).view.snapshot;
    update(symbol, {
      error: apiErr,
      status: cur ? "ready" : "error",
    });
  }
}

export function useSnapshot(symbol: Symbol): SnapshotState {
  const sock = useLiveSocket();

  useEffect(() => {
    void seed(symbol);
  }, [symbol]);

  useEffect(() => {
    if (!sock) return;
    const channel = `${symbol.toLowerCase()}:gex` as Channel;
    return sock.subscribe(channel, (ev) => {
      if (!ev.snapshot) return;
      const e = ensure(symbol);
      const cur = e.view.snapshot;
      if (cur && ev.snapshot.ts_ns < cur.ts_ns) return;

      // Always force-flush on the FIRST sample so the panel paints data
      // immediately rather than waiting up to a minute. After that,
      // throttle ordinary updates but force-flush whenever a "regime"
      // signal flips (regime, charm zone, pin activation/strike) so the
      // operator never misses a meaningful transition.
      const now = Date.now();
      const isFirst = !cur;
      const regimeChange =
        cur &&
        (cur.regime !== ev.snapshot.regime ||
          cur.charm_zone !== ev.snapshot.charm_zone ||
          cur.pin.active !== ev.snapshot.pin.active ||
          cur.pin.top_strike !== ev.snapshot.pin.top_strike);

      if (
        isFirst ||
        regimeChange ||
        now - e.lastForwardedAt >= LIVE_THROTTLE_MS
      ) {
        e.lastForwardedAt = now;
        update(symbol, { snapshot: ev.snapshot, status: "ready", error: null });
      }
    });
  }, [sock, symbol]);

  return useSyncExternalStore(
    (cb) => {
      const e = ensure(symbol);
      e.listeners.add(cb);
      return () => {
        e.listeners.delete(cb);
      };
    },
    () => ensure(symbol).view,
    () => EMPTY_VIEW,
  );
}
