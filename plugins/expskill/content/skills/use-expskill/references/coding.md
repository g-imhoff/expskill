---
name: coding
description: Explain you exactly how to handle an coding task from and vague idea into a completly implemented and tested.
---

## Precondition

- Run `ls .expskill/design/references` and`ls .expskill/test/references`, if one of those folder is not existing you should propose to load respectively `$setup-design` and `$setup-test`, say clearly to the user than without it the expskill workflow will be less effective and reliable.

## Workflow

Follow this workflow sequentially by making one step at a time : 

1. This step can be skipped if the coding task is already well defined. Otherwise load `$brainstorm` skill.
2. (ONLY if `cat ./expskill/design/setup.md` with done == true and the task is related to any type ui/ux work) Load the `$design` skill
3. Load the `$plan` skill

## Prepare routed UI work

When setup is ready and only one of technical planning or production UI approval
is unresolved, select its standalone skill. When both are unresolved, use
the `parallel-plan-design` execution-policy route. The user opens two CLI
conversations and talks to Plan and Design directly. In this route, the
router only prepares the Design worktree before the user starts Design, asks the
user to start both before waiting for either result, and relays messages among
the user, Plan, and Design. Both work from the same exact repository
baseline and saved Concept Brief. The Plan conversation reads the unchanged
target checkout and is the only Plan Graph writer. The Design conversation
receives one isolated worktree created by `{expskill_folder}/scripts/worktrees.py`. The
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

Resolve the graph helper from the loaded skill at `{expskill_folder}/scripts/plan_graph.py`.
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

Keep discovering facts and resolving quality problems autonomously. Exhaust supplied context, repository evidence, prior accepted evidence, and authoritative sources before any user question.

Route every remaining user question through `$grill-me`. Map decisions as a design tree. Work the frontier in rounds with a recommended answer per question. Use a one question frontier round for an isolated decision. Use complete frontier rounds for connected decisions where one answer changes which later questions matter. Wait for user answers before the next round. Act only after the user confirms shared understanding.

Keep the owning skill paused while `$grill-me` resolves the tree. Return the confirmed decision delta and resume the owning skill. The owning skill remains responsible for its state and invalidating any dependent work. Ask the user for explicit consent before opening `$grill-me` from a routed lifecycle, then invoke it without further gate checks.
