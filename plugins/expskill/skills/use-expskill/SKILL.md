---
name: use-expskill
description: Use only for code or executable-configuration requests needing lifecycle routing. Stay inactive for explicit skills, read-only work, and non-code requests.
---

# Use ExpSkill

Choose and coordinate the smallest appropriate product skill for a development
request. Do not reproduce another skill's work inside the router. A routed UI
request may start the bounded Plan and Design sibling pair defined below.

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

Except for the bounded Plan and Design sibling pair, open only the selected
skill for the current transition. Explain the choice in plain language. A
direct skill remains independently usable and never needs to return through
this router.

## Prepare routed UI work

Before routing production UI work, run the packaged
`../setup-ui-testing/scripts/inspect_setup.py` against the current project root.
Treat its exact `ready` result as reusable setup. If it returns `absent`,
`invalid`, malformed output, or an error, select only `$setup-ui-testing` in
routed mode. Do not start Plan, Design, or feature implementation in that
transition. Direct `$setup-ui-testing` remains independently usable.

When setup is ready and only one of technical planning or production UI
approval is unresolved, select its normal standalone skill. When both are
unresolved, use the `parallel-plan-design` execution-policy route. Launch one
fresh `expskill-planner` and one fresh `expskill-designer` before waiting for
either result. Both receive the same exact repository baseline and accepted
outcome. Planner reads the unchanged target checkout and is the only Plan Graph
writer. Designer receives one isolated worktree created by
`../../scripts/worktrees.py`, then populated by its `seed-ui-harness` operation.
Never copy `.ui-harness/evidence` from the target checkout.
Initialize the Design helper with `invocation_mode` set to `routed`. Direct
Design uses the default `direct` mode, which cannot checkpoint a candidate.

The router owns user interaction while the siblings run. Siblings return
questions instead of asking the user. Present at most one current question at a
time, apply its answer to the owning session, and then request the next current
question. Do not infer approval. Start both sessions before serializing their
questions.

After user approval, Design creates one coherent local candidate commit and
delivers its route-neutral manifest and candidate-bearing Design receipt. Send
that unchanged receipt and commit identity to the same Plan session. Plan
records the typed Design join only while the isolated branch still points to
that candidate and cannot become ready before validation. A blocked session,
stale baseline, moved branch, malformed receipt, invalid setup, or failed
Design gate stops the join. Keep the isolated worktree for `$implement`, which
alone may later integrate and clean it. Once the exact candidate is an ancestor
of the target HEAD, the Plan Graph validates that integrated ancestry and no
longer requires the temporary Design branch to exist. The router never edits
tracked source, commits, integrates, pushes, or merges.

## Coordinate the lifecycle

The canonical Plan Graph remains owned by `$plan`. Discover it on the current
repository branch when it exists, revalidate its revision and commit before a
transition, and apply only receipts returned by the selected skill. Do not let
workers write graph state.

Resolve the graph helper from the loaded skill at `../../scripts/plan_graph.py`.
`plugins/expskill/scripts/plan_graph.py` is only the source-package locator.

`$implement` owns its TDD workers, per-node review/spec correction loops, safe
local lane integration, affected checks, cleanup, and final whole-branch
review/spec gates. There are no separate review, verification, or integration
routes to stitch together.

Stop on stale state, a failed gate, a material user decision, missing authority,
or a protected-branch boundary. Never infer permission to push, create or update
a merge request, approve, merge, enable auto-merge, or enter a merge queue.

## Resolve a decision frontier

`$grill-me` is an optional decision tool, not a lifecycle phase. Let the owning
skill handle a single isolated user decision. Keep discovering facts and
resolving quality problems autonomously.

Offer `$grill-me` only when every condition below is true:

- Facts are exhausted.
- The owning skill cannot continue because multiple consequential decisions
  remain.
- Those decisions are connected, so one answer changes which later questions
  matter.
- Only the user can decide them.

Explain the blocker briefly and offer `$grill-me`. Never automatically invoke
`$grill-me`. Wait for explicit consent. If the user accepts, keep the owning
skill paused while Grill Me resolves the decision tree. When the user confirms
shared understanding, return the confirmed decision delta and resume the owning
skill. The owning skill remains responsible for its state and for invalidating
any dependent work.
