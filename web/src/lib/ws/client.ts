// LiveSocket — singleton WebSocket client for /ws/live.
//
// Protocol (see backend/internal/api/ws.go):
//   client → server: {action:"subscribe"|"unsubscribe", symbols:[...], kinds:[...]}
//   server → client: {type:"snapshot"|"snapshot.replay"|"ack"|"error"|"heartbeat", symbol, kind, ts_ns, data}
//
// A "channel" in this client is the pair `${symbol}:${kind}` (e.g. "spx:gex").
// Subscribe ref-counts handlers per channel: the first subscribe sends the
// upstream subscribe message, the last unsubscribe sends unsubscribe.
//
// Browsers can't set custom headers on a WebSocket upgrade, so the API key
// is passed as `?api_key=` query param. The backend currently leaves /ws/live
// open in dev; once the gate is wired the same shape works for prod.

import type { components } from "../api/schema";
import {
  decodeSnapshot,
  type Snapshot,
  type Symbol,
} from "../api/types";

export type ChannelKind = "gex" | "narrative" | "alert";
export type Channel = `${"spx" | "ndx"}:${ChannelKind}`;

export interface SnapshotEvent {
  channel: Channel;
  symbol: Symbol;
  kind: ChannelKind;
  ts_ns: number;
  // Decoded snapshot when kind === "gex". Other kinds expose `raw`.
  snapshot?: Snapshot;
  raw: unknown;
  isReplay: boolean;
}

interface WireEvent {
  type: "snapshot" | "snapshot.replay" | "ack" | "error" | "heartbeat";
  symbol?: string;
  kind?: string;
  ts_ns?: number;
  data?: components["schemas"]["StateSnapshot"];
  error?: string;
}

type Handler = (ev: SnapshotEvent) => void;
type StatusHandler = (s: SocketStatus) => void;

export type SocketStatus = "idle" | "connecting" | "open" | "closed" | "reconnecting";

const PING_INTERVAL_MS = 25_000;
const STALE_THRESHOLD_MS = 40_000;
const MAX_BACKOFF_MS = 30_000;
const BASE_BACKOFF_MS = 500;

export class LiveSocket {
  private url: string;
  private apiKey: string | null;
  private ws: WebSocket | null = null;
  private status: SocketStatus = "idle";

  // Channel → set of handlers. Ref-counted: first subscribe sends upstream
  // subscribe, last unsubscribe sends upstream unsubscribe.
  private handlers = new Map<Channel, Set<Handler>>();
  private statusHandlers = new Set<StatusHandler>();

  // Active channels confirmed/sent to the server. Cleared on disconnect
  // and resent on reconnect so handlers don't have to re-subscribe.
  private active = new Set<Channel>();
  private lastMessageAt = 0;
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private wantOpen = false;

  constructor(url: string, apiKey: string | null) {
    this.url = url;
    this.apiKey = apiKey;
  }

  connect(): void {
    this.wantOpen = true;
    this.openSocket();
  }

  close(): void {
    this.wantOpen = false;
    this.clearTimers();
    if (this.ws) {
      this.ws.onopen = null;
      this.ws.onclose = null;
      this.ws.onmessage = null;
      this.ws.onerror = null;
      try {
        this.ws.close(1000, "client closing");
      } catch {
        // ignore
      }
      this.ws = null;
    }
    this.setStatus("closed");
  }

  getStatus(): SocketStatus {
    return this.status;
  }

  onStatus(handler: StatusHandler): () => void {
    this.statusHandlers.add(handler);
    handler(this.status);
    return () => {
      this.statusHandlers.delete(handler);
    };
  }

  subscribe(channel: Channel, handler: Handler): () => void {
    let bucket = this.handlers.get(channel);
    if (!bucket) {
      bucket = new Set();
      this.handlers.set(channel, bucket);
    }
    bucket.add(handler);

    // First handler for this channel triggers upstream subscribe.
    if (bucket.size === 1) {
      this.sendSubscribe([channel]);
    }
    if (!this.wantOpen) this.connect();

    return () => {
      const cur = this.handlers.get(channel);
      if (!cur) return;
      cur.delete(handler);
      if (cur.size === 0) {
        this.handlers.delete(channel);
        this.sendUnsubscribe([channel]);
      }
    };
  }

  // -- internals --

  private setStatus(s: SocketStatus) {
    if (s === this.status) return;
    this.status = s;
    this.statusHandlers.forEach((h) => h(s));
  }

  private buildUrl(): string {
    if (!this.apiKey) return this.url;
    const sep = this.url.includes("?") ? "&" : "?";
    return `${this.url}${sep}api_key=${encodeURIComponent(this.apiKey)}`;
  }

  private openSocket(): void {
    if (typeof window === "undefined") return;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    this.setStatus(this.reconnectAttempts === 0 ? "connecting" : "reconnecting");

    let socket: WebSocket;
    try {
      socket = new WebSocket(this.buildUrl());
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = socket;

    socket.onopen = () => {
      this.reconnectAttempts = 0;
      this.lastMessageAt = Date.now();
      this.setStatus("open");

      // Re-subscribe to whatever we had. Do it before clearing `active`
      // so we send fresh subscribe messages for everything.
      const channels = Array.from(this.handlers.keys());
      this.active.clear();
      if (channels.length > 0) this.sendSubscribe(channels);

      this.startHeartbeat();
    };

    socket.onmessage = (ev) => {
      this.lastMessageAt = Date.now();
      let parsed: WireEvent;
      try {
        parsed = JSON.parse(ev.data) as WireEvent;
      } catch {
        return;
      }
      this.dispatch(parsed);
    };

    socket.onerror = () => {
      // onclose follows; don't reconnect twice.
    };

    socket.onclose = () => {
      this.clearHeartbeat();
      this.ws = null;
      this.active.clear();
      if (this.wantOpen) {
        this.scheduleReconnect();
      } else {
        this.setStatus("closed");
      }
    };
  }

  private dispatch(ev: WireEvent) {
    if (ev.type === "heartbeat" || ev.type === "ack" || ev.type === "error") return;
    if (!ev.symbol || !ev.kind) return;
    const sym = ev.symbol.toLowerCase() as "spx" | "ndx";
    if (sym !== "spx" && sym !== "ndx") return;
    const kind = ev.kind as ChannelKind;
    const channel = `${sym}:${kind}` as Channel;
    const bucket = this.handlers.get(channel);
    if (!bucket || bucket.size === 0) return;

    const out: SnapshotEvent = {
      channel,
      symbol: sym.toUpperCase() as Symbol,
      kind,
      ts_ns: ev.ts_ns ?? 0,
      raw: ev.data,
      isReplay: ev.type === "snapshot.replay",
      snapshot: kind === "gex" && ev.data ? decodeSnapshot(ev.data) : undefined,
    };
    bucket.forEach((h) => {
      try {
        h(out);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("[flowgreeks] subscriber threw", err);
      }
    });
  }

  private sendSubscribe(channels: Channel[]) {
    const fresh = channels.filter((c) => !this.active.has(c));
    if (fresh.length === 0) return;
    fresh.forEach((c) => this.active.add(c));
    this.sendControl("subscribe", fresh);
  }

  private sendUnsubscribe(channels: Channel[]) {
    const targets = channels.filter((c) => this.active.has(c));
    if (targets.length === 0) return;
    targets.forEach((c) => this.active.delete(c));
    this.sendControl("unsubscribe", targets);
  }

  private sendControl(action: "subscribe" | "unsubscribe", channels: Channel[]) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const symbols = new Set<string>();
    const kinds = new Set<string>();
    channels.forEach((c) => {
      const [s, k] = c.split(":");
      symbols.add(s);
      kinds.add(k);
    });
    try {
      this.ws.send(JSON.stringify({
        action,
        symbols: Array.from(symbols),
        kinds: Array.from(kinds),
      }));
    } catch {
      // socket already closing — onclose will handle reconnect.
    }
  }

  private startHeartbeat() {
    this.clearHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      const idle = Date.now() - this.lastMessageAt;
      if (idle > STALE_THRESHOLD_MS) {
        // Server dropped; force reconnect.
        try {
          this.ws?.close(4000, "stale");
        } catch {
          // ignore
        }
        return;
      }
      // Re-send the current channel set as a no-op subscribe; this keeps
      // intermediate proxies awake without inventing a new wire frame.
      const channels = Array.from(this.active);
      if (channels.length > 0) this.sendControl("subscribe", channels);
    }, PING_INTERVAL_MS);
  }

  private clearHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private clearTimers() {
    this.clearHeartbeat();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private scheduleReconnect() {
    if (!this.wantOpen) return;
    this.setStatus("reconnecting");
    this.reconnectAttempts++;
    const expo = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** Math.min(this.reconnectAttempts, 6));
    const jitter = Math.random() * 0.5 * expo;
    const delay = expo / 2 + jitter;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => this.openSocket(), delay);
  }
}
