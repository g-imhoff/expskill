---
name: verify
description: Use only for an explicit verify phase; perform only exact checks and stay inactive for other phases or tasks.
---

# Verify

Run the exact commands named by acceptance and review, then record exit evidence.

## Procedure

1. Confirm the checkout, accepted scope, and commands before execution.
2. Run each exact command without changing tracked source; capture its exit status and relevant output.
3. Report PASS or a precise failure, including environment limits and the next decision needed.

There are no tracked-source edits in this phase. This phase does not invoke another skill, open another skill, or run another skill.

## Typed next-skill outcome handoff

The selected phase is always `verify`. Every outcome emits exactly five fields: `schema`, `selected_phase`, `reason_code`, `next_skill`, and `status` of `phase-handoff-v1`.

- success/ready: `reason_code: completion-gates`; `next_skill: integrate`.
- findings/failure: `reason_code: findings-failure`; `next_skill: implement` (the owning phase); do not advance.
- blocked: `reason_code: blocked`; `next_skill: none`; stop.
- user-decision: `reason_code: user-decision`; `next_skill: none`; ask the user.

The handoff remains typed with `status: handoff` and no additional outcome field.
