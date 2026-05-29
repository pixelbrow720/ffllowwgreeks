# 11 · References

## In-repo

- `CLAUDE.md` — top-level rules and operating model. Read every session.
- `AGENTS.md` — multi-agent collaboration notes.
- `HANDOFF.md` — session-by-session state log.
- `backend/CLAUDE.md` + `backend/HANDOFF.md` — backend-specific.
- `backend/docs/PROGRESS.md` — phase and milestone tracker.
- `backend/docs/openapi.yaml` — REST + WS contract source-of-truth.
- `backend/SECURITY.md` — auth model, defense layers.

## Skills (vendored)

- `.claude/skills/frontend-design/` — Anthropics frontend design skill.
- `.claude/skills/ui-ux-pro-max/` — nextlevelbuilder UX skill (161 reasoning rules, 67 styles, 161 palettes, 57 font pairings, 99 UX guidelines).
- `.claude/skills/dashboard-screenshot/` — terminal-grade dashboard patterns.
- `.claude/skills/design/` + `.claude/skills/design-system/` — Anthropics design skill.
- `.claude/skills/ui-styling/` — Tailwind + shadcn integration patterns.
- `.claude/skills/banner-design/` + `.claude/skills/brand/` + `.claude/skills/slides/` — bundled siblings.

## External

### Vendor
- Databento — https://databento.com (currently locked OPRA account)
- Databento docs — https://databento.com/docs
- CME Group MDP3 — https://www.cmegroup.com/market-data/distributors/market-data-platform/

### Component library research
- 21st.dev — https://21st.dev/community/components
  - Sidebars, Dock, Bento grids, AI chat, Number tickers, Tooltip, Popover, Tabs, Empty States, Cards.
  - Patterns recreated inline in this repo, no new dependencies.

### Reference dashboards (visual benchmarks)
- SpotGamma — https://spotgamma.com (gamma exposure dashboards for retail)
- MenthorQ — https://menthorq.com (similar)
- Bloomberg Terminal — proprietary, learn from screenshots and Bloomberg Documentary
- TradingView — https://tradingview.com (chart density baseline)
- Bookmap — https://bookmap.com (depth-of-book visualization)

### Math + data references
- Black-Scholes — Hull, "Options, Futures and Other Derivatives", Ch 15.
- Charm / Vanna — same, Ch 19.
- Dealer gamma exposure — SpotGamma research notes (publicly archived).
- 0DTE flow analysis — JPM Marko Kolanovic notes (search "0DTE dealer hedging").
- OPRA Pillar protocol — https://opraplan.com.
- CME MDP 3.0 — https://www.cmegroup.com/confluence/display/EPICSANDBOX/MDP+3.0+-+Market+by+Order.

### Tooling
- Next.js 14 docs — https://nextjs.org/docs
- Tailwind CSS — https://tailwindcss.com/docs
- Recharts — https://recharts.org/en-US/
- pgx (Postgres driver) — https://github.com/jackc/pgx
- NATS JetStream — https://docs.nats.io/nats-concepts/jetstream
- TimescaleDB hypertables — https://docs.timescale.com/use-timescale/latest/hypertables/

## Internal scripts

- `backend/scripts/dbn_to_postgres.py` — Python bridge for DBN v1 InstrumentDef.
- `backend/scripts/jetstream_setup/` — NATS streams provisioning.
- `backend/scripts/synth_state/` — synthetic state generator (for dev when no replay).
- `backend/scripts/migrations/` — Postgres schema migrations.
- `tmp/run-api.ps1` / `tmp/run-compute.ps1` / `tmp/run-replay.ps1` — local dev helpers.
- `tmp/dashboard-audit/audit.js` — Playwright dashboard auditor. Run `node audit.js` from that folder.

## Repo this lives in

- GitHub: https://github.com/pixelbrow720/ffllowwgreeks
- Local: `C:\FLOWGREEKS`
