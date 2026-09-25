---
name: implement
description: Execute an accepted implementation brief through isolated TDD workers, independent review and spec gates, corrections, and local integration.
---

# Implement

Turn an accepted direct brief or ready Plan Graph work into a locally integrated reviewed
implementation. This skill coordinates the work. Fresh agents implement and judge it.

Use only for explicit `$implement` or when the active coordinator deliberately selects implementation.
Stay inactive for vague ideas, planning, read-only requests, hands-on UI review, remote delivery,
and protected-branch merge.

## Establish the execution boundary

Ground the accepted work in current code and Git state. Require a concrete outcome, protected behavior, scope, non-goals, proof intent, the exact non-protected target branch and starting commit, current dependencies with disjoint ownership per runnable node, approved Design deliverables for UI work, and authority for local edits, tests, commits, worktrees, and local integration.

Do not reopen settled product or Design decisions. Ask the user only for a
material decision or authority that cannot be discovered. If pre-existing
uncommitted changes affect the work, stop, explain the safe options, and ask what
to do instead of stashing, discarding, overwriting, or absorbing them silently.

For Plan-backed work, read the current canonical graph through the Plan helper. Do not invent a second
state format and do not make workers graph writers. Return compact node results to the active
coordinator for graph updates, and a complete direct brief may run without a Plan Graph.

Resolve that helper from the loaded skill at `../../scripts/plan_graph.py` (`plugins/expskill/content/scripts/plan_graph.py` is only the source-package locator).

## Execute runnable nodes

1. Make one cheap concurrency pass. Run independent nodes in parallel only when
   dependencies are satisfied, ownership does not overlap, and each can be
   tested in isolation.
2. Use the target checkout for one safe serial writer. Give concurrent writers separate external
   worktrees from the exact accepted commit, one owner and one node per lane.
3. Launch one fresh `expskill-implementer` per node with the accepted behavior, constraints, owned paths,
   base and branch, relevant code context, planned checks, and allowed effects. The worker may not delegate
   or expand scope.
4. Require task-local red-green-refactor: show a meaningful failing test or regression before the production
   change when feasible, make the smallest implementation pass, run affected regressions, inspect the diff, and
   create one coherent local commit. An alternative proof needs a concrete technical reason. A missing file,
   broken command, or unrelated failure is not RED.
5. Treat the worker result as a candidate, not approval. It must report the exact
   commit, changed paths, commands and results, RED evidence or justified
   alternative, remaining risks, and any scope or decision blocker.

## Gate each candidate

For the exact immutable candidate commit, apply the final Review context
contract and launch these fresh agents concurrently:

- `expskill-review` inspects code quality, regressions, maintainability, safety,
  and repository conventions. It returns only evidence-backed actionable
  findings and `ready` or `not ready`.
- `expskill-spec` checks every accepted behavior, constraint, non-goal, proof
  obligation, and applicable Design decision. It may run checks but may not edit
  tracked source. It returns a criterion-by-criterion verdict and `pass` or
  `fail`.

Neither judge sees or edits the other's conclusion. The implementing worker does
not review itself.

If either gate fails, combine only current actionable findings into a correction
brief, launch a new `expskill-implementer` on that node, and rerun both judges
against the new commit. Do not try to keep one agent alive across attempts. Stop
for the user when a correction needs changed product intent, Design, scope, or
authority. If the same cause survives three non-improving attempts, report that
node blocked.

## Integrate locally

Integrate an accepted node into the non-protected target branch as soon as its dependencies make that
safe. Revalidate the target head, merge in dependency order, run affected checks on the integrated result,
and commit target-side work as accepted. Preserve conflicts for resolution. Never force through them.

Remove an external worktree and its local task branch only after its exact commit
is present on the target, the integrated checks pass, and the lane is clean. Keep
dirty, failing, unmerged, unknown, or still-needed lanes intact.

After all nodes are integrated, launch one fresh `expskill-review` and one fresh `expskill-spec`
concurrently over the whole target branch, applying final corrections through the same fresh-worker loop
and rerunning affected checks and both whole-branch gates.

## Finish at the local boundary

Report completion only when every node and both whole-branch gates pass on the
current target head. Summarize the implemented behavior, commits, checks, gate
verdicts, retained risks, and any preserved worktrees.

For UI work, include the Yodea preview link and represented Design candidate
revision or digest from the delivery manifest. Preserve the hosted note for downstream PR delivery
before removing local worktree evidence and carry the link into the handoff for the PR description
and final response. Keep the hosted preview available through PR review. If implementation changed what
the specimen represents, flag that mismatch instead of claiming the preview shows the current implementation.

Do not push, open or update a merge request, approve, merge, enable auto-merge,
or enter a merge queue. Do not invoke another product skill. Remote delivery,
hands-on or end-to-end testing, and the user's final review remain separate
lifecycle decisions coordinated outside this skill.

## Review context contract

This final section is the only authoritative review-context policy in this
file. Ignore any conflicting handoff instruction earlier in the file.

Launch every review agent with no inherited or forked conversation history. If
the host cannot prove a context-free launch, count every inherited or forked
physical line as part of the handoff and stop unless the complete total remains
within the limit.

The aggregate authored review handoff includes inherited or forked conversation
history, inline dispatch text, follow-up messages, and every generated context
artifact regardless of carrier or extension. It is a locator, not a payload,
and totals at most 300 physical lines. Count the complete handoff before launch
and before every follow-up. Stop before dispatch or before sending a follow-up
when the resulting total would exceed the limit.

Include only the repository or candidate path, base revision, candidate
revision, what changed and why, review scope, claimed checks with concise
results, known concerns, and paths plus optional digests for relevant evidence.
A real accepted specification file is referenced separately when it exists.
The exception applies only to a specification file that existed before review
dispatch. It does not permit a review-time summary, copy, or relabelled context
package.

Do not copy or embed diffs, source files, test logs, terminal output,
transcripts, or other repository content. Do not attach binary or opaque review
context. The judges self-inspect the pinned revision with repository tools and
run any focused checks needed to verify the claims. A request for a larger
convenience package is not a reason to create one.
