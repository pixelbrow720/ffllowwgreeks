# Reference documentation

> Detailed, validated reference for every subsystem in FlowGreeks. **Validated against commit `3e5b0ec` (2026-05-27).**

This folder is the deepest layer of project documentation. The other docs (in [`docs/`](../) one level up) cover roadmap, data model, compute model, architecture overview, openapi spec. This folder covers **how the implemented system actually works right now** — file by file, with line citations.

## How to read it

Start with [`00-system-overview.md`](00-system-overview.md). It maps the binary topology + NATS subjects + storage + concurrency model.

After that, follow the recommended order:

| # | Doc | What you'll learn |
|---|---|---|
| 00 | [`00-system-overview.md`](00-system-overview.md) | Binary topology, NATS subjects, storage, latency budget |
| 01 | [`01-data-pipeline.md`](01-data-pipeline.md) | How a tick gets from Databento to a websocket client |
| 02 | [`02-auth.md`](02-auth.md) | Signup / login / refresh rotation / family revocation / lockout |
| 03 | [`03-math-pipeline.md`](03-math-pipeline.md) | Black-Scholes → IV solver → Greeks |
| 04 | [`04-dealer-model.md`](04-dealer-model.md) | DPI, Charm Clock, GEX, Pin, Simulator |
| 05 | [`05-time-machine.md`](05-time-machine.md) | Replay session + backtest engine |
| 06 | [`06-alerts-engine.md`](06-alerts-engine.md) | Rules → Triggers → sinks (broker, webhook + SSRF guard) |
| 07 | [`07-defense-in-depth.md`](07-defense-in-depth.md) | 7-layer security model, layer by layer |
| 08 | [`08-deployment-ops.md`](08-deployment-ops.md) | Docker compose, k8s probes, graceful shutdown, CI/nightly |
| 09 | [`09-observability.md`](09-observability.md) | Trace ids, audit log, full metric catalog, alert rules, investigation playbook |

## Citation contract

Every diagram, every claim, points at the code that proves it:

> Constants ([`types.go`](../../internal/auth/types.go)):
> ```
> LockoutThreshold  = 10
> LockoutDuration   = 15 * time.Minute
> ```
>
> Implementation: [`handlers.go:119`](../../internal/auth/handlers.go#L119).

If a citation looks stale (line number drift, file moved), **the doc is wrong, not the code**. Open the file, find the new location, fix the citation in the same change that moved the code. CI does not enforce this — humans do.

## How to update it

When you change the code:

1. **Find the affected diagram / table.** Each doc's table-of-contents header lists the source files it covers. If your file is in that list, you may have invalidated something.
2. **Verify the cited line numbers still say what the doc claims.** Use `sed -n` or just open the file.
3. **Update the citation + prose.** Keep the doc readable cold — a new reader should still understand the intent.
4. **Bump the "Validated against commit" header in the affected file** to your new commit hash. The validation tag is the single most useful field — readers can `git diff` from that commit forward to see what's drifted.

If you add a new subsystem, add a new numbered file (`10-...md`) and an entry in this README's table.

## Audience

These docs are for:
- **Future Claude Code sessions** picking up after compaction or in a new conversation.
- **The solo dev** trying to remember why a particular invariant exists at 2am.
- **Future contributors** who weren't here when each design decision landed.

They are deliberately **not** for end users — that surface is the OpenAPI spec ([`docs/openapi.yaml`](../openapi.yaml)) and the eventual frontend mockups. End users see API contracts; this folder explains the inside.

## What's deliberately not here

- **Roadmap / milestones** — see [`docs/ROADMAP.md`](../ROADMAP.md).
- **Math derivations** — see [`docs/COMPUTE_MODEL.md`](../COMPUTE_MODEL.md). This folder cites the implementations of those formulas; it doesn't re-derive them.
- **Schemas / DDL** — see [`docs/DATA_MODEL.md`](../DATA_MODEL.md) and [`scripts/migrations/`](../../scripts/migrations/).
- **Architecture diagrams (high-level)** — see [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md). This folder is the **detailed** layer beneath that doc.
- **Tech-stack rationale** — see [`docs/STACK.md`](../STACK.md).
- **Per-PR change history** — that's [`CHANGELOG.md`](../../CHANGELOG.md).
- **Cross-session work state** — that's [`docs/PROGRESS.md`](../PROGRESS.md) + [`HANDOFF.md`](../../HANDOFF.md).
- **Review findings** — that's [`docs/REVIEW.md`](../REVIEW.md).

This folder answers "how does X work?". The files above answer "where are we?", "why this stack?", "what's left?", "what changed?".

## Validation checklist

Before claiming this folder is current, verify:

- [ ] `go test ./...` is green
- [ ] `go vet ./...` is clean
- [ ] `staticcheck ./...` is clean
- [ ] `govulncheck ./...` reports no findings
- [ ] The "Validated against commit" header in each file matches `git rev-parse --short HEAD`
- [ ] Each cited line number `sed -n 'Np' <file>` still shows the expected content
- [ ] No file in this folder references a struct, function, or type that has been renamed or deleted

Last verification on commit `3e5b0ec` — all green.
