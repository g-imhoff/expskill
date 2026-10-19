---
name: use-expskill
description: Route a request to change code or executable configuration to the right ExpSkill skill. A request that names a skill goes straight to that skill.
---

# Use ExpSkill

The router only chooses and coordinates the smallest appropriate product skill,
and the selected skill does the work.

## Stay inactive

Stay inactive when the user names a skill, requests read-only analysis, or asks
for non-code work.

## Route by missing decision

- Choose `correct` when the request identifies a concrete defect, regression,
  failing test, or accepted review finding whose expected behavior is
  settled. Correct diagnoses whether its bounded repair gate passes.
- Choose `brainstorm` when the intended outcome or user experience is
  ambiguous.
- Choose `plan` when the direction is concrete but the technical execution is
  not accepted.
- Choose `design` whenever accepted work includes UI whose production-intended
  components lack approval.
- Choose `implement` when the implementation facts and any required Design
  deliverables are accepted.
- Choose `roast` when implementation is done and the change needs
  one adversarial category review pass before a PR. It returns
  scores with top issues and stops without fixes.
- Choose `roast-loop` when implementation is done and the change needs
  adversarial category review with fixes before a PR. It coordinates
  `$roast` reviewers and fixers for up to three review cycles, passing
  when every category reaches 9 of 10.

Except for the bounded Plan and Design conversation pair below, open only the
selected skill for the current transition and explain the choice in plain
language. A direct skill stays independent of this router throughout.

A selected `$correct` runs alone in the invoking coordinator, outside any agent
profile or Plan Graph, from diagnosis through check. If diagnosis reveals a
consequential decision, stop the repair before production edits and let Correct
present the decision to the user. After that stop, only the user's choice starts
Brainstorm or Plan.

## Prepare routed UI work

There is no setup gate. A missing or invalid setup record alone leaves
`$setup-design` and `$setup-test` unloaded and unselected, and routing precedes
any inspector script. If the user explicitly asks for design-sketch or
test-method setup, select only that setup skill. If UI work arrives with no
setup record and no explicit setup intent, report "not configured, run
`$setup-design` / `$setup-test`" and stop before Plan, Design, or feature
implementation in that transition. A ready `.expskill/setup-design.md` or
`.expskill/setup-test.md` record is read only by its owning
skill, and routing ignores its contents. Eligible `$correct` repairs that
restore accepted UI behavior without redesign skip every setup requirement.

When setup is ready and only one of technical planning or production UI approval
is unresolved, select its standalone skill. When both are unresolved, use
the `parallel-plan-design` execution-policy route. The user opens two CLI
conversations and talks to Plan and Design directly. In this route, the
router only prepares the Design worktree before the user starts Design, asks the
user to start both before waiting for either result, and relays messages among
the user, Plan, and Design. Both work from the same exact repository
baseline and saved Concept Brief. The Plan conversation reads the unchanged
target checkout and is the only Plan Graph writer. The Design conversation
receives one isolated worktree created by `../../scripts/worktrees.py`. The
worktree starts as the baseline's tracked files. Design works from them and may
consult a ready `.expskill/setup-design.md` record when one exists. Keep new
specimens and temporary evidence in that worktree. Initialize the Design helper with `invocation_mode`
set to `routed`. Direct Design uses the default `direct` mode, which cannot
checkpoint a candidate.

Hand the user one command per conversation, in the user's current CLI.
Each shape below was checked against the stated release. Run the Plan
conversation from the target checkout.

- `codex exec "Run $plan from baseline <sha> using the Concept Brief at <brief path>. Return questions to me instead of asking the user."` Checked on codex-cli 0.153.4.
- `claude -p "Run $plan from baseline <sha> using the Concept Brief at <brief path>. Return questions to me instead of asking the user."` Checked on Claude Code 2.1.197.
- `opencode run "Run $plan from baseline <sha> using the Concept Brief at <brief path>. Return questions to me instead of asking the user."` Checked on opencode 1.18.30.

Run the Design conversation from the isolated worktree.

- `codex exec "Run $design in routed mode from baseline <sha> using the Concept Brief at <brief path>. Return questions to me instead of asking the user."`
- `claude -p "Run $design in routed mode from baseline <sha> using the Concept Brief at <brief path>. Return questions to me instead of asking the user."`
- `opencode run "Run $design in routed mode from baseline <sha> using the Concept Brief at <brief path>. Return questions to me instead of asking the user."`

The router owns user interaction while both conversations run, and each
returns questions to the router. Present one current question at a time, apply
its answer to its owner, then request the next current one. Only explicit user
approval is approval. Relay questions only after the user starts both
conversations.

Relay Design's clickable Yodea preview link with each visual approval request
and progress update, and carry the hosted note plus represented candidate
revision or digest through handoffs and summaries. The coordinator authorized
to create or update a PR includes the current preview link in its description
and both links in the final user response. The hosted preview survives PR
review after the local Design worktree is removed.

After user approval, Design creates one coherent local candidate commit with its
route-neutral manifest and candidate-bearing Design receipt. Pass that unchanged
receipt and commit identity to the same Plan session. Plan records the typed
Design join only while the isolated branch points to that candidate and
cannot become ready before validation. A blocked conversation, stale baseline,
moved branch, malformed receipt, invalid setup, or failed Design gate stops the
join. Keep the isolated worktree for `$implement`, which alone may
integrate and clean it. Once the exact candidate is an ancestor of the target
HEAD, the Plan Graph validates that integrated ancestry and the temporary Design
branch may go. The router's only repository write is preparing the Design
worktree.

## Coordinate the lifecycle

The canonical Plan Graph remains owned by `$plan`. Discover it on the current
repository branch when it exists, revalidate its revision and commit before a
transition, and apply only the selected skill's receipts, which alone reach the
graph.

Resolve the graph helper from the loaded skill at `../../scripts/plan_graph.py`.
`plugins/expskill/content/scripts/plan_graph.py` is only the source-package locator.

`$implement` owns its TDD workers, per-node review/spec correction loops, safe
local lane integration, affected checks, cleanup, and final whole-branch
review/spec gates. There are no separate review, verification, or integration
routes to stitch together.

Stop on stale state, a failed gate, a material user decision, missing authority,
or a protected-branch boundary. Only an explicit user grant permits pushing,
creating or updating a merge request, approving, merging, enabling auto-merge,
or entering a merge queue.

## Resolve a decision frontier

Let the owning skill handle one isolated user decision. Keep discovering
facts and resolving quality problems autonomously.

Use `$grill-me` only when every condition below is true:

- Facts are exhausted.
- The owning skill cannot continue because multiple consequential decisions
  remain.
- Those decisions are connected, so one answer changes which later questions
  matter.
- Only the user can decide them.

Explain the blocker briefly, then offer `$grill-me` for the decision frontier,
and invoke it only with explicit consent. Keep the owning skill paused while `$grill-me`, a
frontier-by-round interview with a recommended answer per question, resolves
the decision tree. When the user confirms shared understanding, return
the confirmed decision delta and resume the owning skill. The owning skill
remains responsible for its state and invalidating any dependent work.
