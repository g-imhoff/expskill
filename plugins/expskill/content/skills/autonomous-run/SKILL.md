---
name: autonomous-run
description: Act as an AI user on the human's behalf, following $use-expskill to a draft PR, then running $review-loop directly before human review. Use only on explicit invocation.
---

# Autonomous run

You are an AI user of expskill, acting on the human user's behalf. Use `$use-expskill` to take their idea to a review-ready draft PR. Answer questions, challenge proposals, make decisions within the delegated scope, and request revisions until the result meets the agreed intent.

## When this skill runs

Run only on explicit `$autonomous-run` invocation with one idea and one repository target. Never ask a launched conversation to invoke `$autonomous-run` recursively.

## Start the workflow

Before the first launch, create one private cumulative budget ledger with a run ID, limit source, start time, and explicit limits. Use the human's tighter stated limits when supplied, otherwise default to 80 AI dispatches, six retries, four hours of wall-clock elapsed time from the first launch, at most six in flight, and depth one. Depth one applies to delegated worker and reviewer lanes within their owning phase. A coordinator conversation never authorizes worker subdelegation. Record the chosen limits in the initial handoff. Use one research sublimit across phases: six researcher turns or named direct investigations, twenty minutes of active research, and at most three in flight, all within the lifecycle dispatch allowance. Inherit tighter explicit research limits when present. Read the installed execution policy and host limits too. For each controlled dispatch, the effective allowance is the minimum of the lifecycle remainder, delegated child allocation, and applicable route or host limits. The two-conversation parallel route and the 30-call implementation route are additional ceilings, never new pools of lifecycle capacity. Their elapsed clocks remain independently binding. Reserve the final required reviews before launch, including implementation's final two judges. A tighter deadline or host capacity wins. A bounded authorized extension may raise a coordinator limit while preserving all spent totals, but never overrides a host ceiling.

Launch one initial provider-CLI conversation in the target repository, using the CLI already in use. Give it the idea, scope, constraints, and draft-PR destination. State that you own authorized remote delivery after the router reaches its local boundary. The router never pushes or creates the PR. Ask it to follow [the `$use-expskill` workflow](../use-expskill/SKILL.md).

Choose the starter for the CLI already in use and replace the placeholders. Single quotes preserve the literal `$use-expskill` name.

- `codex exec 'Use $use-expskill to develop <idea> into a review-ready draft PR. Scope and constraints: <scope and constraints>. PR destination: <destination>.'`
- `claude -p 'Use $use-expskill to develop <idea> into a review-ready draft PR. Scope and constraints: <scope and constraints>. PR destination: <destination>.'`
- `opencode run 'Use $use-expskill to develop <idea> into a review-ready draft PR. Scope and constraints: <scope and constraints>. PR destination: <destination>.'`

Follow the instructions and transitions the workflow returns, including requests to open, resume, or switch conversations. Use one cumulative run identifier and remaining call, retry, elapsed-time, and in-flight allowance for all conversations and phases. Record each actual launch and delegated call once, including corrections, and reconcile child totals without double-counting. Resuming or opening another conversation never resets this allowance. Before implementation, require its `3N + 2` call preflight and reserve its final two judges. Stop before another launch when the required work cannot fit the remaining allowance. The current workflow defines its own process and completion requirements. Do not duplicate those rules or perform its work yourself.

A dispatch means each actual provider CLI launch or resume, worker, researcher, or judge turn. A corrective prompt that starts another AI turn and a retry each consume a dispatch. Give every launch a unique dispatch ID and a child allocation from the existing remainder. Reserve that allocation before launching. Count the parent launch once, then add only new descendant dispatch IDs from the child. Do not grant the same reserved capacity to overlapping children. Retain outstanding reservations until their terminal accounting is reconciled. If an interrupted child has uncertain spend, preserve its reserved capacity as outstanding and stop dependent launches rather than assume it was unused.

Every child return, including successful, blocked, interrupted, or exhausted work, carries a budget handoff: run ID, own dispatch ID, inherited limits and source, unique completed and outstanding descendant dispatch IDs, spent calls and retries, elapsed time, in-flight count, reserved final calls, and remaining allowance. The coordinator verifies IDs and totals against its ledger and any available host records. Return unused allocation only after reconciliation. Missing or contradictory accounting blocks another launch until resolved. Preserve the ledger and child receipts across turns and replacement conversations. Generic-provider totals remain coordinator attestations, do not describe them as mechanically enforced. OpenCode's persisted route counters enforce only dispatches observed by that hook, alongside this ledger. At exhaustion preserve local state, unfinished scope, and accounting, then stop without claiming completion.

Read the installed CLI help for conversation control. Track the identifiers of conversations you open and send answers to the conversation that requested them. Preserve those identifiers and any outstanding requests if the run is interrupted.

## Participate as the user

Explicit invocation delegates decisions needed to develop the supplied idea within the human's stated scope and constraints. Read proposals and their evidence before accepting them. Give explicit answers and confirmations when requested. Record your decisions and reasons as delegated AI decisions, distinct from the human's own statements.

Answer from the supplied context and accepted decisions. Make reasonable choices within the delegation and identify assumptions. Ask the human only when a decision exceeds the delegation or requires information you cannot obtain. Relay their answer to the requesting conversation. Never invent a human preference or claim the human approved your decision.

Evaluate the results against the human's intent and request revisions when they fall short. Your acceptance cannot replace missing evidence or turn a failed check into a pass. Follow the workflow's response to failures and blockers.

## Deliver the draft PR

The requested result is a review-ready draft PR. You are the delivery actor in this invoking conversation. Once the router returns accepted local implementation, verify the exact non-protected branch, head, target remote and base, authenticated host integration, local completion evidence, remaining review budget, and delegated delivery authority. Then push that feature branch and create the draft PR through the authenticated host CLI or API. Verify the returned URL, head, base, and draft state. Do not send delivery back to a router that forbids it. A missing remote or authentication stops delivery with the local commit preserved and no claimed PR. Authorize pushing only the non-protected feature branch and opening the PR as a draft. Never push the protected branch. Never approve, merge, enable auto-merge, or enter a merge queue. The human reviews and merges.

Ask for a PR description that explains the goal, delegated AI decisions, rejected alternatives and their evidence, checks with results, retained risks, and anything still unproven. Include links to supporting records and the head SHA so the human can retrace the run.

## Run the final review loop

Once the `$use-expskill` workflow has finished and delivered the draft PR, load [the `$review-loop` skill](../review-loop/SKILL.md) and run it yourself in this invoking conversation on the delivered PR diff. Do not launch a provider-CLI conversation or send this final step back to a workflow conversation. Follow the skill's procedure, including its reviewer and fixer agents. A valid earlier loop may be reused only when its complete scope, base and candidate pins, accepted criteria, evidence, and independent reviewer provenance are unchanged. Otherwise run a new loop. Reuse retains the original evidence, never a coordinator-reassigned score.

If the loop makes fixes, commit and push them to the existing PR branch, refresh the affected checks, and update the PR description. Require its passing result to cover the final published revision. If it fails or stops blocked, report its result and unresolved issues without declaring the PR ready for human review.

Report completion only when the draft PR exists, the workflow's completion requirements are satisfied, and this final review loop passes. Return the PR URL, final head SHA, review-loop report, checks with results, retained risks, and reconciled budget handoff. If blocked, report the cause, outstanding request, and relevant conversation identifiers and the same budget handoff, including outstanding reservations, so the run can resume. Then stop.
