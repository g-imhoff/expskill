---
name: full-code-change
description: Use only after an explicitly selected Full route or exact router handoff from route-code-change for this accepted change; plan and execute independent work in externally managed, independently gated worktrees. Do not trigger for arbitrary coding requests.
---

# Full Code Change

Use only after accepted Full selection or exact `$route-code-change` handoff. Do not invoke superseded project-local workflow agents.

GPT-5.6 Sol Max owns exploration decisions, the conversation, proposition, decision recap, acceptance, dependency graph, integration, adjudication, and final outcome. For each bounded unknown, dispatch `devflow-explorer` with `agent_type: "devflow-explorer", fork_turns: "none"`; omit model and reasoning overrides. Send only one bounded question and the relevant repository scope. Independent explorations may run concurrently.

Before implementation planning, present the proposed solution, explicit decisions and assumptions, acceptance shape, and route consequences. Wait for explicit acceptance. Continue autonomously afterward unless a newly discovered important product decision falls outside acceptance; ask about that decision immediately. Ask for help only when the same operational blocker has occurred three times.

Create coherent review-sized tasks with dependencies, one writer, non-overlapping file ownership, acceptance criteria, and commit boundaries. Maximize useful parallelism without artificial microtasks.

For every review gate, dispatch `devflow-reviewer` with `agent_type: "devflow-reviewer", fork_turns: "none"`; omit model and reasoning overrides. Send only the accepted contract, brief, scoped diff, applicable repository policy, and concise verification evidence.

1. Dispatch one Luna Max `devflow-test-engineer` with `agent_type: "devflow-test-engineer", fork_turns: "none"` in its own external worktree first. It commits shared or cross-cutting acceptance tests that fail for the expected missing behavior and names every regression. Concurrently dispatch the reviewer and one Luna Max `devflow-verifier` with `agent_type: "devflow-verifier", fork_turns: "none"` for that test commit. The verifier proves RED is behavioral rather than environmental. Return confirmed findings to the test engineer and repeat both gates after fixes. Integrate the accepted test branch while preserving ancestry before creating implementation worktrees.
2. Create implementation worktrees from the integrated shared-test commit with the worktree helper. Dispatch one Luna Max `devflow-implementer` with `agent_type: "devflow-implementer", fork_turns: "none"` per coherent task. Independent, non-overlapping tasks may run concurrently; each implementer owns task-local red-green-refactor and its shared-test subset.
3. For every completed task, concurrently dispatch exactly one reviewer and one Luna Max `devflow-verifier` with `agent_type: "devflow-verifier", fork_turns: "none"`. Return confirmed findings to the original implementer. After any fix, repeat both gates; stale evidence cannot certify changed code. Integrate only Ready/PASS tasks in dependency order while preserving branch ancestry.
4. Dispatch one final whole-change reviewer and complete-suite `devflow-verifier` concurrently. Repeat both with their fix owner after confirmed findings. Success requires full-green evidence.

For every custom-agent dispatch, including repeated gates, supply the matching `agent_type` and `fork_turns: "none"`. Omit model and reasoning overrides so checked-in profiles control execution.

Call the worktree finish helper only after a branch is integrated, all applicable gates are green, and the integration checkout is clean. Preserve every dirty, failing, unmerged, or otherwise ineligible worktree and branch; report exact recovery paths. Never force-delete, prune unknown work, overwrite branches, or delete unmerged work.

Final handoff states implementation, integration, review, verification, ancestry, cleanup, tracked-state, and remaining-concern evidence. Claim success only when all required gates are Ready/PASS and the complete suite is green.
