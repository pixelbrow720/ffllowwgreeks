# FlowGreeks — Full Frontend Build Prompt (for Claude Code / Opus 4)

> **How to use this:** Save this file as `FRONTEND_BUILD.md` di root repo `flowgreeks/`. Buka Claude Code, sebut "baca FRONTEND_BUILD.md lalu execute Phase 1 dulu, jangan langsung semua phase." Approve setelah tiap phase. Jangan minta dia execute semua sekaligus — output bakal berantakan.

---

## 0 — Konteks Project (BACA INI DULU SEBELUM CODING)

**FlowGreeks** = real-time options flow + dealer positioning intelligence platform, **laser-focused on 0DTE SPX & NDX**.

**Tagline:** "Read the Dealer."

**Diferensiator vs SpotGamma / GEXBot / MenthorQ:**
- **Predictive forced-flow** — translates dealer state into "what dealers MUST do next" in dollar notional
- **Charm Clock** — visualizes intraday charm decay window with directional bias
- **0DTE-only** — deliberately narrow surface, no 3,500-ticker bloat
- **Replay + Backtest** — time machine + signal backtest engine
- **Desktop-only** — terminal-grade aesthetic, no mobile compromise

**Parent product:** [flowjob.id](https://flowjob.id) — owns user accounts, billing, tier activation. FlowGreeks frontend authenticates via opaque API keys (`internal/apikey/`). **Frontend tidak handle signup, password, refresh token, tier gating** — semua di flowjob.id.

**Backend status:** Complete M0–M9. Stack: Go 1.22+, TimescaleDB, Redis 7+, NATS JetStream, Prometheus+Grafana. Latency target: p99 <100ms wire-to-WebSocket.

**Data feeds:** OPRA Pillar (US options all-strikes) + CME Globex MDP 3.0 (futures incl ES/NQ).

**Wajib baca file ini sebelum mulai (semua sudah ada di repo):**
- `CLAUDE.md` — project intro
- `HANDOFF.md` — current state
- `docs/openapi.yaml` — REST + WS contract (gunakan ini sebagai source of truth untuk types)
- `docs/DATA_MODEL.md` — schemas
- `docs/COMPUTE_MODEL.md` — math: DPI, charm clock, simulator, pin
- `docs/ARCHITECTURE.md` — system design
- `internal/api/` — handler shapes
- `internal/apikey/` — auth mechanism

**Reference UI mockup (sudah ada, jadikan baseline):**
- `../flowgreeks-mockup/ui/` — Next.js 14 mockup dengan landing page + dashboard. Pakai design tokens, mock data shape, dan component aesthetic dari sini. JANGAN ulang dari nol — clone struktur foldernya, lalu extend.

---

## 1 — Tech Stack (FIXED, JANGAN DIGANTI)

```
Framework:        Next.js 14.2.x (App Router, RSC + Client Components)
Language:         TypeScript 5.x (strict mode)
Styling:          Tailwind CSS 3.4.x + CSS variables for theme
UI Primitives:    Radix UI (Dialog, Dropdown, Tabs, Tooltip, Progress, Slot, Separator)
                  shadcn/ui patterns (copy-paste components, NOT a library)
Icons:            lucide-react@0.290.0 (DO NOT upgrade — newer versions break Next.js 14 barrel optimizer)
Charts:           Recharts 3.x (Area, Bar, Line, Composed, Scatter, Pie)
Tables:           TanStack Table v8 (untuk strikes table, alerts table, backtest results)
State:            Zustand 4.x (global) + TanStack Query 5.x (server state + WS subscription cache)
Forms:            react-hook-form 7.x + zod (validation, sync dengan openapi.yaml schemas)
Realtime:         Native WebSocket + reconnect logic + heartbeat (NO socket.io)
Animation:        Framer Motion 12.x (sparingly — scroll reveal, panel transitions)
Smooth scroll:    Lenis (landing page only)
Fonts:            Inter (display + body) + JetBrains Mono (numbers + code) via next/font/google
Code highlight:   shiki (untuk OpenAPI page + webhook payload preview)
Date/time:        date-fns 4.x (NO moment, NO dayjs)
Number format:    Intl.NumberFormat (built-in, NO numeral.js)
Validation:       zod 3.x
Testing:          Vitest + React Testing Library + Playwright E2E (later phase)
Lint:             ESLint flat config + Prettier 3 + @tailwindcss/eslint
```

**Folder convention:** App Router. Server Components by default. `"use client"` only when interactive (charts, forms, WS subscriptions).

---

## 2 — Design System (COPY EXACTLY)

### Colors (Tailwind config)

```typescript
colors: {
  bg: {
    base: "#08080a",
    card: "#0f0f12",
    hover: "#16161a",
    subtle: "#1c1c20",
  },
  line: {
    DEFAULT: "#26262a",
    strong: "#3a3a40",
  },
  ink: {
    high: "#f4f4f5",
    base: "#e4e4e7",
    muted: "#a1a1aa",
    faint: "#71717a",
    ghost: "#52525b",
  },
  signal: {
    up:   "#22c55e",
    down: "#ef4444",
    warn: "#f59e0b",
    info: "#3b82f6",
    pin:  "#a855f7",
  },
  brand: {
    DEFAULT: "#ff2a5b",
    hi:      "#ff6f8d",
    lo:      "#cc1e48",
    dim:     "rgba(255,42,91,0.12)",
  },
}
```

### Typography

- **Body/display:** Inter (Google Fonts, variable, subsets: latin). CSS var `--font-inter`.
- **Mono:** JetBrains Mono (Google Fonts, variable). CSS var `--font-jb-mono`.

### Critical utilities

- `.tabnum` — tabular nums + ss01, wajib di semua angka live
- `.bg-grid`, `.bg-grid-fine` — grid backgrounds
- `.text-gradient-brand` — gradient text untuk hero accents
- `.glow-hover` — card hover glow brand
- `.dpi-glow` — drop-shadow untuk DPI ring

### Layout rules

- Dashboard desktop only (min 1280px). Banner di mobile/tablet
- Border radius: sm=4 / md=6 / lg=8 / xl=12 / 2xl=16
- Spacing stick to Tailwind scale
- WCAG AA min contrast on all body text

---

## 3 — File Structure (TARGET)

```
flowgreeks/web/
├── app/
│   ├── (marketing)/  layout + landing/pricing/changelog/docs/about
│   ├── (app)/        layout + dashboard + 12 deep-dive routes
│   └── api/[...proxy]/route.ts  (dev only)
├── components/{primitives,charts,landing,dashboard,data-table}
├── lib/{api,ws.ts,stores,hooks,utils.ts,mock}
├── public/
└── tailwind.config.ts + tsconfig + next.config.mjs + .env.local.example
```

---

## 4 — Phase Plan (EXECUTE STRICTLY IN ORDER, STOP AFTER EACH PHASE FOR APPROVAL)

### Phase 1 — Foundation (1-2h)
Bootstrap Next 14 + Tailwind + design tokens + fonts + (marketing)/(app) layouts + 4 primitives. Lint/typecheck/build green. Screenshot.

### Phase 2 — Types + API + Mock (2-3h)
openapi-typescript codegen, typed fetcher, FlowWS client + reconnect, zustand stores, TanStack Query hooks, mock data behind env flag.

### Phase 3 — Landing (4-6h)
9 sections: Nav, Hero (split + spiral SVG), Marquee, Manifesto, Core Modules, Pipeline, Dashboard Preview, Pricing, Footer.

### Phase 4 — Dashboard Layout + 11 Panels (6-8h)
Sidebar + Topbar + 11 panels in 12-col grid. SpotChart, DPIGauge, CharmClock, KeyLevels, DPITimeline, ForcedFlow, GEXProfile, SignalLog, FlowTape.

### Phase 5 — Auxiliary Pages (6-8h)
13 deep-dive routes: alerts, webhooks, api-keys, openapi, simulator, replay, backtest, dpi, charm-clock, flow-tape, walls, signals, settings.

### Phase 6 — Realtime + Polish (3-4h)
Real API + WS, error boundaries, skeletons, empty states, keyboard shortcuts, command palette.

### Phase 7 — Testing + CI + Deploy (2-3h)
Vitest unit, Playwright E2E smoke, GitHub Actions, Vercel preview, README update.

---

## 5 — DO / DON'T

**DO:** read `../flowgreeks-mockup/ui/` references, ResponsiveContainer always, cn() utility, exact openapi types, server components default, sparing animation, realistic mock values.

**DON'T:** ship without screenshots, narrate diff in comments, `any` / `as any` / `@ts-ignore`, deep lucide imports, lucide > 0.290, mobile-responsive dashboard, localStorage API keys, 3D libs, CMS.

---

## 6 — Acceptance Criteria

- All 13 routes render without console errors
- lint + typecheck + build green
- All 11 dashboard panels visible at 1920x1080 with no overflow
- Mock → real API switch works via env var
- WebSocket reconnects within 8s
- Lighthouse landing: Perf ≥90, A11y ≥95, BP 100
- Playwright E2E smoke pass
- PR has screenshots
- Deployed to Vercel preview (or local Docker)

---

## 7 — Pitfalls

lucide @ latest breaks barrel · Recharts default styling ugly in dark · tabnum easy to forget · ReferenceLine label collisions · WS reconnect needs backoff · TanStack Query staleTime 0 flickers · Next 14 SSR fetch needs explicit cache · forced-dark Tailwind needs `darkMode: "class"` + `<html className="dark">` · Inter needs `tnum` 1 · marquee speed 38-48s

---

## 8 — Reference Files

`../flowgreeks-mockup/ui/` — tailwind.config.ts, globals.css, layout.tsx, lib/mock.ts, lib/utils.ts, primitives/Panel.tsx, dashboard/* (10 panels), landing/* (9 sections).

---

## 9 — Execution Instructions for Claude Code

1. Read all Section 0 files first
2. Confirm understanding (1-paragraph summary) — wait approval
3. Plan Phase 1 (file list + reasons) — wait approval
4. Execute Phase 1 only. Stop. Screenshot. Report.
5. Wait approval per phase
6. Never multi-phase in one go
7. Lint+typecheck+screenshot after each phase
8. Commit per phase, conventional commits
9. PR per phase or per page

---

## 10 — TL;DR

```
Setup: Next 14 + TS + Tailwind + Recharts + Lucide@0.290 + Radix + TanStack + Framer
Design: dark base, brand pink #ff2a5b, Inter + JBM, tabnum, terminal vibes
Pages: landing (TCC-style 9 sections) + dashboard (11 panels) + 12 deep-dive routes
Data: openapi-typescript codegen → typed fetcher → WS subject subscribe → TanStack Query cache
Auth: API key from flowjob.id cookie, NO signup/login UI
Quality: lint/type/build green every phase, screenshots mandatory
Deploy: Vercel preview per PR (or self-host Docker)
```

End of prompt. Build well, ship often, screenshot everything.
