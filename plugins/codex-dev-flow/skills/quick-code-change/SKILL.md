---
name: quick-code-change
description: Use only after an explicitly selected Quick route or exact router handoff from route-code-change for this accepted bounded change; implement it with independent review and verification. Do not trigger for arbitrary coding requests.
---

# Quick Code Change

Use only after explicit Quick selection or exact `$route-code-change` handoff.

GPT-5.6 Sol Max plans concisely, preserves dirty work, implements in the current tree, and runs focused checks. Do not create a worktree or delegate implementation. Test behavior first; validate executable configuration meaningfully.

For each review, resolve bundled `../../scripts/read_only_agent.py`. Store the request, repository policy, scoped diff, and check evidence in a prompt file outside the repository. Start concurrently:

- `python3 ../../scripts/read_only_agent.py reviewer --repo <git-root> --prompt-file <file>` from the resolved path; use its status and stdout.
- `devflow-verifier`: send criteria and exact commands; record tracked state and exit evidence; make no tracked-source edits.

Adjudicate evidence and fix confirmed findings. After changes, rerun both gates concurrently; stale evidence cannot certify code. Never substitute self-review or one agent.

Do not commit without explicit user authorization. Continue autonomously before the threshold; when a required important product decision falls outside acceptance or the same blocker survives three occurrences, ask for help.

Final handoff states implementation, checks, gates, tracked/commit state, and concerns. Success requires `Ready` review and `PASS` verification.
