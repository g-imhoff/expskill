---
name: brainstorm
description: Explore unclear goals and constraints before any code is written.
---

# Brainstorm

Explore an uncertain development request without making production changes.

## Procedure

1. Restate the goal, known constraints, affected area, and missing information.
2. Ask focused questions and compare a small set of plausible approaches.
3. Record assumptions, risks, and the decision needed before planning.

No code is written in this phase. This phase does not invoke another skill, open another skill, or run another skill.

## Typed handoff

Return a typed next-skill handoff containing:

- `schema: phase-handoff-v1`
- `selected_phase: brainstorm`
- `reason_code: requirements-ambiguous`
- `next_skill: plan`
- `status: handoff`
