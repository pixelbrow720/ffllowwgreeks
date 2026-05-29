---
name: resume-session
description: Bootstrap a fresh Claude Code session on FlowGreeks. Reads the workspace status documents in priority order, surfaces the current state, blockers, and next-step menu so work can resume immediately without losing context.
---

# Resume FlowGreeks Session

When invoked, follow this exact procedure to get oriented in a fresh session.

## 1. Read in priority order (parallel)

These are the workspace's source-of-truth documents. Read them all in parallel:

- [c:/FLOWGREEKS/HANDOFF.md](c:/FLOWGREEKS/HANDOFF.md) — most recent session, what's done, what's next
- [c:/FLOWGREEKS/CLAUDE.md](c:/FLOWGREEKS/CLAUDE.md) — workspace rules and structure
- [c:/FLOWGREEKS/backend/HANDOFF.md](c:/FLOWGREEKS/backend/HANDOFF.md) — backend-specific handoff (if exists)
- [c:/FLOWGREEKS/backend/docs/PROGRESS.md](c:/FLOWGREEKS/backend/docs/PROGRESS.md) — backend build log (last 100 lines)
- [c:/FLOWGREEKS/web/README.md](c:/FLOWGREEKS/web/README.md) — frontend status

## 2. Verify workspace state

Run in parallel:
- `git log --oneline -10` — recent commits
- `git status` — uncommitted work in flight
- `git branch --show-current` — current branch
- `ls c:/FLOWGREEKS/` — top-level structure intact?

## 3. Reconcile with stored memory

Search auto-memory at `~/.claude/projects/c--FLOWGREEKS/memory/` for any FlowGreeks-specific facts. If memory contradicts current files, trust the files (they're more recent).

## 4. Output a session-start summary

Report to the user in Bahasa Indonesia, structured like this:

```
## Sesi FlowGreeks — siap lanjut

### State sekarang
- Branch: <name>
- Last commit: <hash> <message>
- Uncommitted: <none / N files>

### Yang lagi in flight (kalau ada)
- ...

### Blocker yang masih aktif
- ...

### Menu next-step (dari HANDOFF.md)
- A: ...
- B: ...
- C: ...

Mau lanjut yang mana?
```

## 5. Don't do anything else yet

Wait for the user to choose. Don't auto-start any task. Don't pre-read files outside this procedure. The user picks the direction.
