---
name: use-expskill
description: Use only for code or executable-configuration requests needing lifecycle routing. Stay inactive for explicit skills, read-only work, and non-code requests.
---

# Use ExpSkill

Choose and coordinate the smallest appropriate product skill for a development
request. Do not reproduce another skill's work inside the router. A routed UI
request may use the bounded Plan and Design conversation pair defined below.

## Stay inactive

Stay inactive when the user names a skill, requests read-only analysis, or asks
for non-code work. Let that direct request proceed.

## Route by missing decision

- Choose `correct` when the request identifies a concrete defect, regression,
  failing test, or accepted review finding whose expected behavior is already
  settled. Correct diagnoses whether its bounded repair gate passes.
- Choose `brainstorm` when the intended outcome or user experience is still
  ambiguous.
- Choose `plan` when the direction is concrete but the technical execution is
  not yet accepted.
- Choose `design` whenever accepted work includes UI whose production-intended
  components have not been approved.
- Choose `implement` when the implementation facts and any required Design
  deliverables are accepted.
- Choose `review-loop` when implementation is done and the change needs
  adversarial category review with fixes before a PR. It coordinates reviewers
  and fixers for up to three review cycles, passing when every category reaches
  9 of 10.

Except for the bounded Plan and Design conversation pair, open only the selected
skill for the current transition. Explain the choice in plain language. A
direct skill remains independently usable and never needs to return through
this router.

A selected `$correct` runs in the invoking coordinator without an agent
profile or Plan Graph. If diagnosis reveals a consequential decision, stop the
repair before production edits and let Correct present the decision to the
user. Never turn that stop into an automatic Brainstorm or Plan transition.

## Prepare routed UI work

There is no setup gate. Neither `$setup-design` nor `$setup-test` is
auto-loaded or router-selected merely because a setup record is absent or
invalid, and no inspector script runs before routing. If the user explicitly
asks for design-sketch or test-method setup, select only that setup skill. If
UI work arrives with no setup record and no explicit setup intent, report
"not configured, run `$setup-design` / `$setup-test`" and stop before
Plan, Design, or feature implementation in that transition. A ready
`.expskill/setup-design.md` or `.expskill/setup-test.md` record, when present,
is read by its owning skill only and never acts as a routing precondition.
Do not apply any setup requirement to an eligible `$correct` repair that
restores accepted UI behavior without redesign. Direct `$setup-design` and
`$setup-test` remain independently usable.

When setup is ready and only one of technical planning or production UI
approval is unresolved, select its normal standalone skill. When both are
unresolved, use the `parallel-plan-design` execution-policy route. The user talks
to Plan and Design directly, in two CLI conversations the user opens. The
router never opens them. It never spawns subagents or background runs from
inside this conversation. Ask the user to start both before waiting for either
result. Both work from the same exact repository baseline and saved Concept
Brief. The Plan conversation reads the unchanged target checkout and is the
only Plan Graph writer. The Design conversation receives one isolated worktree
created by `../../scripts/worktrees.py`. The worktree carries no copied setup
support: Design works from the project's own tracked files and may consult a
ready `.expskill/setup-design.md` record when one exists, but that record is
never a prerequisite. Keep new specimens and temporary evidence inside that
worktree. Initialize the Design helper with `invocation_mode` set to `routed`.
Direct Design uses the default `direct` mode, which cannot checkpoint a
candidate.

Hand the user one command per conversation, in the CLI the user already runs.
Each shape below was checked against the stated release. Run the Plan
conversation from the target checkout.

- `codex exec "Run $plan from baseline <sha> using the Concept Brief at <brief path>. Return questions to me instead of asking the user."` Checked on codex-cli 0.153.4.
- `claude -p "Run $plan from baseline <sha> using the Concept Brief at <brief path>. Return questions to me instead of asking the user."` Checked on Claude Code 2.1.197.
- `opencode run "Run $plan from baseline <sha> using the Concept Brief at <brief path>. Return questions to me instead of asking the user."` Checked on opencode 1.18.30.

Run the Design conversation from the isolated worktree, with the
same baseline and Concept Brief.

- `codex exec "Run $design in routed mode from baseline <sha> using the Concept Brief at <brief path>. Return questions to me instead of asking the user."`
- `claude -p "Run $design in routed mode from baseline <sha> using the Concept Brief at <brief path>. Return questions to me instead of asking the user."`
- `opencode run "Run $design in routed mode from baseline <sha> using the Concept Brief at <brief path>. Return questions to me instead of asking the user."`

The router owns user interaction while the two conversations run. Each
conversation returns questions instead of asking the user. Present at most one
current question at a time, apply its answer to the owning conversation, and
then request the next current question. Do not infer approval. Relay questions
only after the user has started both conversations.

Relay Design's clickable Yodea preview link with each visual approval request
and preview progress update. Carry the hosted note and represented candidate
revision or digest through subsequent handoffs and completion summaries. The
coordinator authorized to create or update a PR must include the current preview
link in its description and both the PR and preview links in the final user
response. Preserve the hosted preview through PR review even after the local
Design worktree is cleaned up.

After user approval, Design creates one coherent local candidate commit and
delivers its route-neutral manifest and candidate-bearing Design receipt. Pass
that unchanged receipt and commit identity to the same Plan session. Plan
records the typed Design join only while the isolated branch still points to
that candidate and cannot become ready before validation. A blocked conversation,
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

`$grill-me` is the decision tool for connected user choices, not a lifecycle phase. Let the owning
skill handle a single isolated user decision. Keep discovering facts and
resolving quality problems autonomously.

Use `$grill-me` when every condition below is true:

- Facts are exhausted.
- The owning skill cannot continue because multiple consequential decisions
  remain.
- Those decisions are connected, so one answer changes which later questions
  matter.
- Only the user can decide them.

Explain the blocker briefly, then resolve the tree with `$grill-me`, a frontier-by-round interview with a recommended answer per question that ends in confirmed shared understanding. Keep the owning skill paused while Grill Me resolves the decision tree. When the user confirms
shared understanding, return the confirmed decision delta and resume the owning
skill. The owning skill remains responsible for its state and for invalidating
any dependent work.
