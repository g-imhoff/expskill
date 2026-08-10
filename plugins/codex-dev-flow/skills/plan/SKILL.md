---
name: plan
description: Use only for an explicit plan phase; perform only planning and stay inactive for other phases or tasks.
---

# Plan

Shape an accepted direction into an ordered, reviewable plan.

## Procedure

1. Define the intended behavior, boundaries, dependencies, and affected files.
2. State assumptions and decisions; identify risks and the smallest useful test shape.
3. Order the work, name the acceptance gate, and describe the handoff to implementation.

No code, including production code, is written in this phase. This phase does not invoke another skill, open another skill, or run another skill.

## Typed next-skill outcome handoff

The selected phase is always `plan`. Every outcome emits exactly five fields: `schema`, `selected_phase`, `reason_code`, `next_skill`, and `status` of `phase-handoff-v1`.

- success/ready for a plan needing acceptance: `reason_code: bounded-change`; `next_skill: acceptance`.
- success/ready for a cross-cutting plan: preserve `reason_code: cross-cutting`; `next_skill: acceptance`.
- success/ready for an accepted bounded plan: preserve `reason_code: bounded-change`; `next_skill: implement`.
- findings/failure: `reason_code: findings-failure`; `next_skill: plan` (the owning phase); do not advance.
- blocked: `reason_code: blocked`; `next_skill: none`; stop.
- user-decision: `reason_code: user-decision`; `next_skill: none`; ask the user.

The handoff remains typed with `status: handoff` and no additional outcome field.
