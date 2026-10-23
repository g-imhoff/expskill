---
name: implement
description: Build an accepted implementation brief with isolated TDD workers and review gates. Only the user or another ExpSkill skill can load $implement.
---

## Precondition

Start from an ready Plan Graph made via `$expskill:plan`.

## Establish the execution boundary

Ground the accepted work in current code and Git state. Require a concrete outcome, protected behavior, scope, non-goals, proof intent, current dependencies, and approved Design deliverables for UI work. Establish the execution boundary (branch, pin, ownership, authority) per `../../rules/pin-scope.md`. Prove parallel-safety per `../../rules/bounded-fanout.md` and lane ownership per `../../rules/isolate-writes.md`.

Preserve settled product and Design decisions. Ask the user only for a
material decision or authority that cannot be discovered. If pre-existing
uncommitted changes affect the work, stop, explain the safe options, and ask what
to do while leaving them untouched.

Use only the existing state formats, for direct briefs too. For Plan-backed work, read the
current canonical graph through the Plan helper. Implement, not workers, returns compact
node results to the active coordinator, which alone makes graph updates. A complete direct brief may run
without a Plan Graph.

Resolve that helper from the loaded skill at `../../scripts/plan_graph.py` (`plugins/expskill/content/scripts/plan_graph.py` is only the source-package locator).

## Execute runnable nodes

1. Make one cheap concurrency pass. Fan out only over proven seams per `../../rules/bounded-fanout.md`.
2. Use the target checkout for one safe serial writer. Create the lane worktree per `../../rules/isolate-writes.md`.
3. Launch one fresh `implementer` per node with the accepted behavior, constraints, owned paths,
   base and branch, relevant code context, planned checks, and allowed effects. Constrain each worker to its lane per `../../rules/isolate-writes.md`.
4. Require task-local red-green-refactor: show a meaningful failing test or regression before the production
   change when feasible, make the smallest implementation pass, run affected regressions, inspect the diff, and
   create one coherent local commit. An alternative proof needs a concrete technical reason. A missing file,
   broken command, or unrelated failure leaves RED unproven.
   Before the worker adds any test, require four authoring answers, and hold the
   test until all four are answered: (1) what observable behavior, invariant, or
   independent contract does it protect? (2) what credible regression makes it
   fail? (3) why does existing coverage miss that failure? Each
   contract has one primary test owner at the strongest boundary, so prefer
   extending a table-driven case or shared fixture over a near-duplicate test.
   (4) does it need a production seam (export, flag, wrapper, injection hook)
   that no production caller needs? If yes, move the test to the real
   boundary. Then reject a new test matching a junk pattern unless it
   independently guards a named contract: assertion-free coverage probes,
   self-comparisons, copied fixtures, manifests, or export lists, exact
   source, import, or string greps, private-predicate or call-shape tests
   duplicated at a real boundary, duplicate invocations of the same contract,
   mocks that implement the asserted behavior, expected values produced by the
   code under test, tests whose only purpose is preserving test-only seams,
   negative controls that pass for an unrelated reason. A test that would
   break under behavior-preserving refactoring asserts implementation, not
   behavior. Rewrite it at the owning boundary before landing it. A bug
   regression must fail on the pre-fix code for the intended reason and pass
   after the owner-boundary repair. One owner-boundary regression covers the
   bug at every layer it crosses, so write only that one.
   Whole-subsystem pruning campaigns are a non-goal here. Note one
   as a follow-up for separate work.
5. Treat the worker result as a candidate until both gates pass. It must report the exact
   commit, changed paths, commands and results, RED evidence or justified
   alternative, remaining risks, and any scope or decision blocker. Bind worker evidence to its pin per `../../rules/evidence-report.md`.

## Gate each candidate

Gate the pinned candidate per `../../rules/pin-scope.md`. For that candidate, apply the final Review context
contract and launch these fresh agents concurrently:

- `review` inspects code quality, regressions, maintainability, safety,
  and repository conventions. It returns only evidence-backed actionable
  findings and `ready` or `not ready`.
- `spec` checks every accepted behavior, constraint, non-goal, proof
  obligation, and applicable Design decision. It may run checks and leaves
  tracked source unchanged. It returns a criterion-by-criterion verdict and `pass` or
  `fail`.

Keep the judges isolated from each other. Gate results come only from
the fresh judges.

If either gate fails, combine only current actionable findings into a correction
brief, launch a new `implementer` on that node, and rerun both judges
against the new commit. Each attempt is a new agent. Reconfirm changed intent/scope/authority per `../../rules/explicit-confirmation.md`. If the same cause survives three non-improving attempts, report that
node blocked.

## Integrate locally

Integrate an accepted node into the non-protected target branch as soon as its dependencies make that
safe. Integrate lanes serially per `../../rules/isolate-writes.md`. Merge in dependency order, run affected checks on the integrated result,
and commit target-side work as accepted. Preserve conflicts for resolution, stopping integration at them.

Remove worktrees/branches only per `../../rules/isolate-writes.md`. Keep
dirty, failing, unmerged, unknown, or still-needed lanes intact.

After all nodes are integrated, launch one fresh `review` and one fresh `spec`
concurrently over the whole target branch, applying final corrections through the same fresh-worker loop
and rerunning affected checks and both whole-branch gates.

## Finish at the local boundary

Report completion only when every node and both whole-branch gates pass. Finish only on a green bound head per `../../rules/pin-scope.md`. Summarize the implemented behavior, commits, checks, gate
verdicts, retained risks, and any preserved worktrees.

For UI work, include the Yodea preview link and represented Design candidate
revision or digest from the delivery manifest. Preserve the hosted note for downstream PR delivery
before removing local worktree evidence and carry the link into the handoff for the PR description
and final response. Keep the hosted preview available through PR review. If implementation changed what
the specimen represents, flag that mismatch and describe the preview as the approved specimen.

Keep every Git write local. Other product skills, remote delivery (push, merge requests, approvals, merges,
auto-merge, merge queues), hands-on or end-to-end testing, and the user's final review remain separate lifecycle
decisions coordinated outside this skill.

## Review context contract

Launch every review agent in a fresh, context-free conversation.

The review handoff is a locator of at most 300 physical lines, counting inherited or forked conversation history, inline dispatch text, follow-up messages, and every generated context artifact regardless of carrier or extension. Count the complete handoff before launch and before every follow-up, and send it only while the total stays within the limit.

Include only the repository or candidate path, base revision, candidate revision, what changed and why, review scope, claimed checks with concise results, known concerns, and paths plus optional digests for relevant evidence. Reference separately an accepted specification file that existed before dispatch. Reviewers reject a review-time summary, copy, or relabelled package as a specification.

Reference diffs, source files, test logs, terminal output, transcripts, other repository content, and binary or opaque review context only by existing path, command, or optional digest alone, leaving them uncopied. Judges inspect the pinned revision per `../../rules/pin-scope.md` with repository tools and run any focused checks needed to verify the claims. Answer a larger convenience package request with this locator handoff.
