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

Before production edits, bind the complete accepted criterion basis. For Plan-backed work, call `freeze_acceptance_basis(repo, branch, workflow_id, expected_revision)` through the packaged helper, or its `freeze-acceptance` CLI command with the exact workflow ID and revision. This preserves the complete current ready planning graph in the private `acceptance-bases` namespace outside rotating Plan generations. Retain its returned absolute path, byte digest, graph digest, original workflow/revision, and baseline commit. Repeated freezing cannot replace an existing basis with a later graph. Never use `current.yaml` or `previous.yaml` as the immutable criterion locator. Use a real accepted specification by immutable locator and digest, or capture the complete direct brief as immutable criterion text in the coordinator. Include observable positive and negative behavior, protected behavior, constraints, non-goals, proof obligations, and Design decisions. Never reconstruct missing criteria from the candidate. Transport that same basis unchanged to every worker and judge under the final Review context contract. Verify its retained byte digest before dispatch. If the basis is missing or cannot be transported completely, stop before production edits and identify only the missing material meaning. If the original basis is missing after production edits began, stop and identify the missing original criteria rather than freeze the updated graph or invent assent. Preserve already accepted user intent without redundant confirmation.

If the user approves a material scope or choice change during implementation, settle or stop existing worker activity before starting a separate implementation epoch. Keep every old worker and judge bound to its original basis. Update and reconfirm the same Plan workflow, then explicitly call `freeze_acceptance_epoch(repo, branch, workflow_id, expected_revision, previous_path, previous_digest, reason)` or `freeze-acceptance-epoch` with `--previous-basis`, `--previous-digest`, and `--reason`. The helper requires changed covered meaning and a later typed projection approval, then preserves a distinct revision-and-digest snapshot with the previous basis binding. The new-epoch reason is a coordinator attestation, not authenticated user assent. Transport the new receipt only to the new epoch's workers and judges. Never rebind prior receipts or replace criteria for existing actors. Ordinary evidence refreshes and execution updates cannot create a new acceptance epoch. Retain cumulative budgets and regression obligations across epochs.

Do not reopen settled product or Design decisions. Ask the user only for a
material decision or authority that cannot be discovered. If pre-existing
uncommitted changes affect the work, stop, explain the safe options, and ask what
to do instead of stashing, discarding, overwriting, or absorbing them silently.

For Plan-backed work, read the current canonical graph through the Plan helper for execution state while keeping the frozen accepted basis unchanged. The snapshot is complete original criterion text, not another canonical workflow or a new approval. `load_acceptance_basis(path, digest)` verifies its retained bytes and historical structure without refreshing it from current repository state. Keep cumulative regression obligations separate from the original basis. Do not invent a second
state format and do not make workers graph writers. Return compact node results to the active
coordinator for graph updates, and a complete direct brief may run without a Plan Graph.

Resolve that helper from the loaded skill at `../../scripts/plan_graph.py` (`plugins/expskill/content/scripts/plan_graph.py` is only the source-package locator).

Use the parent-approved sandbox and approval policy. Before production edits or worker dispatch, verify supported write authority for the assigned checkouts, repository and worktree Git metadata, and the coordinator's original private acceptance-basis namespace when a new snapshot must be created. Workers and judges only read their immutable acceptance receipts. Codex Implementer profiles inherit the parent's current permissions, so a restricted parent may prevent the required local commit. If a required operation is unavailable or denied, stop before production edits and report the exact denied capability and preserved work to the invoking user or parent. Preserve edits and original acceptance receipts after a later denial, and return blocked without claiming an uncreated commit. Do not change sandbox settings, request broader permissions from a child, move private state, replace original criteria, or reset the run to bypass the restriction. Any host-authority decision remains with the invoking user or authorized parent, and a continuation retains the accepted scope, budget, and original receipts.

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
Every judge must also receive the complete accepted criterion basis captured
before production edits for that implementation epoch. Reference the frozen accepted graph snapshot or immutable specification
with its revision and digest, or include that complete criterion text inline
within the 300-line total. This is the sole accepted-requirements exception to
the locator-only rule. It is not a review-time summary or copied transcript.
A missing or incomplete basis makes the handoff invalid. Stop before dispatch
rather than truncate it or infer requirements from the candidate.
The exception applies only to the complete accepted graph snapshot preserved before production edits or a specification file that existed before review
dispatch. It does not permit a review-time summary, copy, or relabelled context
package.

Do not copy or embed diffs, source files, test logs, terminal output,
transcripts, or other repository content. Do not attach binary or opaque review
context. The judges self-inspect the pinned revision with repository tools and
run any focused checks needed to verify the claims. A request for a larger
convenience package is not a reason to create one.
