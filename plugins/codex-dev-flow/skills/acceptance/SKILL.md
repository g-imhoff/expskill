---
name: acceptance
description: Define tests and acceptance evidence before production changes begin.
---

# Acceptance

Turn a plan into observable acceptance criteria and a useful test shape.

## Procedure

1. Convert each accepted behavior into a precise criterion with positive and negative cases.
2. Write or update a failing test, or specify the exact test that will expose the missing behavior.
3. Record fixtures, commands, evidence, and the boundary that production implementation must respect.

No production changes are made in this phase. This phase does not invoke another skill, open another skill, or run another skill.

## Typed handoff

Return a typed next-skill handoff containing:

- `schema: phase-handoff-v1`
- `selected_phase: acceptance`
- `reason_code: tests-ready`
- `next_skill: implement`
- `status: handoff`
