# FlowGreeks Research Package

> **Audience:** you, the next research/build session, or another engineer cloning this work.
> **Status:** snapshot at 2026-05-29 / commit `aef4424` on `main`.
> **Scope:** every fact you need to rebuild or rethink the product without re-reading 14 commits and 50 source files.

This folder is **self-contained**. Read in order:

1. [`01-product-vision.md`](01-product-vision.md) — what FlowGreeks is, who it's for, the one-line value prop, the non-negotiables.
2. [`02-current-state.md`](02-current-state.md) — what exists today, every commit, every gap, every blocker. Truth.
3. [`03-data-pipeline.md`](03-data-pipeline.md) — Databento → Postgres → compute → API → web wiring.
4. [`04-databento-integration.md`](04-databento-integration.md) — schemas, datasets, pricing, the OPRA-locked vendor blocker.
5. [`05-math-model.md`](05-math-model.md) — Greeks, DPI 5-component, Charm Clock, Pin engine, calibration.
6. [`06-dashboard-spec.md`](06-dashboard-spec.md) — what the dashboard MUST show, panel-by-panel data contract.
7. [`07-landing-page.md`](07-landing-page.md) — current landing layout + copy + brand hooks.
8. [`08-design-system.md`](08-design-system.md) — colors, typography, layout rules, density baseline.
9. [`09-known-bugs.md`](09-known-bugs.md) — every bug Brow has flagged + every bug the audit caught.
10. [`10-rebuild-checklist.md`](10-rebuild-checklist.md) — a clean-slate build order if you choose to start over.
11. [`11-references.md`](11-references.md) — external URLs, skills paths, vendor docs.
12. [`12-faq-and-decisions.md`](12-faq-and-decisions.md) — every recorded decision with the why.

## reference/ — actual artifacts

- `snapshot-spx-sample.json` — live `/api/snapshot/spx` payload, real Feb-2026 data. Wire shape ground truth.
- `history-spx-sample.json` — live `/api/history/spx?...` payload, 480 samples × 12 fields. Ground truth for chart data.
- `tick-distribution-2026-02-12.txt` — per-hour tick count for the canonical replay day.
- `db-schemas.txt` — `ticks`, `dealer_state_1s`, `api_keys` Postgres schemas, exact columns.

If anything in the markdown contradicts these JSON / SQL snapshots, the snapshot wins. Update the markdown.

## Repo entry points

- Backend: [`backend/`](../backend/) — Go service.
- Frontend: [`web/`](../web/) — Next.js 14.
- Skills: [`.claude/skills/`](../.claude/skills/) — vendored design + UX skills (frontend-design, ui-ux-pro-max, ui-styling, etc.)
- Live state: [`HANDOFF.md`](../HANDOFF.md), [`CLAUDE.md`](../CLAUDE.md), [`backend/HANDOFF.md`](../backend/HANDOFF.md).

## How to use this package

**If you're rebuilding from scratch:** read 01 → 02 → 04 → 05 → 06 → 10. Skip 07/08 unless you keep the brand.

**If you're picking up where this stopped:** read 02 → 09 → 10. The "next steps" sections at the end of each doc are precisely the work backlog.

**If you're another engineer just orienting:** read 01 → 03 → 04. The rest is reference.
