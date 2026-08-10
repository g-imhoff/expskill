---
name: implement
description: Apply one accepted change with task-local tests and careful scope.
---

# Implement

Apply exactly one accepted brief on one owned branch while preserving unrelated work.

## Procedure

1. Confirm the accepted interface, brief, owned files, and failing test; return any changed decision before editing.
2. Use task-local red-green-refactor: write a failing test, run it, make the minimal implementation pass, and refactor only while green.
3. Keep the diff bounded, record commands and results, and prepare the change for read-only review.

There is no delegation and no scope expansion. Do not change an accepted interface without returning the decision. This phase does not invoke another skill, open another skill, or run another skill.

## Typed handoff

Return a typed next-skill handoff containing:

- `schema: phase-handoff-v1`
- `selected_phase: implement`
- `reason_code: bounded-change`
- `next_skill: review`
- `status: handoff`
