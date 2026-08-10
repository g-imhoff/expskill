---
name: integrate
description: Use only for an explicit integrate phase; perform only accepted integration and stay inactive for other phases or tasks.
---

# Integrate

Combine accepted branches only when their review and verification evidence is ready and passes.

## Procedure

1. Confirm each branch is accepted, review is ready, verification passes, and ancestry is suitable.
2. Integrate only the approved changes in dependency order; preserve unrelated work and report conflicts.
3. Recheck the resulting checkout and state the final evidence. Make no silent or unapproved changes.

This phase does not invoke another skill, open another skill, or run another skill.

## Typed next-skill outcome handoff

The selected phase is always `integrate`. Every outcome emits exactly five fields: `schema`, `selected_phase`, `reason_code`, `next_skill`, and `status` of `phase-handoff-v1`.

- success/ready: `reason_code: accepted-branches`; `next_skill: none`.
- findings/failure: `reason_code: findings-failure`; `next_skill: integrate` (the owning phase); do not advance.
- blocked: `reason_code: blocked`; `next_skill: none`; stop.
- user-decision: `reason_code: user-decision`; `next_skill: none`; ask the user.

The handoff remains typed with `status: handoff` and no additional outcome field.
