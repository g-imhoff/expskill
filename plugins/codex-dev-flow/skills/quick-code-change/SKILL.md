---
name: quick-code-change
description: Use only after an explicitly selected Quick route or exact router handoff from route-code-change for this accepted bounded change; implement it with independent review and verification. Do not trigger for arbitrary coding requests.
---

# Quick Code Change

Use only after accepted Quick selection or exact `$route-code-change` handoff.

GPT-5.6 Sol Max orchestrator makes one concise plan, preserves unrelated dirty work, implements in current working tree, and runs focused checks. Do not create worktree or delegate planning/implementation. Use test-first behavior for features and fixes; pure executable configuration without executable unit boundary uses smallest meaningful validation.

Next, concurrently dispatch exactly these independent gates:

- `devflow-reviewer`: send accepted request, applicable repository policy, scoped diff, and concise focused-check evidence; remain read-only; return actionable findings plus `Ready` or `Not ready`.
- `devflow-verifier`: send accepted criteria and exact relevant commands; record pre/post tracked state and exit evidence; make no tracked-source edits.

Adjudicate evidence. Fix confirmed findings in orchestrator. After fixes, rerun both gates concurrently; stale results cannot certify changed code. Never replace a gate with self-review or one agent.

Do not commit without explicit user authorization. Continue autonomously before the threshold; when a required important product decision falls outside acceptance or the same blocker survives three occurrences, ask for help.

Final handoff states implementation evidence, focused checks, independent review result, independent verification result, tracked/commit state, and remaining concerns. Claim success only when review is `Ready` and verification is `PASS`.
