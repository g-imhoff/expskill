---
name: quick-code-change
description: Use only after an explicitly selected Quick route or exact router handoff from route-code-change for this accepted bounded change; implement it with independent review and verification. Do not trigger for arbitrary coding requests.
---

# Quick Code Change

Use only after explicit Quick selection or exact `$route-code-change` handoff.

GPT-5.6 Sol Max plans concisely, preserves dirty work, implements in the current tree, and runs focused checks. Do not create a worktree or delegate implementation. Test behavior first; validate executable configuration meaningfully.

After implementation, concurrently dispatch exactly one reviewer and one verifier:

- Dispatch `devflow-reviewer` with `agent_type: "devflow-reviewer", fork_turns: "none"`; omit model and reasoning overrides. Send only the accepted contract, brief, scoped diff, applicable repository policy, and concise verification evidence.
- Dispatch `devflow-verifier` with `agent_type: "devflow-verifier", fork_turns: "none"`; omit model and reasoning overrides. Send criteria and exact commands; record tracked state and exit evidence; make no tracked-source edits.

Adjudicate evidence and fix confirmed findings. After changes, repeat both dispatches concurrently with the same arguments; stale evidence cannot certify code. Never substitute self-review or one agent.

Do not commit without explicit user authorization. Continue autonomously before the threshold; when a required important product decision falls outside acceptance or the same blocker survives three occurrences, ask for help.

Final handoff states implementation, checks, gates, tracked/commit state, and concerns. Success requires `Ready` review and `PASS` verification.
