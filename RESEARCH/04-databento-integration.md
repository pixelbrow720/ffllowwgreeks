# 04 · Databento Integration

## Vendor profile

**Databento** — wire-grade market data vendor. Ships normalized OPRA + CME feeds via WebSocket (live) and DBN files (historical). Pricing tier-based; FlowGreeks needs OPRA + GLBX.MDP3.

**URL:** https://databento.com
**Docs:** https://databento.com/docs
**Account status (2026-05-29):** **LOCKED.** Live verification blocked. Backfill blocked. Manual support recovery pending.

## Datasets used

### OPRA Pillar — `OPRA.PILLAR`

US options consolidated tape. Every strike, every quote, every trade across CBOE/AMEX/PHLX/etc. for every listed option.

**Subscription scope (current spec):**
- Symbols: `SPX`, `NDX` (cash index option chains)
- Schemas: `mbp-1` (top-of-book quote), `trades`, `ohlcv-1s`, `definition` (instrument metadata + OI snapshot)
- Stype-in: `raw_symbol` (OPRA root canonical)
- 0DTE filter: server-side, expiry-on-day only

**Volume:** ~23M quotes + 1M trades + ~50K OI snaps per RTH day per symbol.

### CME GLBX MDP 3.0 — `GLBX.MDP3`

CME Group market-data direct. Index futures (ES, NQ) + their options (we don't use SPX-on-future options — those are SPXW vs ESH6 cash-settled). Used for the **basis** that pulls SPX cash spot from ES front-mid.

**Subscription scope:**
- Symbols: `ES.FUT`, `NQ.FUT` (front-month + next via continuous contract symbology)
- Schema: `mbp-1`
- Cadence: tick-level

## DBN file archive (local, working state)

Path: `backend/data/databento/<dataset>/<YYYY-MM-DD>/*.dbn.zst`

```
backend/data/databento/
  OPRA.PILLAR/
    2026-02-02/
    2026-02-03/
    ...
    2026-02-13/
  GLBX.MDP3/
    2026-02-02/
    ...
```

**9 trading days × 2 datasets ≈ 2.4 GB on disk.**

After ingest into `ticks` hypertable: **211,395,854 rows** total. See [`reference/tick-distribution-2026-02-12.txt`](reference/tick-distribution-2026-02-12.txt) for one canonical day's tick distribution.

## The OI seed problem

Open Interest snapshots arrive once per session (US morning, ~11:30 UTC). The compute pipeline cannot compute dealer gamma without OI. So:

1. Replay must start from **before 11:30 UTC** (we use 11:29 UTC) so the OI seed lands.
2. The bus publisher must accept OI ticks. (Was a bug pre-`0226angle67`.)
3. PositionTracker must be concurrency-safe. (Was a bug pre-`0226631`.)

OI rows visible in [`reference/tick-distribution-2026-02-12.txt`](reference/tick-distribution-2026-02-12.txt) at `2026-02-12 11:00 UTC` hour bucket.

## The dbn-go v1 InstrumentDef gap

`dbn-go` v0.9.1 cannot decode the `InstrumentDef` v1 record Databento ships in OPRA. Workaround: a Python bridge.

**`backend/scripts/dbn_to_postgres.py`** — uses `databento` Python SDK (which handles v1) to read DBN files, normalize to a `feed.Tick`-compatible row, and bulk-COPY into Postgres `ticks`.

```bash
# Has its own venv:
cd backend/scripts/validation
.\.venv\Scripts\Activate.ps1
python ../../dbn_to_postgres.py --date 2026-02-12 --dataset OPRA.PILLAR
```

This is brittle and slow (Python <-> Postgres). When `dbn-go` ships v1 InstrumentDef support, `cmd/ingest` can be the single ingest path. Until then, the Python bridge is the only way to load historical archives.

## Symbology

OPRA: `SPX  260213C06900000` (SPX, 2026-02-13 expiry, Call, 6900.000 strike).
GLBX: `ESH6` (Mar 2026 ES quarterly), `ESM6` (Jun), `ESU6` (Sep), `ESZ6` (Dec).

NDX uses `NQ` instead of `ES`.

Rollover: third Friday of expiry month at 09:30 ET (cash settlement). `internal/replay/futures.go::FrontMonthContract` handles the calendar.

## Pricing notes

- OPRA Pillar: ~$2k-3k/month per user for live, archive separate.
- GLBX.MDP3: ~$500-1k/month per user for live, archive separate.
- DBN archive download: ~$0.10/GB.

These are sticker estimates; vendor negotiates volume.

## Live integration plan (when OPRA unlocks)

1. Verify SPX + NDX option mbp-1 feed populates `cmd/ingest`.
2. Verify ES + NQ front-month basis tracker pulls (cmd/ingest already wired).
3. Smoke run: `make demo-up` + live ingest + dashboard. Should match replay numbers within rounding.
4. Schedule daily DBN download for archival (cron, not part of hot path).
5. Calibrate normalizers via `make calibrate` against the realized session distributions.

## Vendor risks

- **Account lockout** (current): no path forward without support.
- **Pricing tier changes**: OPRA full feed is expensive. If the bill jumps, we cap subscription to SPX + NDX only (already the scope).
- **Schema breaks**: Databento has changed `InstrumentDef` versions before. `dbn-go` is third-party; depend on a frozen version.
- **OPRA outages**: rare but real. Ingest must drop-tolerate; dashboard must show OFFLINE clearly.

## Alternatives if Databento fails permanently

1. **Polygon.io** — covers OPRA via a different aggregation. Different schema, different pricing, different latency. Would require a `cmd/ingest-polygon` rewrite.
2. **Cboe LiveVol** — direct OPRA from the exchange. Higher cost, lower latency. Would replace ingest layer entirely.
3. **Build from CME ITCH for SPX-on-future options** — ES options are tradable; FlowGreeks could pivot to ES options dealer state if SPX cash options become inaccessible. But the math is different (delivery vs cash-settled).

## Action items

- [ ] Email Databento support to unlock OPRA account.
- [ ] Once unlocked: validate live ingest matches replay numbers within 0.5%.
- [ ] Set up daily DBN backfill cron (`scripts/backfill/`).
