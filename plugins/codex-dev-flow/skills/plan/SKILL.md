---
name: plan
description: Shape a bounded implementation approach without writing production code.
---

# Plan

Shape an accepted direction into an ordered, reviewable plan.

## Procedure

1. Define the intended behavior, boundaries, dependencies, and affected files.
2. State assumptions and decisions; identify risks and the smallest useful test shape.
3. Order the work, name the acceptance gate, and describe the handoff to implementation.

No code, including production code, is written in this phase. This phase does not invoke another skill, open another skill, or run another skill.

## Typed handoff

Return a typed next-skill handoff containing:

- `schema: phase-handoff-v1`
- `selected_phase: plan`
- `reason_code: bounded-change`
- `next_skill: acceptance`
- `status: handoff`
