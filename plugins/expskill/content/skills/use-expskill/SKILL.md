---
name: use-expskill
description: Use only for code or executable-configuration requests needing lifecycle routing. Stay inactive for other explicit skills, read-only work, and non-code requests.
---

# Use ExpSkill

Choose and coordinate the smallest appropriate product skill for a development
request without reproducing another skill's work inside the router. A routed UI
request may use the bounded Plan and Design conversation pair defined below.

## Stay inactive

Stay inactive when the user names another skill, requests read-only analysis, or asks
for non-code work. Let that direct request proceed. Explicit `$use-expskill` invocation authorizes deliberate selection within the requested lifecycle. Explain each selection. Product skills keep implicit activation disabled.

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
skill for the current transition and explain the choice in plain language. A
direct skill remains independently usable and never needs to return through
this router.

A selected `$correct` runs in the invoking coordinator without an agent
profile or Plan Graph. If diagnosis reveals a consequential decision, stop the
repair before production edits and let Correct present the decision to the
user. Preserve known intent. Correct may hand a user-selected or already
authorized structural repair to Plan or Implement under its own boundary.
Never force Brainstorm for a technical solution whose conceptual direction is
already settled, and never infer new authority from the handoff.

## Prepare routed UI work

There is no setup gate. Neither `$setup-design` nor `$setup-test` is
auto-loaded or router-selected merely because a setup record is absent or
invalid, and no inspector script runs before routing. If the user explicitly
asks for design-sketch or test-method setup, select only that setup skill. If
UI work arrives with no setup record and no explicit setup intent, continue with
the selected phase using discoverable project-native capabilities. Report a
missing capability only when that phase actually needs it. A ready
`.expskill/setup-design.md` or `.expskill/setup-test.md` record, when present,
is read by its owning skill only and never acts as a routing precondition.
Do not apply any setup requirement to an eligible `$correct` repair that
restores accepted UI behavior without redesign. Direct `$setup-design` and
`$setup-test` remain independently usable.

When only one of technical planning or production UI
approval is unresolved, select its normal standalone skill. When both are
unresolved, use the `parallel-plan-design` execution-policy route. The user talks
to Plan and Design directly in two CLI conversations the user opens. The
router never opens them and never spawns subagents or background runs here.
Ask the user to start both before waiting for either result. Both work from the
same exact repository baseline and complete accepted input. Use a saved Concept Brief, an existing accepted specification, or the already-settled direct request with all accepted criteria, constraints, negative behavior, and non-goals. Do not invoke Brainstorm to materialize settled intent. Before launch, bind an existing file by exact path and digest, or copy the complete accepted direct input into one private read-only `accepted-input.json` outside the repository. Record its byte digest and the original user decision reference. This is a faithful input snapshot, never a new proposal, summary that drops criteria, or repository planning document. Both conversations receive the same immutable input locator and digest. The Plan conversation
reads the unchanged target checkout and is the
only Plan Graph writer. The Design conversation receives one isolated worktree
created by `../../scripts/worktrees.py`. The worktree carries no copied setup
support: Design works from the project's own tracked files and may consult a
ready `.expskill/setup-design.md` record when one exists, but that record is
never a prerequisite. Keep new specimens and temporary evidence inside that
worktree. Initialize the Design helper with `invocation_mode` set to `routed`.
Direct Design uses the default `direct` mode, which cannot checkpoint a
candidate.

Hand the user one launch command per conversation in the CLI already in use. Read installed launch and resume help before rendering commands. The shapes below were checked on codex-cli 0.160.0, Claude Code 2.1.252, and OpenCode 2.0.21. Render shell-safe exact paths and literal skill names. Never execute placeholders or use a most-recent-session shortcut when two owners exist. Run Plan from the target checkout and Design from its isolated worktree.

Use a private directory outside the repository for CLI output and a single coordinator-owned `conversation-relay.json`. Record the run ID, each phase owner, CLI and version, working directory, branch, baseline, input locator/digest, actual session identifier, last output locator/digest, pending question ID, and current continuation status. CLI output is evidence. The registry does not replace either phase's canonical state.

- Codex launch: `codex exec --json -o '<private>/plan.reply' 'Run $plan from baseline <sha> using accepted input <path> with digest <digest>. Return questions to the router with stable IDs.' > '<private>/plan.events.jsonl'`. Capture the explicit returned session identifier from the JSON events. Use the analogous command with `$design in routed mode` from its worktree, with separate Design output paths.
- Claude launch: `claude -p --session-id '<new-plan-uuid>' --output-format json 'Run $plan from baseline <sha> using accepted input <path> with digest <digest>. Return questions to the router with stable IDs.' > '<private>/plan.result.json'`. Record that UUID and verify the returned session identifier. Use a different UUID and output path for `$design in routed mode`.
- OpenCode launch: `opencode run --format json --title '<unique-run-plan-title>' 'Run $plan from baseline <sha> using accepted input <path> with digest <digest>. Return questions to the router with stable IDs.' > '<private>/plan.events.jsonl'`. Capture the explicit session identifier from output. If absent, use `opencode session list --format json` in the same directory and require one unambiguous matching new title. Use a different title and output path for `$design in routed mode`.

Ask the user to start both before waiting for either result. The user returns each output or its accessible private locator. The router reads it and binds every pending question to its phase and actual session identifier. Each phase returns current workflow ID/revision, accepted input digest, status, stable question IDs and exact questions, decisions needed, and any delivery receipt. Return `awaiting-answer` when a material question remains, with enough current state to continue. A successful process exit never means the phase is complete.

The router owns user interaction while the two conversations run. Present at most one current question at a time. Preserve the user's exact answer and decision reference, then give one resume command for its owning session, from the recorded working directory. The answer names the run ID, phase, input digest, question ID, and exact answer. Never send it to both sessions or relaunch the initial prompt as a substitute for continuation.

- Codex resume: `codex exec resume --json -o '<private>/plan-next.reply' '<plan-session-id>' '<bound answer or Design receipt>' > '<private>/plan-next.events.jsonl'`.
- Claude resume: `claude -p --resume '<plan-session-id>' --output-format json '<bound answer or Design receipt>' > '<private>/plan-next.result.json'`.
- OpenCode resume: `opencode run --session '<plan-session-id>' --format json '<bound answer or Design receipt>' > '<private>/plan-next.events.jsonl'`.

Use the Design owner and its paths for Design answers. Verify the resumed session identifier and returned question resolution before clearing the pending question. Repeat only for the next material question. Do not infer approval. Once Design is approved and delivered, send its unchanged receipt, candidate identity, and manifest locator/digest through this same resume protocol to the same Plan session. Preserve complete answers and accepted decisions across both owners without re-asking.

On interruption, preserve registry, output, canonical workflow receipts, budget accounting, and pending questions. Revalidate input bytes, workspace identity, baseline and candidate pins before resuming. If an identifier is missing, a session cannot resume, or the CLI would create a new session for an absent ID, stop that continuation. Recover only by handing a new explicitly identified owner the same immutable input, complete accepted decisions, outstanding questions and answers, and current canonical receipts. Record the replaced session and transfer ownership once. Recovery never implies approval, resets a budget, or permits two writers of one canonical phase state. If the retained basis is incomplete or workspace pins changed, reconcile the exact gap before dependent work.

Relay Design's hosted preview link or local review entry and hosting limitation
with each visual approval request and progress update. Carry its review note
and represented candidate revision or digest through handoffs and summaries.
The coordinator authorized to create or update a PR includes that current review
entry in its description and final response. Retain a hosted preview through PR
review. For local mode, preserve or transfer the exact specimen and launch recipe
before cleaning the Design worktree.

After user approval, Design creates one coherent local candidate commit with
its route-neutral manifest and candidate-bearing Design receipt. Pass that
unchanged receipt and commit identity to the same Plan session. Plan records
the typed Design join only while the isolated branch still points to that
candidate and cannot become ready before validation. A blocked conversation,
stale baseline, moved branch, malformed receipt, or failed
Design gate stops the join. Keep the isolated worktree for `$implement`, which
alone may later integrate and clean it. Once the exact candidate is an ancestor
of the target HEAD, the Plan Graph validates that integrated ancestry and the
temporary Design branch may go. The router never edits tracked source, commits,
integrates, pushes, or merges.

## Coordinate the lifecycle

The canonical Plan Graph remains owned by `$plan`. Discover it on the current
repository branch when it exists, revalidate its revision and commit before a
transition, and apply only receipts returned by the selected skill. Do not let
workers write graph state.

Resolve the graph helper from the loaded skill at `../../scripts/plan_graph.py`.
`plugins/expskill/content/scripts/plan_graph.py` is only the source-package locator.

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

Explain the blocker briefly, then offer `$grill-me` for the decision frontier and proceed only with
explicit consent. Never automatically invoke `$grill-me`. Resolve the tree with `$grill-me`, a frontier-by-round interview that follows its preference-first policy. Elicit user experience and priorities before recommending an answer, unless the user asks for AI-led guidance. It ends in confirmed shared understanding. Keep the owning skill paused while Grill Me resolves the decision tree. When the user confirms
shared understanding, return the confirmed decision delta and resume the owning
skill. The owning skill remains responsible for its state and for invalidating
any dependent work.
