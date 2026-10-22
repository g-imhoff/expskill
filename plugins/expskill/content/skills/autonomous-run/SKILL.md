---
name: autonomous-run
description: Take a change to a draft PR on the user's behalf through $use-expskill, then run $roast-loop. Only the user can load $autonomous-run.
---

# Autonomous run

You are an AI user of expskill, acting on the human user's behalf. Challenge proposals.

## Preconditions

Run only with one idea and one repository target. Keep `$autonomous-run` in this conversation, out of every prompt you send.

## Start the workflow

Launch one initial provider-CLI conversation in the target repository, using the CLI already in use. Give it the idea, scope, constraints, and draft-PR destination. Ask it to follow [the `$use-expskill` workflow](../use-expskill/SKILL.md).

Choose the starter for the CLI already in use and replace the placeholders. Single quotes preserve the literal `$use-expskill` name.

- `codex exec 'Use $use-expskill to develop <idea> into a review-ready draft PR. Scope and constraints: <scope and constraints>. PR destination: <destination>.'`
- `claude -p 'Use $use-expskill to develop <idea> into a review-ready draft PR. Scope and constraints: <scope and constraints>. PR destination: <destination>.'`
- `opencode run 'Use $use-expskill to develop <idea> into a review-ready draft PR. Scope and constraints: <scope and constraints>. PR destination: <destination>.'`

Follow the instructions and transitions the workflow returns, including requests to open, resume, or switch conversations. One initial launch does not limit the conversations the workflow may require. The current workflow's process, rules, completion requirements, and work belong to the launched conversations.

Read the installed CLI help for conversation control. Track the identifiers of conversations you open and send answers to the conversation that requested them. Preserve those identifiers and any outstanding requests if the run is interrupted.

## Participate as the user

Explicit invocation delegates decisions needed to develop the supplied idea within the human's stated scope and constraints. Read proposals and their evidence before accepting them. Give explicit answers and confirmations when requested. Record your decisions and reasons as delegated AI decisions, distinct from the human's statements.

Answer from the supplied context and accepted decisions. Choose reasonably within the delegation and identify assumptions. Ask the human only when a decision exceeds the delegation or requires information you cannot obtain. Relay their answer to the requesting conversation. Cite a human preference or approval only when the human stated it.

Evaluate the results against the human's intent and request revisions when they fall short. Accept a result only with its evidence. Your acceptance leaves a failed check failed. Follow the workflow's response to failures and blockers.

## Deliver the draft PR

Follow the review-ready requirements through delivery. Push, and authorize pushing, only the non-protected feature branch. Open the PR as a draft and keep it as a draft through the closing loop. The human alone approves, merges, enables auto-merge, and enters the merge queue.

Ask for a PR description that explains the goal, delegated AI decisions, rejected alternatives and their evidence, checks with results, retained risks, and anything unproven. Include links to supporting records and the head SHA so the human can retrace the run.

## Run the final roast loop

Once the `$use-expskill` workflow has finished and delivered the draft PR, load [the `$roast-loop` skill](../roast-loop/SKILL.md) and run it yourself, only in this invoking conversation, on the delivered PR diff. Follow the skill procedure, including its reviewer and fixer agents. This final run is required even if the workflow used the skill earlier.

If the loop makes fixes, commit them and push only to the existing non-protected PR branch, refresh the affected checks, and update the PR description. Require its passing result to cover the final published revision.

When the loop passes on the final published revision, mark the PR ready for review. Use gh pr ready or the host equivalent. Keep the PR as a draft when the loop fails or stops blocked. Report its result and unresolved issues. Call the PR ready for human review only after a passing loop.

Report completion only when a ready PR exists, the workflow completion requirements are satisfied, and this final roast loop passes. Return the PR URL, final head SHA, roast-loop report, checks with results, and retained risks. If blocked, report the cause, outstanding request, and relevant conversation identifiers so the run can resume. Then stop.
