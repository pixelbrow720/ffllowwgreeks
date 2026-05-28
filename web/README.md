# web — FlowGreeks frontend

Next.js 14 dashboard + landing for FlowGreeks. Consumes the Go backend over REST + WebSocket.

> Workspace-level rules live in [../CLAUDE.md](../CLAUDE.md). Frontend status, blockers, and next-step menu live in [../HANDOFF.md](../HANDOFF.md). Read both before starting.

## Run

```bash
npm install
npm run dev          # http://localhost:3000
npm run lint
npm run build
```

To exercise against a live backend, run `make demo-up` from [../backend/](../backend/) in another terminal — it boots the api binary plus a synthetic state publisher so the dashboard has data without Databento.

## Status

~35% complete. See [../HANDOFF.md](../HANDOFF.md) for the up-to-date checklist. Currently:

- Landing 9 sections rendering
- Dashboard skeleton with 11 panels rendering mock data shaped after [../backend/docs/openapi.yaml](../backend/docs/openapi.yaml)
- WebSocket client, real API wiring, and the 13 deep-dive routes are still pending

## Source-of-truth references

- [../backend/docs/openapi.yaml](../backend/docs/openapi.yaml) — REST + WS contract; types should be derived from this, not hand-written
- [../design-reference/mockup3/_v3.css](../design-reference/mockup3/_v3.css) + [_v3.js](../design-reference/mockup3/_v3.js) — design tokens + 9 progressive enhancements
- [../design-reference/mockup3/DESIGN_SYSTEM.md](../design-reference/mockup3/DESIGN_SYSTEM.md) — design system spec

## Cross-cutting rules (durable — restated for convenience)

These are enforced workspace-wide; full list in [../CLAUDE.md](../CLAUDE.md):

- **Desktop only.** No mobile, no responsive, no touch.
- **Tickers locked to SPX + NDX.**
- **Tabular numerics always on:** `font-feature-settings: "tnum", "ss01", "cv11"`.
- **Color discipline:** monochrome default. Three earned accents only — `--accent-short` (`#ef4444`), `--accent-long` (`#10b981`), `--accent-warn` (`#f59e0b`). Brand pink, indigo, violet are decorative-only ambient lighting.
- **Auth:** opaque API keys minted by flowjob.id. No signup, login, or tier UI here.

## Layout

```
src/
├── app/             Next.js App Router routes (landing + dashboard)
├── components/      panels, primitives, charts
└── lib/             fetchers, stores, utilities
```
