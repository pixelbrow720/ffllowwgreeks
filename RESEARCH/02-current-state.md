# 02 · Current State (2026-05-29)

## Truth status

**Backend:** production-grade plumbing, math-correct on real Feb-2026 SPX archive replay. Ships.
**Frontend:** working but not aesthetically right. Brow has flagged the dashboard 3+ times as inadequate. Audit found 11 P0/P1 issues, 8 fixed in commit `aef4424`. Still iterating.
**Vendor:** Databento OPRA account locked. Live verification BLOCKED until unlock. Replay against 9-day archive is the only working mode.

## Repo state

- Branch: `main`
- Remote: `https://github.com/pixelbrow720/ffllowwgreeks`
- Commit count: 14 commits ahead of pre-session baseline
- Working tree: clean (as of doc snapshot)
- Build: `go test ./... ` 19/19 packages green · `go vet` clean · `npm run lint` zero warnings · `npm run build` clean

## Commit log this session (chronological)

```
aef4424 fix(web): audit-driven P0/P1 dashboard polish
b5336a5 fix(web): GEX panel fills full height + 1min throttle + RTH 20:30 WIB
f9255f4 feat: signal log seed + spot history backfill + GEX/DPI swap
24c7fc1 feat(web): dashboard redesign v2 - landing-aligned chrome
c8b748f chore(skills): vendor frontend-design + ui-ux-pro-max skills
c4135bd feat(web): Bloomberg-grade dashboard redesign
71332f8 fix: middleware order + history store stability
83b2cbb docs: session log for 2026-05-29 multi-agent fan-out batch
b4cd8fc feat(web): typed REST + WS client and Sprint 1 panel migration
c811a1a feat(apikey): accept ?api_key= query param on WebSocket upgrades
6bbe9c1 feat(api): admin keys list/revoke surface on separate loopback port
e98e989 feat(calibrate): offline tool for fitting DPI/charm/pin normalizers
21c95bd fix(replay,compute): unblock historical math + trim state payload
0226631 feat: Enhance open-interest handling and concurrency in position tracking
```

## What works (verified)

### Backend pipeline
- Ingest → Postgres `ticks` hypertable: 211M rows, 9 trading days (2026-02-02 → 2026-02-13), 27 chunks, 2.4 GB on disk.
- Replay → compute → `dealer_state_1s`: real Feb-2026 SPX state at 1Hz, spot ~6970, NetGEX ~-54B, DPI 60→71, charm zone PEAK by mid-session. Verified via smoke run.
- Auth: 5-layer defense — IP rate limit → API key → per-key rate limit → body cap → audit log. `internal/apikey/` + `internal/api/admin.go`.
- Admin keys CRUD on loopback `:9090`, `ADMIN_TOKEN` gated.
- WS auth via `?api_key=` query param (RFC 6455 upgrade only, not plain HTTP).
- Default alert rules seeded on startup (DPI 70/30, NetGEX ±50B, charm zone PEAK, pin >40%).
- `/api/history/{symbol}` returns downsampled `dealer_state_1s` series with spot/dpi/walls/zero-γ.
- Offline calibration tool `cmd/calibrate` ready, consumer wired into `cmd/compute --calibration-config`.

### Frontend
- Typed REST client (`web/src/lib/api/client.ts`) generated from `backend/docs/openapi.yaml`.
- Typed WS client with exp-jitter reconnect + heartbeat watchdog + ref-counted channel subscribe.
- Snapshot store throttled to 1 update/min, force-flush on regime/charm-zone/pin transitions.
- 8h history backfill on mount via `/api/history`.
- 8 dashboard panels live: RegimeStrip, RailNav, GEXProfile, SpotChart, DPILive, KeyLevels, PinPanel, DPITimelineLive, SignalLog.

## What does NOT work

### Frontend (Brow's complaints + audit findings)

| # | Issue | Status |
|---|---|---|
| 1 | Spot value flickering 6884 → 6944 within 1 second | **Not fixed.** Throttle is 1 update/min but regime/charm flips force-flush, and replay event-time jumps cause "1 sec real time = many minutes event time" appearance |
| 2 | Layout still feels jelek to Brow despite v2 redesign | **Open.** Subjective. Audit confirms 1080px exact, panels filled. |
| 3 | DPI Timeline backfill works but charm zone column is FADING all the time | **Not investigated.** Likely the charm clock thresholds are wrong vs replay event-time speed. |
| 4 | "FADING" zone label color on screen looks pink-ish per Claude audit | Code says ink-muted; possible browser font rendering / CSS resolution drift. |
| 5 | Pin candidate panel mostly empty `—` | **By design**: pin engine triggers only when prob > threshold. Real Feb-2026 sessions rarely activate pin until last 30 min. |
| 6 | All 47+ events in Signal Log are CHARM_ZONE PEAK / DPI > 70 | Working as configured. Default rules + cooldowns 60-120s = mostly PEAK + DPI repeat. |
| 7 | NDX panel shows nothing | Replay only feeds SPX. NDX archive ingestion is partial (vendor blocker). |

### Backend

| # | Issue | Status |
|---|---|---|
| 1 | Replay slows once strike cache > 1500 | Open. IV solver throughput. Acceptable for offline use. |
| 2 | Pin `min_probability` from calibration JSON parsed but not applied | Open. Pin engine has no clean trigger-prob gate. Design call deferred. |
| 3 | Real calibration walk vs full archive not yet executed | Open. Operational, not code. `make calibrate` ready. |
| 4 | Race detector not run locally | Windows no gcc. CI handles `-race`. |
| 5 | `make demo-up` end-to-end not validated | Open. Manual binary launch works. |

### Vendor

| # | Blocker | Action |
|---|---|---|
| 1 | Databento OPRA account locked | Contact support. No technical fix. |
| 2 | NDX OI archive partial (02-12 missing) | Until OPRA unlocks, can't backfill. |

## Stack inventory

- **Postgres 16 + TimescaleDB** — ticks hypertable, dealer_state_1s, api_keys.
- **Redis** — Spot Window Cache for hot snapshot reads.
- **NATS JetStream** — pub/sub fabric between binaries.
- **Go 1.22+** — backend (api, compute, ingest, replay, calibrate).
- **Next.js 14 (App Router)** — web/.
- **Tailwind CSS** — design tokens via `tailwind.config.ts`.
- **Recharts** — every chart.
- **Databento `dbn-go` v0.9.1** — DBN parser.
- **Python bridge** — `backend/scripts/dbn_to_postgres.py` (workaround for `dbn-go` v1 InstrumentDef gap).

## Known gotchas

- `tmp/` and `*.exe` gitignored. `backend/api.exe`, `backend/replay_dbn.exe` exist on disk, never staged.
- `backend/data/` 2.4 GB DBN archive — gitignored.
- `.kilo/state/` chat transcripts — gitignored.
- `POSTGRES_*` env required for migrations. Source `.env` first.
- Replay binary path uses `dbn_to_postgres.py` Python script. Has its own venv at `backend/scripts/validation/.venv/`.
- LF→CRLF git warnings on every commit. Cosmetic; ignore.
- Commit `21c95bd` has UTF-8 BOM in subject. Cosmetic.

## Next steps

If continuing this codebase:
1. Real calibration run vs full 9-day archive (`make calibrate` then feed to compute).
2. Wire `pin_min_probability` into pin engine trigger gate.
3. Migrate Sprint 2 panels (DPIGauge, FlowTape, ForcedFlow real impl).
4. Add `/api/history-strikes/{symbol}` for time-machine GEX strike replay.
5. Contact Databento support to unlock OPRA.

If rebuilding from scratch:
1. Read [`10-rebuild-checklist.md`](10-rebuild-checklist.md). It's the ordered guide.
