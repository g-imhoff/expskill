---
name: integrate
description: Combine accepted work only after required review and verification pass.
---

# Integrate

Combine accepted branches only when their review and verification evidence is ready and passes.

## Procedure

1. Confirm each branch is accepted, review is ready, verification passes, and ancestry is suitable.
2. Integrate only the approved changes in dependency order; preserve unrelated work and report conflicts.
3. Recheck the resulting checkout and state the final evidence. Make no silent or unapproved changes.

This phase does not invoke another skill, open another skill, or run another skill.

## Typed handoff

Return a typed next-skill handoff containing:

- `schema: phase-handoff-v1`
- `selected_phase: integrate`
- `reason_code: accepted-branches`
- `next_skill: none`
- `status: handoff`
