"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { LiveSocket, type Channel, type SnapshotEvent, type SocketStatus } from "./client";

let singleton: LiveSocket | null = null;

function getSocket(): LiveSocket {
  if (singleton) return singleton;
  const base = process.env.NEXT_PUBLIC_FLOWGREEKS_API_BASE ?? "http://localhost:8080";
  // http(s) → ws(s)
  const wsUrl = base.replace(/^http(s?):/, "ws$1:") + "/ws/live";
  const apiKey = process.env.NEXT_PUBLIC_FLOWGREEKS_API_KEY ?? null;
  singleton = new LiveSocket(wsUrl, apiKey);
  singleton.connect();
  return singleton;
}

export function useLiveSocket(): LiveSocket | null {
  const [sock, setSock] = useState<LiveSocket | null>(null);
  useEffect(() => {
    setSock(getSocket());
  }, []);
  return sock;
}

export function useSocketStatus(): SocketStatus {
  const sock = useLiveSocket();
  return useSyncExternalStore(
    (cb) => {
      if (!sock) return () => undefined;
      return sock.onStatus(() => cb());
    },
    () => sock?.getStatus() ?? "idle",
    () => "idle",
  );
}

// Subscribe to a single channel for the lifetime of the component.
// Pass `null` to skip (useful while waiting for SSR data, etc.).
export function useChannel(channel: Channel | null, handler: (ev: SnapshotEvent) => void): void {
  const sock = useLiveSocket();
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    if (!sock || !channel) return;
    const unsub = sock.subscribe(channel, (ev) => handlerRef.current(ev));
    return unsub;
  }, [sock, channel]);
}
