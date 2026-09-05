---
name: skill-builder
description: Applies only when the user explicitly asks to create, improve, redesign, or evaluate an agent skill.
---
# Skill Builder

Build, improve, redesign, or evaluate one exact agent skill through evidence that is bound to the user's confirmed intent.

## Boundary

`$skill-builder` is standalone and explicit-only. Stay inactive for ordinary development, product planning, application design, documentation that is not an agent skill, installation-only work, and lifecycle routing. Do not invoke or depend on a product lifecycle phase or an ambient authoring skill.

Success exists only when one exact revision has a confirmed contract, frozen evaluation evidence, isolated trial evidence, builder-run conformance, independent review, ten independently satisfied target category scores, verification, and retained release evidence. Static validation alone is never completion.

Read [artifact contracts](references/artifact-contracts.md) completely at run start and again before resuming persisted work. Read [evaluation rubric](references/evaluation-rubric.md) completely before freezing the evaluation pack and before every review or scoring pass.

## Durable run state

Resolve the package's `scripts/run_state.py` relative to the loaded skill. Execute it for run creation, validation, stage transitions, downstream invalidation, recovery, finalization, and authorized cleanup. Read its runtime help before the first operation and use the interface it exposes instead of guessing flags or editing state directly.

The helper owns private XDG state outside target repositories. Require immutable hash-chained transition receipts, strict manifests, digests, bounded raw artifacts, exact target identity, optional Git identity, revisions, stages, queue state, and one active-target lock. Treat current-state files as replaceable derived indexes, never as the append-only source of truth. Treat missing, stale, inconsistent, or unauditable state as a failed gate.

Before every stage transition, have the helper validate the receipt chain, immutable artifacts, derived state, input bindings, target snapshot, and allowed transition. A successful transition appends one canonical receipt linked to the prior receipt digest. A failed transition appends nothing, leaves the current stage unchanged, and blocks downstream work.

## Ordered workflow

### 1. Resolve the host, target, mode, and authority

Ground the requested host and exact target from supplied context, host registries, skill roots, invocation names, metadata, and available repository facts before asking a question. Record canonical host identity, canonical target identity, authority, current snapshot, optional Git identity, and intended delivery effects.

Scan exact names, aliases, invocation tokens, overlapping responsibility, and near neighbours. A near neighbour is evidence to compare, never permission to repurpose it.

Resolve mode from observed identity:

| Observation | Mode and action |
|---|---|
| Exact target is absent after overlap and near-neighbour scans | Create. Record the absent-target baseline and overlap map. |
| Exact target exists | Improve. Record its current behavior, failures, strengths, and preserved regressions. |
| Exact identity or replacement intent remains ambiguous after facts are exhausted | Ask one concise question and wait. |

Never overwrite an existing exact target as creation. Never silently rename, replace, or absorb a near neighbour.

Create or resume a queue with exactly one active target. Finish, explicitly abandon, or validly pause that target before activating another. A multi-target request does not waive any target's confirmation, evaluation, or release gates.

### 2. Capture the mode-specific baseline

Capture the baseline before any candidate edit.

For create mode, prove the exact target is absent, retain the overlap map, record relevant host conventions, and identify regressions that nearby skills must preserve.

For improve mode, snapshot the exact target and record current behavior, observed failures, strengths, context cost, tool and permission behavior, and preserved regressions.

Bind the baseline to the target snapshot and run revision. If the target changes, invalidate the baseline and every dependent artifact.

### 3. Run three blind research lanes

Launch exactly three bounded, blind, independent web research lanes. Assign GPT-5.6-Luna at max reasoning to each lane:

1. Domain techniques relevant to the target's job.
2. Agent-skill design, instruction, interaction, and tooling practices.
3. Evaluation methods, adversarial cases, and failure modes.

Bound each lane's question, source scope, and evidence budget before launch. Researchers return evidence cards only, use direct primary or authoritative sources, state applicability and limitations, and do not edit any target or draft the candidate. Do not reveal candidate direction, hidden cases, desired scores, or sibling output.

If any required lane cannot run or lacks direct evidence for its bounded question, record the limitation and stop before design convergence or candidate work.

### 4. Sieve every evidence card

Normalize and deduplicate the research pack. Classify every card as `adopt`, `experiment`, or `reject`. Record a reason, conflicts, and retained dissent for each disposition. Pass only adopted and experimental evidence into design. Never turn majority agreement into evidence.

### 5. Explore and challenge coherent designs

Collaborate with the user on materially different, coherent designs. Compare their mechanisms and tradeoffs across triggering, behavior, tools, permissions, delegation, outputs, recovery, composition, and context cost. Challenge viable designs with near neighbours, hostile inputs, changed goals, missing evidence, and authority boundaries.

Resolve facts autonomously. Ask only for a material product decision that facts cannot settle, one question per turn. Record the user's decisions and rejected alternatives. The user owns material product decisions.

### 6. Produce one concrete skill contract

Write one contract with these required fields:

- purpose
- success signal
- triggers
- non-triggers
- inputs and preconditions
- ordered behavior and decision rules
- allowed and forbidden actions
- tools
- permissions and authority
- delegation boundaries
- outputs and consumers
- failures and blocked behavior
- changed-goal behavior
- stopping conditions
- required resources
- explicit non-goals

Resolve every conflict with host rules and preserved regressions. Bind the contract to the baseline, retained evidence, design record, and target snapshot.

### 7. Obtain user confirmation

Present the current contract and wait for explicit user confirmation. Record the user's confirmation against the exact contract digest and target identity. The agent may propose that the contract is ready for confirmation, but cannot confirm it, force the session to end, or treat silence, urgency, seniority, prior broad approval, or an instruction to skip questions as confirmation.

A material contract change invalidates confirmation and all downstream artifacts. Return to this stage after correcting the contract.

### 8. Freeze acceptance before candidate work

Create visible development cases, frozen validation cases, and hidden release cases. Bind the evaluation pack to the confirmed contract digest and unchanged target snapshot. Include positive, negative, near-neighbour, ambiguous, adversarial, recovery, permission, output, and multi-turn cases as applicable.

Keep hidden expectations and their oracles unavailable to candidate implementers and trial agents. Prove that new capability cases expose the baseline gap and that preserved regressions remain valid. Freeze the pack before any candidate edit.

Reject entry to candidate work unless the helper confirms all four bindings: the current contract digest, its user-confirmation record, the frozen evaluation-pack digest, and an unchanged target snapshot.

### 9. Build one isolated candidate

Create one isolated candidate in a disposable copy or isolated worktree. Never test by editing a production skill. Preserve unrelated work. Give exactly one implementer write access to the candidate and only its accepted brief, owned paths, visible cases, and necessary target artifacts.

Keep production targets, sibling skills, hidden cases, research conclusions not retained by the sieve, and delivery surfaces outside the implementer's write authority. Record the candidate revision, digest, diff, and checks.

### 10. Run fresh-context trials

Run create or improve trials, as selected by the current mode, in disposable targets with a fresh agent context for every case. Capture raw prompts, outputs, tool events, before and after target manifests, and trial receipts. Bind each receipt to the candidate, loaded skill, request, context identity, target snapshot, filesystem result, and verdict.

Candidate agents receive only the candidate skill, realistic request, visible case material, and necessary target artifacts. Keep hidden expectations, prior transcripts, diagnoses, desired results, and sibling outputs unavailable. Treat leakage, wrong-skill loading, unauthorized writes, missing trace evidence, and false success as failed trials.

### 11. Obtain independent review before scoring

Give an independent read-only target reviewer a compact locator handoff. The
aggregate authored review handoff includes inline dispatch text, follow-up
messages, and every generated context artifact regardless of carrier or
extension. It is a locator, not a payload, and totals at most 300 physical
lines. Count the complete handoff before launch and stop before dispatch when
it exceeds the limit.

Include only the repository or candidate path, base revision, candidate
revision, what changed and why, review scope, claimed checks with concise
results, known concerns, and paths plus digests for relevant evidence. A real
accepted specification file is referenced separately when it exists. The
exception applies only to a specification file that existed before review
dispatch. It does not permit a review-time summary, copy, or relabelled context
package.

Reference the confirmed contract, host rules, evaluation pack, original raw
trial evidence, preserved regressions, artifact manifest, and this rubric by
path and digest. Do not restate them.

Do not copy or embed diffs, source files, test logs, terminal output,
transcripts, or other repository content. Do not attach binary or opaque review
context. The reviewer must self-inspect the pinned candidate with repository
tools and open only the referenced evidence needed for the review. Verify
independence, revision freshness, read-only behavior, and access to the
referenced artifacts. Do not give the reviewer a desired verdict or create a
larger convenience package.

Require evidence, impact, correction, severity, affected target criteria, and `ready` or `not ready`. An inaccessible, contaminated, stale, or otherwise invalid review blocks advancement and cannot be scored.

Treat a valid `not ready` review or a valid review with High or Medium findings as negative scoring evidence. Map every finding to affected target criteria, mark those criteria failing, and carry them into the ordinary lowest-category repair loop. No material finding may coexist with a score of 10.

### 12. Score and repair the lowest category

Evaluate the builder-run conformance gates in the evaluation rubric as a separate pass or fail ledger. These gates prove that this run followed its research, confirmation, acceptance-first, isolation, evidence, state, authority, and cleanup rules. A failed gate blocks release and returns the run to its earliest affected stage, but never becomes a target category criterion.

Score the exact candidate target against the confirmed target contract, host rules, accepted neighbouring boundaries, and frozen evaluation pack. Never score the candidate against `$skill-builder` identity, invocation policy, package layout, or workflow. Score these exact target categories using the evaluation rubric:

1. triggering
2. scope discipline
3. workflow quality
4. collaboration
5. output contract
6. safety
7. recovery
8. composability
9. context efficiency
10. testability

Use integer scores from 0 to 10. Every category must independently reach 10. Never average, waive, round, or replace evidence with confidence.

Select the lowest score. Break ties by greater safety or correctness impact, then greater dependency impact, then earlier position in the category list. Identify the evidence preventing 10, strengthen or add the proving case, issue one bounded repair brief, edit only the isolated candidate, rerun affected and preserved cases, obtain independent review, and rescore the exact new revision. Continue until every category is 10.

Missing evidence, a flaky target gate, a valid negative review, conflicting requirements, or an unresolved user decision keeps the affected target category below 10 and stops target completion. Invalid review evidence blocks advancement without receiving a score.

### 13. Review, verify, and finalize one exact revision

After all ten target categories reach 10 and every builder-run conformance gate passes, run a final independent target review against the same exact candidate revision. Validate the review before using its verdict. An invalid final review blocks advancement without changing scores. A valid `not ready` review or valid High or Medium finding lowers every affected target category below 10 and returns it to repair.

After a valid `ready` final review, run independent verification against that exact revision. Verification records exact commands or operations, exit status, relevant output, target manifests, and revision. Any candidate change invalidates both final gates and returns affected categories to repair.

Finalize through the state helper only after every builder-run conformance gate passes, every target category is 10, the final review is valid and `ready`, verification passes, every binding matches, and release evidence is retained. The finalization terminal stage retains the contract, confirmation, evaluation pack, candidate identity, conformance ledger, scorecard, review, verification, limitations, and release record.

Commit, install, push, publish, or otherwise deliver only with explicit user authority for that effect. Record any accepted installation or integration against the exact finalized revision.

Cleanup is a separate terminal transition. It requires explicit user authority plus a recorded accepted installation or integration. Before deletion, write and validate a helper-owned parent-level tombstone outside the run directory, bound to the run identity, final transition digest, final run-manifest digest, and accepted delivery or installation record digest. Only then may cleanup delete the helper-owned run directory. The tombstone survives. Cleanup must never delete a target skill, repository, candidate outside that owned directory, tombstone, or any unowned path.

## Roles, tools, and permissions

- The main agent owns identity resolution, user collaboration, evidence synthesis, hidden-case custody, state transitions, and authority checks.
- Exactly three research agents use web access and return evidence only. They never edit.
- One candidate implementer may write only inside the isolated candidate.
- Trial agents may write only inside their disposable targets.
- Reviewers and verifiers are independent and read-only to tracked source.
- No delegated agent may confirm product intent, broaden scope, reveal hidden material, deliver externally, or clean state.

## Changed goals and gate failures

When the user changes target identity, behavior, success, scope, delivery intent, or another material decision, record the change, invalidate every dependent artifact through the helper, and return to the earliest affected stage. Preserve unrelated valid evidence only when its bindings still match.

Stop at the current gate when identity, authority, evidence, confirmation, isolation, state integrity, target stability, review readiness, scoring, or verification is unresolved. Report the exact failed gate, retained evidence, invalidated artifacts, and next decision or proof needed. Never report a candidate or run as complete because files changed, agents ran, or static checks passed.

## Status and output contract

During work, report the exact target, mode, active stage, queue position, latest valid evidence, invalidated evidence, next gate, and any user-owned decision. At finalization, report the exact revision, builder-run conformance, ten target category scores, review validity and verdict, verification evidence, retained limitations, release-record locator, and authorized delivery result.

A status report is an observation, not a stage transition. Only a validated hash-chained helper receipt advances the live run. The cleaned terminal state additionally requires its valid durable tombstone and deletion of only the helper-owned run directory.
