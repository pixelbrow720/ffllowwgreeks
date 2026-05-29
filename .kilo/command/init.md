---
description: Bootstrap session — read all FlowGreeks handoff docs in order
---

# /init — FlowGreeks session bootstrap

You are starting a fresh session on the FlowGreeks workspace at `C:\FLOWGREEKS`.

Read these files **in this exact order**, then summarize what you learned:

1. **`C:\FLOWGREEKS\CLAUDE.md`** — workspace rules, durable user constraints, big-picture architecture
2. **`C:\FLOWGREEKS\HANDOFF.md`** — most recent session log (top), historical session logs below, next-session menu
3. **`C:\FLOWGREEKS\backend\CLAUDE.md`** — backend-specific working agreements + directory layout
4. **`C:\FLOWGREEKS\backend\HANDOFF.md`** — backend TL;DR + deferred bugs + next-session menu
5. **`C:\FLOWGREEKS\backend\docs\PROGRESS.md`** — full chronological build log + decisions

After reading, run these in parallel to confirm workspace state:

```powershell
git log --oneline -10
git status --short
```

Then produce a concise (≤15 lines) status briefing that covers:

- **Where the project is** (last completed milestone in 1-2 sentences)
- **Uncommitted work** (if any — name files + 1-line description per group)
- **Active blockers** (deferred bugs from last session + vendor blockers like Databento OPRA)
- **Next-session menu** (the A/B/C/D options from `HANDOFF.md`, condensed to one line each)
- **Awaiting user decision** — end with: "Mau lanjut menu yang mana? (A/B/C/D atau yang lain)"

**Do not start any implementation work** until the user picks a direction. Do not propose a plan. Do not write code. The briefing is the entire deliverable for `/init`.

If any of the five files above are missing or stale (older than 7 days when the session log claims something newer), say so explicitly in the briefing instead of guessing.
