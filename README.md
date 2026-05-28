# FlowGreeks

Real-time options flow + dealer-positioning intelligence for **0DTE SPX & NDX**.

Distributed as an **add-on inside flowjob.id** — parent product owns user accounts, billing, and bootcamp activation. FlowGreeks authenticates via opaque API keys minted by flowjob.id; this workspace contains the intelligence engine + dashboard.

## Workspace layout

| Path | Role |
|---|---|
| [backend/](backend/) | Go service: OPRA + GLBX ingest, Greeks/dealer compute, REST + WebSocket API. Source of truth for product behavior. |
| [web/](web/) | Next.js 14 dashboard + landing. Consumes backend over REST + WS. Design implementation lives here. |
| [docs/](docs/) | Workspace-level cross-cutting docs (methodology, integration guides, redesigns). Backend's own deep docs live in [backend/docs/](backend/docs/). |
| [archive/](archive/) | Deprecated Python backend (predecessor of `backend/`). Not in active use. |

## Quickstart

### Backend (no Databento needed)

```bash
cd backend
cp .env.example .env             # edit POSTGRES_PASSWORD
make demo-up                     # postgres + redis + nats + api + synth_state
curl localhost:8080/health/ready
make demo-down
```

### Frontend (mock data)

```bash
cd web
npm install
npm run dev                      # http://localhost:3000
```

### Backend + frontend together

```bash
# terminal 1
cd backend && make demo-up

# terminal 2
cd web && npm run dev
```

## Status (2026-05-28)

- **Backend**: M0–M9 complete + post-M9 hardening + auth pivot to API keys. Hard-blocked on Databento OPRA unlock for live verification. See [backend/docs/PROGRESS.md](backend/docs/PROGRESS.md).
- **Frontend**: Landing 9 sections + dashboard skeleton with 11 panels rendering mock data. WebSocket client, real API wiring, 13 deep-dive routes still pending. See [HANDOFF.md](HANDOFF.md).
- **Math validation**: methodology specced, calibration vs ground truth waiting on OPRA unlock. Property tests + `py_vollib` parity tests open as offline work.

## Cross-cutting rules (durable)

- **Desktop only.** No mobile, no responsive.
- **Tickers locked to SPX + NDX.** No 3,500-ticker bloat.
- **Tabular numerics always on**: `font-feature-settings: "tnum", "ss01", "cv11"`.
- **Bahasa Indonesia for chat, English for code + comments + docs.**
- **No `git push`.** Commit locally; user pushes manually.
- **Color discipline (web)**: monochrome default, three earned accents only — `--accent-short` (red), `--accent-long` (emerald), `--accent-warn` (amber). Brand pink + indigo/violet decorative-only.
- **Hot path = no allocations in steady state** (Go side). Latency budget: ingest 5ms / normalize 2ms / compute 30ms / fanout 10ms — total p99 < 100ms wire-to-WS.

## Entry points for AI sessions

1. Read [CLAUDE.md](CLAUDE.md) — workspace-level instructions.
2. Read [HANDOFF.md](HANDOFF.md) — current state, pending work, blockers.
3. `cd` into the project you need (`backend/` or `web/`) and read its own CLAUDE.md / README.
