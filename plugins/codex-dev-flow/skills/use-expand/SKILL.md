---
name: use-expand
description: Use only for code or executable-configuration requests needing phase routing; stay inactive for explicit phase, read-only, and non-code requests.
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

## Reason-code map

Preserve the router reason code in the selected phase handoff:

- `requirements-ambiguous` selects `brainstorm`.
- `bounded-change` selects `plan` when an approach is needed, or `implement` when the accepted plan is ready.
- `cross-cutting` selects `plan`; that plan must preserve `cross-cutting` and recommend `acceptance`.
- `tests-ready` selects `implement`.
- `completion-gates` selects `review`, then `verify`.
- `accepted-branches` selects `integrate`.

## One-transition handoff

Selects, explains, and invokes only one phase per transition. Open only the selected phase for this transition; never open more than that one phase.

The selected phase must return exactly the five fields of semantic `phase-handoff-v1`: `schema`, `selected_phase`, `reason_code`, `next_skill`, and `status`. Consume semantic `phase-handoff-v1` only after checking that its selected phase and reason match the transition.

Handle each outcome without advancing a failed gate: success/ready accepts the mapped next skill; findings/failure returns to the owning phase; blocked returns `next_skill: none`; user-decision returns `next_skill: none` and asks the user.

Return a typed next-skill recommendation. After the handoff, continue only when the next gate is satisfied; otherwise stop or ask for a user decision.
