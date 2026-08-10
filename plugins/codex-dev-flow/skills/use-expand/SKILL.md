---
name: use-expand
description: Classify a development request and hand off one appropriate phase.
---

# Use Expand

Classify one transition for a development request and explain the reason for it.

## Stay inactive

Stay inactive when the user names an explicit phase, asks for a read-only action, or asks for a non-code task. Let the requested direct work proceed.

## Select one phase

- If requirements are ambiguous, select `brainstorm` and explain the reason.
- If the request is clear and bounded, select `plan` for an approach or `implement` when the approach is accepted.
- If the request is cross-cutting, select `plan`, require `acceptance`, and only then allow `implement`.
- If work is at completion, select `review`, then require `verify`.
- If multiple branches are accepted, select `integrate`.

Protect every required gate: do not skip acceptance, review, or verification evidence.

## One-transition handoff

Selects, explains, and invokes only one phase per transition. Open only the selected phase for this transition; never open more than that one phase.

The selected phase must return the semantic `phase-handoff-v1` object with `selected_phase`, `reason_code`, `next_skill`, and `status: handoff`. Consume semantic `phase-handoff-v1` only after checking that its selected phase and reason match the transition.

Return a typed next-skill recommendation. After the handoff, continue only when the next gate is satisfied; otherwise stop or ask for a user decision.
