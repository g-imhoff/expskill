---
name: use-expskill
description: Explain to the AI how to behave. ALWAYS load this skill before starting a conversation or after compacting.
---

Before doing anything ALWAYS load the `$unslop` skill.

## Rules index

Rules clearly state specific behavior in different cases. When the context fit the cases, those rules has to be ALWAYS loaded to be sure you follow the expect user behavior.

- `pin-scope.md` when entering repo work, before finishing work, when branch, HEAD, or dirty state may have changed.
- `isolate-writes.md` when concurrent writes exist, when creating worktrees, when integrating or cleaning lanes.
- `explicit-confirmation.md` when asking the user to authorize a write, push, publish, or merge.
- `bounded-fanout.md` when facts are missing and discovery is needed.
- `evidence-report.md` when saving proof, run folders, verdicts, or latest links.
- `subagent-usage.md` when handing work to a child agent.

## Routing 

Analyze the request of the user and follow this :

- Coding work : load `./references/coding.md`.
- For any other : load nothing.
