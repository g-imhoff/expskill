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
- If the request is cross-cutting, select `plan`; its design must include
  observable behavior, planned tests, and proof before implementation.
- If work is at completion, select `review`, then require `verify`.
- If multiple branches are accepted, select `integrate`.

Protect every required gate: do not skip planned tests, review, or verification evidence.

## Reason-code map

Preserve the router reason code in the selected phase outcome:

- `requirements-ambiguous` selects `brainstorm`.
- `bounded-change` selects `plan` when an approach is needed, or `implement` when the accepted plan is ready.
- `cross-cutting` selects `plan`; that plan must preserve `cross-cutting` and include implementation-and-proof design.
- `tests-ready` selects `implement`.
- `completion-gates` selects `review`, then `verify`.
- `accepted-branches` selects `integrate`.

## One-transition coordination

Selects, explains, and invokes only one phase per transition. Open only the selected phase for this transition; never open more than that one phase.

The one coordinator resolves the graph helper from this loaded skill at `../../scripts/plan_graph.py`; `plugins/codex-dev-flow/scripts/plan_graph.py` is only the source-package locator. It discovers the workflow ID and graph revision on the current repository branch and applies updates with `apply_updates`. Each selected phase returns a receipt bound to branch and commit; the coordinator revalidates it against the canonical Plan Graph and never permits a worker to write canonical state.

Handle each outcome without advancing a failed gate: success/ready may recommend the mapped next phase; findings/failure returns to the owning phase; blocked stops; user-decision stops and asks the user.

Return one next-phase recommendation. After the gate, continue only when the canonical graph, workflow ID, graph revision, branch, and commit match; otherwise stop or ask for a user decision. This router does not self-certify implementation, review, or verification.
