// Typed REST client for FlowGreeks. Reads base URL + API key from
// public env vars. Throws ApiError on non-2xx with a structured shape
// the panels can branch on.

import type { components, paths } from "./schema";
import {
  decodeLevels,
  decodeSnapshot,
  type Levels,
  type Snapshot,
  type Symbol,
} from "./types";

export interface ApiError {
  status: number;
  code: string;
  message: string;
}

export class ApiClientError extends Error implements ApiError {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = code;
  }
}

const DEFAULT_BASE = "http://localhost:8080";

export function getApiBase(): string {
  return process.env.NEXT_PUBLIC_FLOWGREEKS_API_BASE ?? DEFAULT_BASE;
}

let warnedNoKey = false;

export function getApiKey(): string {
  const k = process.env.NEXT_PUBLIC_FLOWGREEKS_API_KEY;
  if (!k) {
    if (!warnedNoKey) {
      // eslint-disable-next-line no-console
      console.error(
        "[flowgreeks] NEXT_PUBLIC_FLOWGREEKS_API_KEY is not set — public REST endpoints will work but auth-gated endpoints (/api/simulate/*, /api/alerts/*, /api/backtest/*) will 401",
      );
      warnedNoKey = true;
    }
    throw new ApiClientError(401, "NO_KEY", "FLOWGREEKS_API_KEY missing");
  }
  return k;
}

interface FetchOpts {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  // Some endpoints (snapshot, levels) are public and the gate skips them
  // even when APIKEY_ENABLED=true, so the key is optional.
  requireAuth?: boolean;
}

async function request<T>(path: string, opts: FetchOpts = {}): Promise<T> {
  const { method = "GET", body, signal, requireAuth = false } = opts;
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  if (requireAuth) {
    headers["Authorization"] = `Bearer ${getApiKey()}`;
  } else {
    // Best-effort: send the key if we have one so per-key rate limits
    // apply on public endpoints too. Don't throw if missing.
    const k = process.env.NEXT_PUBLIC_FLOWGREEKS_API_KEY;
    if (k) headers["Authorization"] = `Bearer ${k}`;
  }

  const res = await fetch(`${getApiBase()}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
    cache: "no-store",
  });

  if (!res.ok) {
    let code = `HTTP_${res.status}`;
    let message = res.statusText;
    try {
      const errBody = (await res.json()) as components["schemas"]["Error"];
      if (errBody?.error) {
        message = errBody.error;
        code = errBody.error;
      }
    } catch {
      // body wasn't json — keep the status text
    }
    throw new ApiClientError(res.status, code, message);
  }

  // 204 No Content
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

type SnapshotResp = paths["/api/snapshot/{symbol}"]["get"]["responses"]["200"]["content"]["application/json"];
type LevelsResp = paths["/api/levels/{symbol}"]["get"]["responses"]["200"]["content"]["application/json"];

export async function getSnapshot(symbol: Symbol, signal?: AbortSignal): Promise<Snapshot> {
  const wire = await request<SnapshotResp>(`/api/snapshot/${symbol.toLowerCase()}`, { signal });
  return decodeSnapshot(wire);
}

export async function getLevels(symbol: Symbol, signal?: AbortSignal): Promise<Levels> {
  const wire = await request<LevelsResp>(`/api/levels/${symbol.toLowerCase()}`, { signal });
  return decodeLevels(wire);
}
