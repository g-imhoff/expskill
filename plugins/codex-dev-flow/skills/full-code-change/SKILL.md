---
name: full-code-change
description: Use only after an explicitly selected Full route or exact router handoff from route-code-change for this accepted change; plan and execute independent work in externally managed, independently gated worktrees. Do not trigger for arbitrary coding requests.
---

# Full Code Change

Use only after accepted Full selection or exact `$route-code-change` handoff. Do not invoke superseded project-local workflow agents.

GPT-5.6 Sol Max owns exploration decisions, the conversation, proposition, decision recap, acceptance, dependency graph, integration, adjudication, and final outcome. Bounded unknowns may use parallel read-only `devflow-explorer` agents.

Before implementation planning, present the proposed solution, explicit decisions and assumptions, acceptance shape, and route consequences. Wait for explicit acceptance. Continue autonomously afterward unless a newly discovered important product decision falls outside acceptance; ask about that decision immediately. Ask for help only when the same operational blocker has occurred three times.

Create coherent review-sized tasks with dependencies, one writer, non-overlapping file ownership, acceptance criteria, and commit boundaries. Maximize useful parallelism without artificial microtasks.

1. Dispatch one Luna Max `devflow-test-engineer` in its own external worktree first. It commits shared or cross-cutting acceptance tests that fail for the expected missing behavior and names every regression. Concurrently dispatch exactly one read-only Sol XHigh `devflow-reviewer` and one Luna Max `devflow-verifier` for that test commit. The verifier proves RED is behavioral rather than environmental. Return confirmed findings to the test engineer and repeat both gates after fixes. Integrate the accepted test branch while preserving ancestry before creating implementation worktrees.
2. Create implementation worktrees from the integrated shared-test commit with the worktree helper. Dispatch one Luna Max `devflow-implementer` per coherent task. Independent, non-overlapping tasks may run concurrently; each implementer owns task-local red-green-refactor and its shared-test subset.
3. For every completed task, concurrently dispatch exactly one read-only Sol XHigh `devflow-reviewer` and one Luna Max `devflow-verifier`. Return confirmed findings to the original implementer. After any fix, repeat the same reviewer and verifier; stale gates cannot certify changed code. Integrate only Ready/PASS tasks in dependency order while preserving branch ancestry.
4. Run one final concurrent whole-change `devflow-reviewer` and complete-suite `devflow-verifier`. Repeat both with their fix owner after confirmed findings. Success requires full-green evidence.

Call the worktree finish helper only after a branch is integrated, all applicable gates are green, and the integration checkout is clean. Preserve every dirty, failing, unmerged, or otherwise ineligible worktree and branch; report exact recovery paths. Never force-delete, prune unknown work, overwrite branches, or delete unmerged work.

Final handoff states implementation, integration, review, verification, ancestry, cleanup, tracked-state, and remaining-concern evidence. Claim success only when all required gates are Ready/PASS and the complete suite is green.
