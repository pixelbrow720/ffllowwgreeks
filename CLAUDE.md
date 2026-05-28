# CLAUDE.md — Workspace

This file provides workspace-level guidance to Claude Code (claude.ai/code).
Project-specific guidance lives in [backend/CLAUDE.md](backend/CLAUDE.md) and any future `web/CLAUDE.md`.

## Workspace shape

This is a **single consolidated repository** holding three layers:

| Folder | Role | Source of truth |
|---|---|---|
| [backend/](backend/) | Go service — OPRA + GLBX ingest, Greeks/dealer compute, REST + WS API | [backend/CLAUDE.md](backend/CLAUDE.md), [backend/docs/PROGRESS.md](backend/docs/PROGRESS.md) |
| [web/](web/) | Next.js 14 frontend (landing + dashboard) | [web/README.md](web/README.md) |
| [design-reference/](design-reference/) | Static HTML mockups, read-only visual reference | n/a — do not modify |

Predecessor Python backend is archived at [archive/python-legacy/](archive/python-legacy/) — **not in active use**, kept for reference only.

## Project relationships

- `backend/` is feature-complete through M9 + post-M9 hardening. Hard-blocked on Databento OPRA unlock for live verification. Demo profile (`make demo-up`) runs api + synthetic state publisher so frontend work can proceed without Databento.
- `web/` consumes `backend/` over REST + WebSocket. Currently rendering mock data shaped after `backend/docs/openapi.yaml`. Real API wiring is pending.
- Auth model: **opaque API keys minted by flowjob.id** (parent product). FlowGreeks does not handle signups, passwords, or tier gating — see [backend/internal/apikey/](backend/internal/apikey/) and [backend/SECURITY.md](backend/SECURITY.md).

## When working on backend

`cd backend` and treat [backend/CLAUDE.md](backend/CLAUDE.md) as authoritative. Run quality gates from inside `backend/`:

```bash
make check                       # fmt + vet + lint + test
make demo-up                     # full demo stack
make build                       # → bin/api bin/ingest bin/compute bin/replay
```

After meaningful backend work, update [backend/docs/PROGRESS.md](backend/docs/PROGRESS.md).

## When working on frontend

`cd web` and follow Next.js conventions:

```bash
npm install
npm run dev                      # http://localhost:3000
npm run lint
npm run build
```

Frontend reference for design decisions:
- `design-reference/mockup3/_v3.css` + `_v3.js` — tokens + 9 progressive enhancements
- `design-reference/mockup3/DESIGN_SYSTEM.md` — design system spec
- `backend/docs/openapi.yaml` — REST + WS contract (source of truth for types)

## Cross-cutting user rules (durable — do not re-ask)

These apply across both `backend/` and `web/`:

- **Desktop only.** Never propose mobile, responsive, or touch behavior.
- **Tickers locked to SPX + NDX.** No equity options, no crypto, no FX, no RUT.
- **Tabular numerics always on**: `font-feature-settings: "tnum", "ss01", "cv11"`.
- **Bahasa Indonesia for chat, English for code + comments + docs.**
- **No `git push`.** User pushes manually. Commit locally; don't push.
- **No mocked DB tests** unless the test is unit-scoped to non-DB logic.
- **Hot path = no allocations in steady state** (Go side): pre-allocate, reuse, `sync.Pool` where applicable. Latency budget per stage: ingest 5ms / normalize 2ms / compute 30ms / fanout 10ms — total p99 < 100ms wire-to-WS.
- **Color discipline (web)**: monochrome default. Three earned accents only — `--accent-short` (#ef4444 red), `--accent-long` (#10b981 emerald), `--accent-warn` (#f59e0b amber). Brand pink, indigo, violet are decorative-only ambient lighting.

## Distribution model

FlowGreeks is **not sold standalone**. Access is granted to bootcamp graduates of the parent product **flowjob.id** via opaque API keys. Pricing and tier logic live entirely in flowjob.id.

API key integration with flowjob.id (Node.js + Next.js stack) is **pending implementation** — see [HANDOFF.md](HANDOFF.md) for the recommended pattern.

## Research gaps tracked

The following are known gaps the user wants closed before launch (none of them are unblocked yet — most need OPRA):

1. Math/quant validation against ground truth (DPI/Charm/Pin priors vs realised 0DTE flow) — needs OPRA.
2. Cross-validate Greeks vs `py_vollib` — offline, can ship now.
3. Property-based tests for math invariants — offline, can ship now.
4. Competitor methodology cross-check (SpotGamma, GEXBot, Squeeze Metrics) — offline, can ship now.
5. Dashboard UX redesign — design iteration, can ship now.
6. flowjob.id ↔ FlowGreeks API key integration — pending kawan's Node.js work.
