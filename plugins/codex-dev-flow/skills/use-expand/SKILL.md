---
name: use-expand
description: Use only for code or executable-configuration requests needing lifecycle routing; stay inactive for explicit skills, read-only work, and non-code requests.
---

# Use Expand

Choose and coordinate the smallest appropriate product skill for a development
request. Do not reproduce another skill's work inside the router.

## Stay inactive

Stay inactive when the user names a skill, requests read-only analysis, or asks
for non-code work. Let that direct request proceed.

## Route by missing decision

- Choose `brainstorm` when the intended outcome or user experience is still
  ambiguous.
- Choose `plan` when the direction is concrete but the technical execution is
  not yet accepted.
- Choose `design` whenever accepted work includes UI whose production-intended
  components have not been approved.
- Choose `implement` when the implementation facts and any required Design
  deliverables are accepted.

Open only the selected skill for the current transition. Explain the choice in
plain language. A direct skill remains independently usable and never needs to
return through this router.

## Coordinate the lifecycle

The canonical Plan Graph remains owned by `$plan`. Discover it on the current
repository branch when it exists, revalidate its revision and commit before a
transition, and apply only receipts returned by the selected skill. Do not let
workers write graph state.

Resolve the graph helper from the loaded skill at `../../scripts/plan_graph.py`;
`plugins/codex-dev-flow/scripts/plan_graph.py` is only the source-package locator.

`$implement` owns its TDD workers, per-node review/spec correction loops, safe
local lane integration, affected checks, cleanup, and final whole-branch
review/spec gates. There are no separate review, verification, or integration
routes to stitch together.

Stop on stale state, a failed gate, a material user decision, missing authority,
or a protected-branch boundary. Never infer permission to push, create or update
a merge request, approve, merge, enable auto-merge, or enter a merge queue.
