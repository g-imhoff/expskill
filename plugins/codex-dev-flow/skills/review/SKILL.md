---
name: review
description: Use only for an explicit review phase; perform only read-only findings and stay inactive for other phases or tasks.
---

# Review

Inspect the owned diff read-only and report findings with evidence and impact.

## Procedure

1. Check the accepted brief, interface, tests, and file ownership against the diff.
2. Classify each finding by severity, explain impact, and cite the evidence that supports it.
3. Report whether the change is ready or not ready. For every finding, state a concrete correction the implementer can apply; do not make that correction in this phase.

This phase is read-only. No fix is made here. It does not invoke another skill, open another skill, or run another skill.

## Typed next-skill outcome handoff

The selected phase is always `review`. Every outcome emits exactly five fields: `schema`, `selected_phase`, `reason_code`, `next_skill`, and `status` of `phase-handoff-v1`.

- success/ready: `reason_code: completion-gates`; `next_skill: verify`.
- findings/failure: `reason_code: findings-failure`; `next_skill: implement` (the owning phase); do not advance.
- blocked: `reason_code: blocked`; `next_skill: none`; stop.
- user-decision: `reason_code: user-decision`; `next_skill: none`; ask the user.

The handoff remains typed with `status: handoff` and no additional outcome field.
