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
  listeners: Set<() => void>;
}

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
      const cur = ensure(symbol).view.snapshot;
      if (cur && ev.snapshot.ts_ns < cur.ts_ns) return;
      update(symbol, { snapshot: ev.snapshot, status: "ready", error: null });
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
