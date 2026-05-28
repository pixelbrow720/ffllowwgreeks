# HANDOFF — mockup3 premium revamp

> Read this in any new Claude Code session before touching the folder.

## Status

**v3.1 complete.** All five HTML pages ported. Design system + interactions layer + docs all match the v3.1 token set.

| File | State | Notes |
|---|---|---|
| `_v3.css` | ✅ v3.1 | Tailwind-modern palette (zinc base, emerald/red/amber accents, indigo/violet decorative-only). Components: `.lamp`, `.glare`, `.beam` (+ variants), `.tracing-beam`, `.statusbar`, `.kbd`, `.gradient-text-emerald`, `.split-char`, `.flicker-up/.flicker-down`. |
| `_v3.js` | ✅ v3.1 | 9 progressive enhancements: spotlight, reveal, char-split, number-ticker, hyper-text scramble, tracing beam, Cmd+K palette, live-flicker simulator, gex-bar wiring. |
| `landing.html` | ✅ v3.1 | Lamp hero + char-split title + KPI tickers + glare cards + tracing-beam pipeline + emerald-beam CTA + Cmd+K palette. |
| `dashboard.html` | ✅ v3.1 | Sidebar + 3-col content + persistent statusbar at bottom. Net GEX KPI wears `.beam.beam-red` (only beam-bordered widget). GEX dual-panel + DPI + Charm/Flow + Pin + tape + Cmd+K palette + live-flicker on every numeric. |
| `simulator.html` | ✅ v3.1 | Slider sidebar with grid-bg + char-split title. Glare result card with number-ticker headline. Three projection tiles (all tickers). Contributors table. Curl preview. Cmd+K palette. |
| `activate.html` | ✅ v3.1 | Left art column with lamp + grid-bg + step list with gradient connector rail. Right column with secret reveal + amber warn card + tabbed code samples (curl/js/py/ws). |
| `index.html` | ✅ v3.1 | Catalog page with lamp hero, char-split title, journey strip with arrow connectors, 4 page-cards with glare + open-arrow, 3 design-choice cards with swatches, integration card with indigo glow. |
| `DESIGN_SYSTEM.md` | ✅ v3.1 | Full v3.1 doc: tokens, components, JS hooks, page recipes, production-port notes. |

## Design intent (the user's brief — durable)

User pushed back on v3 calling it "lumayan" — wanted it to feel **wah**. Specific direction:

- **Premium tone** — Linear / Mercury / Bloomberg Terminal feel. Reference shadcn restraint + Aceternity polish + Magic UI components.
- **Scroll-trigger storytelling** on landing — pinned sections, hero text reveal, animated background.
- **Background animation** — lamp at hero top (Aceternity-style), animated grid that breathes, optional aurora as accent. Pick the right one per section, not all at once.
- **Fully functional terminal** — dashboard should look like something professionals actually use: status bar, Cmd+K palette, dense data, real keyboard shortcuts visible, live data jitter.
- **Color discipline ironclad** — three earned accents only (red short-γ, emerald long-γ, amber warn). Indigo + violet are decorative-only, hero ambient lighting only.

User constraints (durable, jangan ditanya ulang):
- Desktop only — never suggest responsive
- Tickers locked to SPX + NDX
- Tabular numerics with `tnum + ss01 + cv11` always on
- No build step (static HTML/CSS/JS, GSAP via CDN OK)
- Bahasa: Indonesia for chat, English in code/comments
- Solo dev, user pushes manually — JANGAN `git push`
- Autonomous mode

## What the v3.1 tokens give you

```
Surface:       --bg-0 (page) → --bg-4 (rare). Warmer slate base than v3.
Lines:         --line-faint, --line, --line-strong, --line-bright
Text:          --fg-0 → --fg-4 (zinc scale)
Accents:       --accent-short (#ef4444), --accent-long (#10b981), --accent-warn (#f59e0b)
Decorative:    --glow-indigo (#6366f1), --glow-violet (#a855f7)  ← hero ambient only
Type scale:    --t-display goes up to clamp(56px, 7vw, 108px) — much bigger hero feel
Easing:        --ease-emph (cubic-bezier(0.16, 1, 0.3, 1)) for premium emphasis
```

See `DESIGN_SYSTEM.md` for the full token + component + JS hook reference, plus production-port notes for the TS + React + shadcn/ui rebuild.

## Where this fits

This folder is the **frontend mockup track**. The backend lives at `../flowgreeks/` and is feature-complete pending Databento OPRA unlock. Auth has been pivoted to API keys (mockup uses `Authorization: Bearer fg_...` in any API examples). Read `../flowgreeks/HANDOFF.md` for backend context if relevant; otherwise this folder is self-contained.

## Next steps (when user is ready)

1. **Open all five pages in a browser** — `index.html` → `landing.html` → `activate.html` → `dashboard.html` → `simulator.html`. Verify the lamp + char-split + tickers + Cmd+K + statusbar + flicker all render and behave. Test ⌘K / Ctrl+K to open palette on every page.
2. **If anything feels off** — per-page tweaks land in the page's own `<style>` block. Cross-cutting changes go to `_v3.css`.
3. **Production port** — see "Notes for the next session" in `DESIGN_SYSTEM.md`. Tokens transfer cleanly to `tailwind.config.ts`. shadcn primitives map directly. Bespoke charts (GEX dual-panel, Charm Clock, DPI bars) need hand-rolled React + SVG.

---

**Last updated:** 2026-05-27 — v3.1 revamp complete. Five HTML pages, design system docs, no build step. Ready for browser smoke-test or production-stack port.
