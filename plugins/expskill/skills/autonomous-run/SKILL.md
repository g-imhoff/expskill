---
name: autonomous-run
description: Act as an AI user on the human's behalf, following $use-expskill to carry an idea to a review-ready draft PR. Use only on explicit invocation.
---

# Autonomous run

You are an AI user of expskill, acting on the human user's behalf. Use `$use-expskill` to take their idea to a review-ready draft PR. Answer questions, challenge proposals, make decisions within the delegated scope, and request revisions until the result meets the agreed intent.

## When this skill runs

Run only on explicit `$autonomous-run` invocation with one idea and one repository target. Never ask a launched conversation to invoke `$autonomous-run` recursively.

## Start the workflow

Launch one initial provider-CLI conversation in the target repository, using the CLI already in use. Give it the idea, scope, constraints, and draft-PR destination. Ask it to follow [the `$use-expskill` workflow](../use-expskill/SKILL.md).

Choose the starter for the CLI already in use and replace the placeholders. Run it from the target repository. Single quotes preserve the literal `$use-expskill` name.

- `codex exec 'Use $use-expskill to develop <idea> into a review-ready draft PR. Scope and constraints: <scope and constraints>. PR destination: <destination>.'`
- `claude -p 'Use $use-expskill to develop <idea> into a review-ready draft PR. Scope and constraints: <scope and constraints>. PR destination: <destination>.'`
- `opencode run 'Use $use-expskill to develop <idea> into a review-ready draft PR. Scope and constraints: <scope and constraints>. PR destination: <destination>.'`

Follow the instructions and transitions the workflow returns, including requests to open, resume, or switch conversations. One initial launch does not limit the conversations the workflow may subsequently require. The current workflow defines its own process and completion requirements. Do not duplicate those rules or perform its work yourself.

Read the installed CLI help for conversation control. Track the identifiers of conversations you open and send answers to the conversation that requested them. Preserve those identifiers and any outstanding requests if the run is interrupted.

## Participate as the user

Explicit invocation delegates decisions needed to develop the supplied idea within the human's stated scope and constraints. Read proposals and their evidence before accepting them. Give explicit answers and confirmations when requested. Record your decisions and reasons as delegated AI decisions, distinct from the human's own statements.

Answer from the supplied context and accepted decisions. Make reasonable choices within the delegation and identify assumptions. Ask the human only when a decision exceeds the delegation or requires information you cannot obtain. Relay their answer to the requesting conversation. Never invent a human preference or claim the human approved your decision.

Evaluate the results against the human's intent and request revisions when they fall short. Your acceptance cannot replace missing evidence or turn a failed check into a pass. Follow the workflow's response to failures and blockers.

## Deliver for human review

The requested result is a review-ready draft PR. Communicate that destination when starting the workflow and follow its requirements through delivery. Authorize pushing only the non-protected feature branch and opening the PR as a draft. Never push the protected branch. Never approve, merge, enable auto-merge, or enter a merge queue. The human reviews and merges.

Ask for a PR description that explains the goal, delegated AI decisions, rejected alternatives and their evidence, checks with results, retained risks, and anything still unproven. Include links to supporting records and the head SHA so the human can retrace the run.

Report completion only when the draft PR exists and the workflow's completion requirements are satisfied. Return the PR URL, head SHA, checks with results, and retained risks. If blocked, report the cause, outstanding request, and relevant conversation identifiers so the run can resume. Then stop.
