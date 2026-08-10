---
name: verify
description: Run exact checks and record trustworthy evidence for the change.
---

# Verify

Run the exact commands named by acceptance and review, then record exit evidence.

## Procedure

1. Confirm the checkout, accepted scope, and commands before execution.
2. Run each exact command without changing tracked source; capture its exit status and relevant output.
3. Report PASS or a precise failure, including environment limits and the next decision needed.

There are no tracked-source edits in this phase. This phase does not invoke another skill, open another skill, or run another skill.

## Typed handoff

Return a typed next-skill handoff containing:

- `schema: phase-handoff-v1`
- `selected_phase: verify`
- `reason_code: completion-gates`
- `next_skill: integrate`
- `status: handoff`
