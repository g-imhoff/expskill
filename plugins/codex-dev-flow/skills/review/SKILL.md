---
name: review
description: Inspect a completed change read-only and report actionable findings.
---

# Review

Inspect the owned diff read-only and report findings with evidence and impact.

## Procedure

1. Check the accepted brief, interface, tests, and file ownership against the diff.
2. Classify each finding by severity, explain impact, and cite the evidence that supports it.
3. Report whether the change is ready or not ready. Do not fix findings in this phase.

This phase is read-only. No fix is made here. It does not invoke another skill, open another skill, or run another skill.

## Typed handoff

Return a typed next-skill handoff containing:

- `schema: phase-handoff-v1`
- `selected_phase: review`
- `reason_code: completion-gates`
- `next_skill: verify`
- `status: handoff`
