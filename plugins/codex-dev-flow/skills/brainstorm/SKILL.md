---
name: brainstorm
description: Use only for an explicit brainstorm phase; perform only exploration and stay inactive for other phases or tasks.
---

# Brainstorm

Explore an uncertain development request without making production changes.

## Procedure

1. Restate the goal, known constraints, affected area, and missing information.
2. Ask focused questions and compare a small set of plausible approaches.
3. Record assumptions, risks, and the decision needed before planning.

No code is written in this phase. This phase does not invoke another skill, open another skill, or run another skill.

## Typed next-skill outcome handoff

The selected phase is always `brainstorm`. Every outcome emits exactly five fields: `schema`, `selected_phase`, `reason_code`, `next_skill`, and `status` of `phase-handoff-v1`.

- success/ready: `reason_code: requirements-ambiguous`; `next_skill: plan`.
- findings/failure: `reason_code: findings-failure`; `next_skill: brainstorm` (the owning phase); do not advance.
- blocked: `reason_code: blocked`; `next_skill: none`; stop.
- user-decision: `reason_code: user-decision`; `next_skill: none`; ask the user.

The handoff remains typed with `status: handoff` and no additional outcome field.
