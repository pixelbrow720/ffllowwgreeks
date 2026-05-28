# Workspace docs

Cross-cutting documentation that doesn't belong inside `backend/` or `web/`.

Backend's own deep subsystem docs live in [../backend/docs/](../backend/docs/) — don't duplicate them here.

## Planned structure

```
docs/
├── README.md                       this file
├── architecture/                   workspace-level architecture (backend ↔ web ↔ flowjob.id)
├── methodology/                    math validation, competitor crosscheck, research notes
│   └── (planned: competitor-crosscheck.md, math-validation.md)
├── integration/                    flowjob.id ↔ FlowGreeks integration specs
│   └── (planned: flowjob-api-keys.md)
└── design/                         UX research, redesign proposals, screenshots
    └── (planned: dashboard-redesign.md)
```

Folders will be created on demand when their first document lands. Empty folders left out to keep `git status` clean.
