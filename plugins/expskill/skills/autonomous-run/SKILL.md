---
name: autonomous-run
description: Drive one idea from concept to a review-ready draft PR through each phase gate.
---

# Autonomous run

Take one idea and carry it through the complete expskill chain to a review-ready draft PR. You act as the coordinator. Fresh provider-CLI conversations do the phase work. The existing skills keep their gates. You never bypass them.

## When this skill runs

It runs only on explicit `$autonomous-run` invocation with one idea and one repo target. Stay inactive for anything else. A request that names another phase skill belongs to that skill. A concrete defect with settled expectations is not an idea. Say so and stop instead of running the whole chain. `$use-expskill` never selects this skill, and this skill never calls `$use-expskill`. That keeps routing from looping back on itself.

## Launch phases in fresh conversations

Never run phase work as in-session subagents. Open each phase as a fresh provider-CLI conversation from the exact baseline, in the CLI already in use.

- `codex exec "Run $brainstorm on <idea>. Return the Concept Brief path and open questions to me."` Checked on codex-cli 0.153.4.
- `claude -p "Run $plan from baseline <sha> using the Concept Brief at <brief path>. Return questions to me instead of asking the user."` Checked on Claude Code 2.1.197.
- `opencode run "Run $implement for graph revision <rev> at <commit>. Return the candidate commit and gate results to me."` Checked on opencode 1.18.30.

Each conversation returns questions instead of asking the user. You own user interaction while phases run. Present at most one current question at a time, apply its answer to the owning conversation, then ask for the next one. Never infer approval.

## Walk every phase in order

1. Brainstorm the idea to a confirmed Concept Brief saved at a known path. The brief settles the concept. Later phases read it and never re-ask what it already answers.
2. Create the non-protected feature branch from the pinned baseline before planning, so every phase shares one base. Plan from the brief on the exact target checkout and baseline. The Plan conversation owns the Plan Graph. You are never the graph writer. For UI work, open the Plan and Design conversation pair from the same baseline on the `parallel-plan-design` route. Plan records the approved Design receipt through the typed join before it can become ready.
3. Implement the ready graph on a non-protected feature branch. Implement owns its TDD workers, per-node review and spec gates, correction loops, local integration, and whole-branch gates. You apply only receipts the phase returns. Workers never write graph state.
4. Test the integrated head through realistic paths and bind the evidence to the exact commit. A failure stops the run. Test never repairs production code, so route a defect back through the owning phase instead of patching around it.
5. Review the branch diff as the last gate before delivery. Review reports findings without fixes. Route actionable findings to the owning phase and rerun the affected gates.

When several connected decisions block a phase and only the user can settle them, offer `$grill-me` and wait for explicit consent. Never launch it on your own. Keep the owning phase paused until the user confirms shared understanding, then resume with the confirmed delta.

## Respect every gate

Carry each phase gate through unchanged. That means a confirmed brief before planning, confirmed projections before implementation, approved UI before integration, red-green evidence or a justified alternative per node, independent review and spec verdicts per candidate, and whole-branch gates on the integrated head. A stale baseline, a failed gate, or missing authority stops the run at the current boundary with the exact cause. A correction that survives three non-improving attempts on the same cause stops that node as blocked instead of looping.

## Stop at human review

The run ends at `ready-for-human-review` with a draft PR. Push only the non-protected feature branch. Open the PR as a draft against the protected destination with checks bound to its head. Never push the protected branch. Never approve, merge, enable auto-merge, or enter a merge queue. The user reviews and merges.

## Write a PR description that explains the choices

Draw each part from its phase record. Name the idea and who it affects, plus the rejected directions and why they lost, from the Concept Brief. Name the technical shape, the rejected alternatives and their evidence, from the Plan Graph and the Design receipt. Name the commits, the checks run with results, and the review and spec verdicts from the implementation and test evidence. Close with retained risks and anything still unproven. Link the brief paths and the head SHA so the reviewer can retrace the run.

## Report the finish

Report only when the draft PR exists and every gate passed on its head. Name the Concept Brief path, the Plan Graph revision, the Design receipt where one exists, the branch and head SHA, the checks with results, the gate verdicts, retained risks, and the PR URL. Then stop. Do not start another phase.
