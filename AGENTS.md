# AGENTS.md

Cross-agent quick reference for the FlowGreeks workspace. Keep this short. The
deep rules and current session state live in [CLAUDE.md](CLAUDE.md) and
[HANDOFF.md](HANDOFF.md) — treat them as authoritative when this file is
silent.

## Workspace shape

This is a single repo with three layers. The root has **no** Makefile,
`package.json`, or `go.mod`. `cd` into the project before running anything.

| Path | Role |
|---|---|
| [backend/](backend/) | Go service: OPRA + GLBX ingest, Greeks/dealer compute, REST + `/ws/live`. Source of truth for product behavior. |
| [web/](web/) | Next.js 14 dashboard + landing. Consumes backend over REST + WS. |
| [archive/python-legacy/](archive/python-legacy/) | Deprecated predecessor. Not in active use. |
| [docs/](docs/) | Workspace-level cross-cutting docs. Backend-specific deep docs live in [backend/docs/](backend/docs/). |

## Reading order for a new session

1. [CLAUDE.md](CLAUDE.md) — durable workspace rules + architecture
2. [HANDOFF.md](HANDOFF.md) — current state, uncommitted work, blockers, next-session menu
3. [backend/CLAUDE.md](backend/CLAUDE.md) + [backend/HANDOFF.md](backend/HANDOFF.md) for Go work
4. [web/README.md](web/README.md) for frontend work
5. `git log --oneline -10` and `git status` to confirm tree state

A `/init` slash command in [.kilo/command/init.md](.kilo/command/init.md)
codifies this ritual.

## Durable cross-cutting rules

These get violated most often. Restated verbatim from CLAUDE.md so an agent
that only loads this file still gets them right:

- **Desktop only.** Never propose mobile, responsive, or touch behavior.
- **Tickers locked to SPX + NDX.** No equities, crypto, FX, RUT.
- **Tabular numerics always on:** `font-feature-settings: "tnum", "ss01", "cv11"`.
- **Bahasa Indonesia for chat, English for code + comments + docs.**
- **No `git push`.** User pushes manually. Commit locally only.
- **No mocked DB tests** unless the test is unit-scoped to non-DB logic.
- **Hot path = zero allocations in steady state** (Go). Latency budget per
  stage: ingest 5ms · normalize 2ms · compute 30ms · fanout 10ms · total
  p99 < 100ms wire-to-WS.
- **Color discipline (web):** monochrome default, three earned accents only —
  `--accent-short` (`#ef4444`), `--accent-long` (`#10b981`), `--accent-warn`
  (`#f59e0b`). Brand pink/indigo/violet are decorative ambient lighting only.
  Pending migration: ~98 stale `signal-up`/`signal-down`/`signal-warn` token
  references in `web/` per the new `tailwind.config.ts`.
- **Comments:** only when WHY is non-obvious. Do not narrate the diff.
- **Auth model:** opaque API keys minted by [flowjob.id](https://flowjob.id)
  (parent product). No signup, login, password, refresh token, or tier UI in
  this repo.

## Commands agents guess wrong

Backend (run from `backend/`):

```
make demo-up      # postgres + redis + nats + api + synth_state — no Databento needed
make demo-down
make check        # fmt + vet + lint + test — preferred quality gate
make build        # → bin/api bin/ingest bin/compute bin/replay
make test         # go test -race -timeout 60s ./...
make bench        # zero-alloc benchmarks
make replay-dbn DIR=data/databento/2026-02-02
make migrate-up   # needs POSTGRES_* env exported

# focused
go test ./internal/greeks/...
go test -run TestBlackScholes ./internal/greeks/...
go test -bench=. -benchmem -run=^$ ./internal/greeks/
```

Frontend (run from `web/`):

```
npm install
npm run dev       # http://localhost:3000
npm run lint
npm run build
```

No test runner is wired up in `web/` yet — don't invent `npm test`.
`openapi-typescript` is installed for codegen from
[backend/docs/openapi.yaml](backend/docs/openapi.yaml).

## Architecture

Pipeline (Go binaries glued by NATS JetStream):

```
OPRA / GLBX  →  cmd/ingest  →  NATS  →  cmd/compute  →  Postgres + Redis
                                                            ↓
                                                         cmd/api  →  REST + /ws/live  →  web/
```

`cmd/replay` swaps in for `cmd/ingest` on historical sessions; everything
downstream is unchanged. Hot-path packages live in
[backend/internal/](backend/internal/):

- `feed/` OPRA Pillar + GLBX MDP3 parsers (Databento `dbn-go`)
- `greeks/` Black-Scholes + IV (Brent) + analytic Greeks
- `dealer/` GEX, DPI 5-component, Charm Clock, Pin engine, forced-flow simulator
- `bus/` NATS pub/sub
- `store/` TimescaleDB hypertables (`ticks`, `dealer_state_1s`) + Redis SWC
- `api/` chi router, REST, `/ws/live`, `/ws/replay`
- `apikey/` opaque API-key auth (parent product mints rows in shared Postgres)

[backend/docs/openapi.yaml](backend/docs/openapi.yaml) is the single contract
source of truth for TypeScript types. Generate from it; don't hand-write.

## Gotchas the repo can't tell you on first glance

- **Race detector locally:** no gcc on Windows → `go test -race` may fail to
  compile locally. CI runs `-race` on every PR via
  [backend/.github/workflows/test.yml](backend/.github/workflows/test.yml).
  For local runs use plain `go test ./...`.
- **Don't commit binaries.** `tmp/`, `backend/tmp/`, and `*.exe` are
  gitignored. Two `.exe` files (`backend/api.exe`, `backend/replay_dbn.exe`)
  currently sit in the worktree — leave them, do not stage them.
- **Don't commit data.** `backend/data/` holds the 2.4 GB DBN archive
  (211M ticks, 9 trading days). Gitignored.
- **`.kilo/state/`** is gitignored — chat transcripts and per-session config
  don't belong in git.
- **`POSTGRES_*` env required for migrations.** `make migrate-up` interpolates
  `$POSTGRES_USER`, `$POSTGRES_PASSWORD`, `$POSTGRES_HOST`, `$POSTGRES_PORT`,
  `$POSTGRES_DB`. Source `.env` first or migrations silently target the wrong
  DB.
- **DBN replay workaround.** `dbn-go` v0.9.1 cannot decode the v1
  InstrumentDef Databento served. Bridge is
  [backend/scripts/dbn_to_postgres.py](backend/scripts/dbn_to_postgres.py)
  (Python, has its own venv at `backend/scripts/validation/.venv/`).
- **Three deferred replay bugs (math output is currently zero):** futures
  contract reconstruction, wall-clock vs event-time TTE, and NATS 1 MiB
  payload cap. Detailed in [HANDOFF.md](HANDOFF.md) and
  [backend/HANDOFF.md](backend/HANDOFF.md).
- **Hard blocker (vendor):** Databento OPRA account locked. Live verification
  + DPI/Charm/Pin calibration cannot proceed until unlock. Offline calibration
  is unblocked once the three replay bugs above are fixed.
- **`backend/docs/PROGRESS.md` is the cross-session journal.** Update it after
  meaningful backend work so the next session catches up.
- **Existing per-agent assets:** [.claude/agents/](.claude/agents/) and
  [.claude/skills/](.claude/skills/) hold Claude Code subagents (greeks
  validator, hot-path reviewer, web design guardian, etc.) and skills. Useful
  as reference for how specialized work is split out. Don't move or delete.

## What lives where else

- Full architecture: [backend/docs/ARCHITECTURE.md](backend/docs/ARCHITECTURE.md),
  [backend/docs/COMPUTE_MODEL.md](backend/docs/COMPUTE_MODEL.md),
  [backend/docs/DATA_MODEL.md](backend/docs/DATA_MODEL.md)
- Session-by-session log: [HANDOFF.md](HANDOFF.md) + [backend/docs/PROGRESS.md](backend/docs/PROGRESS.md)
- Product framing: [README.md](README.md)
- Security posture: [backend/SECURITY.md](backend/SECURITY.md)
