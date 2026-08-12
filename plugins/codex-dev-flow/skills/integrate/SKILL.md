---
name: integrate
description: Use only for an explicit integrate phase; perform only accepted integration and stay inactive for other phases or tasks.
---

# Integrate

Combine accepted branches only when their review and verification evidence is ready and passes.

## Procedure

1. Confirm each branch is accepted, review is ready, verification passes, and ancestry is suitable.
2. Integrate only the approved changes in dependency order; preserve unrelated work and report conflicts.
3. Recheck the resulting checkout and state the final evidence. Make no silent or unapproved changes.

This phase does not invoke another skill, open another skill, or run another skill.

## Plan Graph contract

Resolve the graph helper from this loaded skill at `../../scripts/plan_graph.py`; `plugins/codex-dev-flow/scripts/plan_graph.py` is only the source-package locator. Consume the workflow ID, graph revision, live target branch, exact commit, join, and lane ownership. Integrate incrementally and target-side; verify gates, apply_updates through the one coordinator, clean lanes, and remove external worktrees only after accepted-on-target. Preserve unrelated dirty work and return a bound integration receipt.
Perform cleanup only after the join is accepted.
