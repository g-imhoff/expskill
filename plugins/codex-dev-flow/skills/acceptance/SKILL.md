---
name: acceptance
description: Use only for an explicit acceptance phase; perform only test definition and stay inactive for other phases or tasks.
---

# Acceptance

Turn a plan into observable acceptance criteria and a useful test shape.

## Procedure

1. Convert each accepted behavior into a precise criterion with positive and negative cases.
2. Write or update a failing test, or specify the exact test that will expose the missing behavior.
3. Record fixtures, commands, evidence, and the boundary that production implementation must respect.

No production changes are made in this phase. This phase does not invoke another skill, open another skill, or run another skill.

## Typed next-skill outcome handoff

The selected phase is always `acceptance`. Every outcome emits exactly five fields: `schema`, `selected_phase`, `reason_code`, `next_skill`, and `status` of `phase-handoff-v1`.

- success/ready: `reason_code: tests-ready`; `next_skill: implement`.
- findings/failure: `reason_code: findings-failure`; `next_skill: acceptance` (the owning phase); do not advance.
- blocked: `reason_code: blocked`; `next_skill: none`; stop.
- user-decision: `reason_code: user-decision`; `next_skill: none`; ask the user.

The handoff remains typed with `status: handoff` and no additional outcome field.
