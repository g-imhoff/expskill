---
name: quick-code-change
description: Use only after an explicitly selected Quick route or exact router handoff from route-code-change for this accepted bounded change; implement it with independent review and verification. Do not trigger for arbitrary coding requests.
---

# Quick Code Change

Use only after accepted Quick selection or exact `$route-code-change` handoff.

GPT-5.6 Sol Max orchestrator makes one concise plan, preserves unrelated dirty work, implements in current working tree, and runs checks. Do not create a worktree or delegate planning or implementation. Use test-first behavior for features and fixes; for pure executable configuration without executable unit boundary, use smallest meaningful validation.

Next, concurrently dispatch exactly these independent gates:

- `devflow-reviewer`: send the accepted request, applicable repository policy, and scoped diff; remain read-only; return actionable findings plus `Ready` or `Not ready`.
- `devflow-verifier`: send accepted criteria and exact relevant commands; record pre/post tracked state and exit evidence; make no tracked-source edits.

Adjudicate evidence. Fix confirmed findings in the orchestrator. After each fix, repeat both gates concurrently; stale results cannot certify changed code. Never replace either gate with self-review or one agent.

Do not commit without explicit user authorization. Continue unless a new important product decision is outside scope or same blocker occurs three times; then ask for help.

Final handoff states implementation evidence, focused checks, independent review result, independent verification result, tracked/commit state, and remaining concerns. Claim success only when review is `Ready` and verification is `PASS`.
