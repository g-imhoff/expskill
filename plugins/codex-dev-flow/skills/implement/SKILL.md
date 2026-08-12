---
name: implement
description: Use only for an explicit implement phase; perform only one accepted change and stay inactive for other phases or tasks.
---

# Implement

Apply exactly one accepted brief on one owned branch while preserving unrelated work.

## Procedure

1. Confirm the accepted interface, brief, owned files, planned test and proof strategy, and any available pre-change evidence; return any changed decision before editing.
2. Create or update planned tests; preserve pre-change failure evidence when feasible, apply the minimal production implementation, and make the accepted green checks pass. If meaningful RED is not feasible, record why and use the accepted alternative proof.
3. Keep the diff bounded, record commands and results, and prepare the change for read-only review.

There is no delegation and no scope expansion. Do not change an accepted interface without returning the decision. This phase does not invoke another skill, open another skill, or run another skill.

The implementation is complete when the planned tests and accepted checks are
green.

## Plan Graph contract

Read the canonical Plan Graph by workflow ID, graph revision, repository branch, and commit before editing. Resolve its helper from this loaded skill at `../../scripts/plan_graph.py`; `plugins/codex-dev-flow/scripts/plan_graph.py` is only the source-package locator. Return a worker receipt bound to workflow ID, graph revision, node, branch, commit, exact commands, exit evidence, and proof result. The implementer does not write canonical state; the one coordinator applies receipts with `apply_updates` after validation.
