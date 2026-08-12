---
name: review
description: Use only for an explicit review phase; perform only read-only findings and stay inactive for other phases or tasks.
---

# Review

Inspect the owned diff read-only and report findings with evidence and impact.

## Procedure

1. Check the accepted brief, interface, code, tests, and file ownership against the accepted plan and diff, read-only.
2. Classify each finding by severity, explain impact, and cite the evidence that supports it.
3. Report whether the change is ready or not ready. For every finding, state a concrete correction the implementer can apply; do not make that correction in this phase.

This phase is read-only. No fix is made here. It does not invoke another skill, open another skill, or run another skill.

## Plan Graph contract

Read-only review resolves the graph helper from this loaded skill at `../../scripts/plan_graph.py`; `plugins/codex-dev-flow/scripts/plan_graph.py` is only the source-package locator. Consume the workflow ID, exact graph revision, repository branch, and exact revision commit. Inspect the accepted graph and diff; this phase does not write canonical state. Return a findings receipt bound to those identities, severity, evidence, impact, correction, and ready or not ready disposition; the coordinator alone records it.
