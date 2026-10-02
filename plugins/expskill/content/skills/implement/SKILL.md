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

Derive an acceptance oracle from those criteria before inspecting implementation output. For each material behavior, name an observable expected result and a boundary or counterexample that could disprove it. Keep these expectations independent of worker-authored tests and preserve them through corrections. Stabilize shared interfaces before fan-out, including for direct briefs.

Do not reopen settled product or Design decisions. Ask the user only for a
material decision or authority that cannot be discovered. If pre-existing
uncommitted changes affect the work, stop, explain the safe options, and ask what
to do instead of stashing, discarding, overwriting, or absorbing them silently.

For Plan-backed work, read the current canonical graph through the Plan helper for execution state while keeping the frozen accepted basis unchanged. The snapshot is complete original criterion text, not another canonical workflow or a new approval. `load_acceptance_basis(path, digest)` verifies its retained bytes and historical structure without refreshing it from current repository state. Keep cumulative regression obligations separate from the original basis. Do not invent a second
state format and do not make workers graph writers. Return compact node results to the active
coordinator for graph updates, and a complete direct brief may run without a Plan Graph.

Resolve that helper from the loaded skill at `../../scripts/plan_graph.py` (`plugins/expskill/content/scripts/plan_graph.py` is only the source-package locator).

## Budget and review reuse

Before any worker launch, count N unfinished accepted nodes and preflight at least `3N + 2` calls for one implementer and two judges per node plus the final two whole-branch judges. Include already spent calls, known optional research, retries, and correction allowances in the same run total. Respect the remaining host and run limits, including the standard 30-call implementation limit. Reserve the final two judge calls throughout the run. If the required work cannot fit, stop before production edits with the exact shortfall and a bounded execution option, never omit a gate or silently reset the counters.

Initialize a standalone implementation run with the policy's 30-call, three-retry, two-hour wall-clock, six-in-flight, depth-one limits. Inherit a tighter lifecycle remainder or child allocation when present, with its run ID, limit source, start time, and existing spent/outstanding dispatch IDs. The launching coordinator keeps one cumulative run identifier and call, retry, elapsed-time, and in-flight totals across nodes, corrections, phase changes, resumed turns, and child conversations. Count each actual call once and reconcile child counts without double-counting. A new conversation or changed pin does not start a new budget. Reserve a unique dispatch ID and child allowance before each launch. Every worker and judge returns that ID, used calls and retries, elapsed time, outstanding descendants, and remaining allocation on success, failure, or interruption. Reconcile unique IDs once, retain uncertain interrupted allocations as outstanding, and never issue the same capacity twice. Recheck the remaining allowance before every dispatch. Host route-local limits are additional restrictions, not proof that this cumulative budget was enforced. With OpenCode, propagate one `EXPSKILL_RUN_ID` into every controlled conversation so observed profile-route dispatch counts and elapsed time persist across sessions and process restarts. This enforces only calls observed by that host hook. Generic agents and other providers still require the coordinator's reconciled totals, and must not be described as mechanically enforced. Never change the run identifier merely to reset spent calls. A bounded batch can finish only its accepted subset, with remaining work and spent calls carried forward, never claim the whole request complete.

Reuse a valid review only when candidate and base pins, complete accepted criterion basis, review scope, evidence inputs, and reviewer independence are unchanged. Retain the original verdict and provenance. A review agent must not have implemented or edited the candidate and must not see a sibling judge's conclusion. Any changed input requires a new review of affected criteria. Whole-branch review may reuse a node verdict only when its complete scope and inputs actually coincide, never merely because the scores agree.

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
   broken command, or unrelated failure is not RED. Retain the exact regression test digest, production baseline, command, environment prerequisites, observed failing assertion, and raw output path. Show that the failure exercises the accepted production behavior, then rerun the same regression against the corrected candidate. A concrete alternative must identify the behavior and proof limit, not merely say testing is inconvenient.
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

If either gate fails, combine current actionable findings into a correction
brief with the original accepted criterion basis and the cumulative regression obligations from resolved findings, launch a new `expskill-implementer` on that node, and rerun both judges
against the new commit. Do not try to keep one agent alive across attempts. Stop
for the user when a correction needs changed product intent, Design, scope, or
authority. If the same cause survives three non-improving attempts, report that
node blocked. Track the complete outstanding finding set and candidate pins across attempts. A correction may not drop or weaken a prior regression obligation without evidence that the underlying accepted criterion changed. Detect alternating or recurring failures as non-improvement even when their labels differ.

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
verdicts, retained risks, and any preserved worktrees. Return a budget handoff on completion, blocking, exhaustion, or interruption: run ID, limit source and effective limits, own dispatch ID, completed and outstanding descendant dispatch IDs, spent calls/retries, elapsed time, in-flight count, reserved final calls, and remaining allowance. The parent counts your launch once and only adds new descendant IDs, never adds the same calls twice.

For UI work, include the hosted preview link or local native review entry and hosting limitation,
with the represented Design candidate revision or digest from the delivery manifest. Preserve its
review note for downstream PR delivery and carry that review entry into the PR description and
final response. Keep hosted previews available through PR review. For local mode, preserve or
transfer the exact specimen and reproducible launch recipe before removing worktree evidence. If implementation changed what
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
