---
name: verify
description: Use only for an explicit verify phase; perform only exact checks and stay inactive for other phases or tasks.
---

# Verify

Run the exact commands named by the plan, implementation evidence, and review,
then record exit evidence.

## Procedure

1. Confirm the checkout, accepted scope, and commands before execution.
2. Run each exact command without changing tracked source; capture its exit status and relevant output.
3. Report PASS or a precise failure, including environment limits and the next decision needed.

There are no tracked-source edits in this phase. This phase does not invoke another skill, open another skill, or run another skill.

Verification is complete when it has independently run the accepted checks and
retained exact evidence.

## Plan Graph contract

Resolve the graph helper from this loaded skill at `../../scripts/plan_graph.py`; `plugins/codex-dev-flow/scripts/plan_graph.py` is only the source-package locator. Read it at the exact workflow ID, graph revision, repository branch, and exact revision commit. Verification is independent: run the planned commands yourself, record each executed command and exit evidence, make no tracked-source edits, and do not rely on another agent's claims. Return a bound verification receipt; the coordinator alone applies it.
Verification does not write canonical state.
