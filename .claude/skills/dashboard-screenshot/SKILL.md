---
name: dashboard-screenshot
description: Capture a full visual record of the FlowGreeks dashboard — all scenes (Pulse, Levels, Tape) at 1920×1080 — and save them with a date-stamped folder under docs/progress/screenshots/. Use before redesign work, before/after polish sessions, or to track visual progress over time.
---

# Dashboard Screenshot Playbook

Captures every dashboard scene at production viewport, stores under a date-stamped folder, and returns the paths.

## Prerequisites

Playwright MCP must be available. Dev server must be reachable at `http://localhost:3000`.

## Procedure

### 1. Ensure dev server is running

Check if `http://localhost:3000` is responsive. If not, start it in the background:

```
cd c:/FLOWGREEKS/web
npm run dev
```

Use a background bash with a Monitor for "Ready in" or "compiled successfully" before proceeding.

### 2. Set up output folder

Create `c:/FLOWGREEKS/docs/progress/screenshots/{YYYY-MM-DD}/` using today's date from the system context (the `currentDate` block).

If the folder already exists today, append a hyphen-suffix: `{date}-2`, `{date}-3` etc, so prior captures aren't overwritten.

### 3. Capture each scene

For each scene below, navigate via Playwright MCP, set viewport to 1920×1080, wait for the dashboard data to render (look for chart elements), and screenshot full-page:

| Scene | URL | Filename |
|---|---|---|
| Landing | `http://localhost:3000` | `landing.png` |
| Dashboard — Pulse | `http://localhost:3000/dashboard?scene=pulse` | `dashboard-pulse.png` |
| Dashboard — Levels | `http://localhost:3000/dashboard?scene=levels` | `dashboard-levels.png` |
| Dashboard — Tape | `http://localhost:3000/dashboard?scene=tape` | `dashboard-tape.png` |

If the routes use a different scene mechanism (e.g., scroll-snap horizontal slider rather than query params), inspect [c:/FLOWGREEKS/web/src/app/dashboard/page.tsx](c:/FLOWGREEKS/web/src/app/dashboard/page.tsx) first to find the actual switching mechanism. Adapt navigation accordingly (scroll-into-view a specific section, click a dock button, etc).

### 4. Capture console errors

After each navigation, capture browser console output via `browser_console_messages` (level: error). Save to `console-errors.txt` in the same folder.

### 5. Close browser

Always `browser_close` at the end — leaks otherwise.

### 6. Report

Return the list of paths and a one-line note per scene indicating any console errors or notable visual issues spotted (broken image, blank panel, layout obviously off).

```
## Screenshots captured

Output: docs/progress/screenshots/2026-05-29/
- landing.png — clean
- dashboard-pulse.png — 2 console errors (see console-errors.txt)
- dashboard-levels.png — clean
- dashboard-tape.png — flow tape panel empty (mock data not loading?)

Console errors: docs/progress/screenshots/2026-05-29/console-errors.txt
```

## What you don't do

- Don't critique the design — that's `dashboard-ux-designer` agent's job.
- Don't edit code.
- Don't push to git.
- Don't capture mobile viewports — desktop-only project.
