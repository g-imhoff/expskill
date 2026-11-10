---
name: skill-builder
description: Create, improve, modify, or evaluate an agent skill. Only the user can load $skill-builder.
---

Read [artifact contracts](references/artifact-contracts.md) completely at run start and again before resuming persisted work. Read [evaluation rubric](references/evaluation-rubric.md) completely before freezing the evaluation pack and before every review or scoring pass.

## Modes

| Mode | Use when | File |
|---|---|---|
| create | The exact target is absent. | [create](references/create.md) |
| improve | The target exists, keeps its contract, and needs higher quality or fixed failures, with everything it does today still working. | [improve](references/improve.md) |
| modify | The target exists and the user changes its contract, with the unchanged parts still working. | [modify](references/modify.md) |
| evaluate | The target exists and the user wants scores only. | [evaluate](references/evaluate.md) |

Read the selected mode file once stage 1 records the mode, read [shared stages](references/shared-stages.md) before the first shared stage, and read both again before resuming persisted work.

## Durable run state

Resolve the package's `scripts/run_state.py` relative to the loaded skill and make every run-state change through the interface its runtime help shows. Root retained state per `../../rules/evidence-report.md`. Pin the finalized revision and baselines per `../../rules/pin-scope.md`. Treat missing, stale, inconsistent, or unauditable state, or a rejected transition, as a failed gate at the current stage. Open the artifact contracts' binding, invalidation, recovery, and tombstone sections whenever a transition fails.

## Ordered workflow

Run stage 1, the mode file's stage 2, then the shared stages it names, in order, applying its differences. Each stage ends at its gate. A failed gate stops the run and returns it through the helper to the earliest affected stage.

### 1. Resolve the host, target, mode, and authority

Ground the host and exact target from supplied context, registries, skill roots, invocation names, metadata, and repository facts before asking anything. Scan exact names, aliases, invocation tokens, and overlapping responsibility. Treat a near neighbour as evidence to compare, and build only the exact requested target.

Take the mode the user's wording names and check it against the table. When they disagree, the wording names no mode in the table, or facts leave the target ambiguous, ask one question and wait.

Create writes only to an absent target. Rename, replace, or absorb a near neighbour only on the user's explicit decision. Keep exactly one active target, and finish, explicitly abandon, or validly pause it before activating the next. A multi-target request keeps every target's gates in force. Write the resolution record.

## Target skill text

Write each contract field where the artifact contracts' placement table puts it. Write the description as one sentence saying what the skill does in the words a user would ask with, then the load sentence when one exists. Start the body with its first action. Keep SKILL.md under each target host's load size, and move the rest to linked references.

## Roles, tools, and permissions

- The main agent owns identity resolution, user collaboration, evidence synthesis, hidden-case custody, state transitions, authority checks, scope changes, external delivery, and cleanup.
- Research agents use web access read-only and return only evidence cards. Fan out narrow discovery per `../../rules/bounded-fanout.md`.
- Exactly one implementer writes, and only inside the isolated candidate. The implementer works in its own session and the main agent never writes candidate bytes. Trial agents write only inside disposable targets. Give one owner per lane and confine trial writes per `../../rules/isolate-writes.md`.
- Reviewers and verifiers stay independent and read-only to tracked source.
- The user alone confirms product intent.

## Changed goals

A changed target identity, mode, behavior, success, scope, delivery intent, or other material decision invalidates its dependent artifacts through the helper, as the artifact contracts' invalidation table lists, and returns the run to the earliest affected stage. Invalidate on material change per `../../rules/explicit-confirmation.md`. Keep unrelated valid evidence while its bindings match.

When an improve request changes the contract, or a modify request keeps it, ask the user one question. On their yes, switch the mode through the helper and read the new mode file.

## Status

During work, report the exact target, mode, kept contract used, active stage, queue position, latest valid evidence, invalidated evidence, next gate, and user-owned decisions. At a failed gate, report the gate, retained evidence, invalidated artifacts, and the next decision or proof needed. At finalization, before asking for delivery authority, report the revision, the final description, the ten scores, the review verdict, verification, limitations, and the release-record locator, then report the delivery result. At `reported`, report the ten scores with their failing criteria, the conformance ledger, the review verdict, and limitations, with the target unchanged.

## Review context contract

Launch every review agent in a fresh, context-free conversation.

The review handoff is a locator of at most 300 physical lines, counting inherited or forked conversation history, inline dispatch text, follow-up messages, and every generated context artifact regardless of carrier or extension. Count the complete handoff before launch and before every follow-up, and send it only while the total stays within the limit.

Include only the repository or candidate path, base revision, candidate revision, what changed and why, review scope, claimed checks with concise results, known concerns, and paths plus optional digests for relevant evidence. Reference separately an accepted specification file that existed before dispatch. Reviewers reject a review-time summary, copy, or relabelled package as a specification.

Reference diffs, source files, test logs, terminal output, transcripts, other repository content, and binary or opaque review context only by existing path, command, or optional digest alone, leaving them uncopied. Judges inspect the pinned revision per `../../rules/pin-scope.md` with repository tools and run any focused checks needed to verify the claims. Answer a larger convenience package request with this locator handoff.
