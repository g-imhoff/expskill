---
name: implement
description: Use only for an explicit implement phase; perform only one accepted change and stay inactive for other phases or tasks.
---

# Implement

Apply exactly one accepted brief on one owned branch while preserving unrelated work.

## Procedure

1. Confirm the accepted interface, brief, owned files, and failing test; return any changed decision before editing.
2. Use task-local red-green-refactor: write a failing test, run it, make the minimal implementation pass, and refactor only while green.
3. Keep the diff bounded, record commands and results, and prepare the change for read-only review.

There is no delegation and no scope expansion. Do not change an accepted interface without returning the decision. This phase does not invoke another skill, open another skill, or run another skill.

## Typed next-skill outcome handoff

The selected phase is always `implement`. Every outcome emits exactly five fields: `schema`, `selected_phase`, `reason_code`, `next_skill`, and `status` of `phase-handoff-v1`.

- success/ready: `reason_code: bounded-change`; `next_skill: review`.
- findings/failure: `reason_code: findings-failure`; `next_skill: implement` (the owning phase); do not advance.
- blocked: `reason_code: blocked`; `next_skill: none`; stop.
- user-decision: `reason_code: user-decision`; `next_skill: none`; ask the user.

The handoff remains typed with `status: handoff` and no additional outcome field.
