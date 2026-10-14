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

Resolve the package's `scripts/run_state.py` relative to the loaded skill and use only the interface its runtime help exposes for run creation, validation, stage transitions, downstream invalidation, recovery, finalization, and authorized cleanup. Never guess flags or edit state directly.

The helper owns private XDG state outside target repositories and requires immutable hash-chained transition receipts, strict manifests, digests, bounded raw artifacts, exact target identity, optional Git identity, revisions, stages, queue state, and one active-target lock. Treat current-state files as replaceable derived indexes and treat missing, stale, inconsistent, or unauditable state as a failed gate. Before every transition the helper validates the receipt chain, immutable artifacts, derived state, input bindings, target snapshot, and allowed transition. Success appends one canonical receipt linked to the prior digest. Failure appends nothing, keeps the current stage, and blocks downstream work. Open the binding, invalidation, recovery, and tombstone sections of the artifact contracts reference whenever a transition fails or persisted work resumes.

## Ordered workflow

Run the thirteen stages in order. Each stage ends at its gate. A failed gate stops the run and returns to the earliest affected stage through the state helper, which invalidates every dependent artifact. Never report completion from changed files, executed agents, or static checks alone.

| # | Stage | Gate |
|---|---|---|
| 1 | Resolve host, target, mode, and authority | Exact target, mode, authority, snapshot, and single-active queue recorded |
| 2 | Capture the mode-specific baseline | Baseline bound to snapshot and revision before any candidate edit |
| 3 | Run three blind research lanes | Three bounded lanes returning evidence cards only, or a recorded stop |
| 4 | Sieve every evidence card | Every card disposed as adopt, experiment, or reject with reasons |
| 5 | Explore and challenge coherent designs | User-owned product decisions recorded with alternatives retained |
| 6 | Produce one concrete skill contract | One contract bound to baseline, evidence, design, and snapshot |
| 7 | Obtain user confirmation | Explicit user confirmation bound to the exact contract digest |
| 8 | Freeze acceptance before candidate work | Evaluation pack frozen with all four entry bindings verified |
| 9 | Build one isolated candidate | One isolated candidate with digest, diff, and checks |
| 10 | Run fresh-context trials | Fresh-context receipts with raw evidence and blindness checks |
| 11 | Obtain independent review before scoring | Valid review with severity-mapped findings, or a blocked advance |
| 12 | Score and repair the lowest category | Every target category independently at 10 with all gates passing |
| 13 | Review, verify, and finalize one exact revision | Valid ready final review, passing verification, retained release evidence |

### 1. Resolve the host, target, mode, and authority

Ground host and exact target from supplied context, registries, skill roots, invocation names, metadata, and repository facts before asking anything. Record canonical host identity, canonical target identity, authority, current snapshot, optional Git identity, and delivery effects. Scan exact names, aliases, invocation tokens, overlapping responsibility, and near neighbours. A near neighbour is evidence to compare, never permission to repurpose.

An absent exact target means create, with an absent-target baseline and overlap map. An existing exact target means improve, with current behavior, failures, strengths, and preserved regressions. Ambiguity after facts are exhausted means asking one concise question and waiting. Never overwrite an existing target as creation. Never silently rename, replace, or absorb a near neighbour. Keep exactly one active target. Finish, explicitly abandon, or validly pause that target before activating another. A multi-target request waives no target's gates.

### 2. Capture the mode-specific baseline

Capture the baseline before any candidate edit. For create mode, prove the exact target is absent, retain the overlap map, record host conventions, and identify regressions that nearby skills must preserve. For improve mode, snapshot the exact target and record current behavior, observed failures, strengths, context cost, tool and permission behavior, and preserved regressions. Bind the baseline to the target snapshot and run revision. A target change invalidates the baseline and every dependent artifact. Payload schemas live in the artifact contracts reference.

### 3. Run three blind research lanes

Launch exactly three bounded, blind, independent web research lanes. Assign GPT-6-Luna at max reasoning to each lane:

1. Domain techniques relevant to the target's job.
2. Agent-skill design, instruction, interaction, and tooling practices.
3. Evaluation methods, adversarial cases, and failure modes.

Bound each lane's question, source scope, and evidence budget before launch. Researchers return evidence cards from direct primary or authoritative sources with applicability and limitations. They never edit targets, never draft candidates, and never see candidate direction, hidden cases, desired scores, or sibling output. A lane that cannot run or lacks direct evidence stops the run before design convergence. Lane and card schemas live in the artifact contracts reference.

### 4. Sieve every evidence card

Normalize and deduplicate the research pack. Classify every card as `adopt`, `experiment`, or `reject` with a reason, conflicts, and retained dissent. Pass only adopted and experimental evidence into design. Never turn majority agreement into evidence.

### 5. Explore and challenge coherent designs

Collaborate with the user on materially different, coherent designs covering triggering, behavior, tools, permissions, delegation, outputs, recovery, composition, and context cost. Challenge viable designs with near neighbours, hostile inputs, changed goals, missing evidence, and authority boundaries. Resolve facts autonomously and ask only material product decisions that facts cannot settle, one question per turn. Record decisions and rejected alternatives. The user owns material product decisions.

### 6. Produce one concrete skill contract

Write one contract with purpose, success signal, triggers, non-triggers, inputs and preconditions, ordered behavior and decision rules, allowed and forbidden actions, tools, permissions and authority, delegation boundaries, outputs and consumers, failures and blocked behavior, changed-goal behavior, stopping conditions, required resources, and explicit non-goals. Resolve every conflict with host rules and preserved regressions. Bind the contract to the baseline, retained evidence, design record, and target snapshot.

### 7. Obtain user confirmation

Present the current contract and wait for explicit user confirmation. Record the confirmation against the exact contract digest and target identity. The agent may propose readiness but cannot confirm, cannot end the session to force confirmation, and cannot treat silence, urgency, seniority, prior broad approval, or an instruction to skip questions as confirmation. A material contract change invalidates confirmation and all downstream artifacts. Return to this stage after correcting the contract.

### 8. Freeze acceptance before candidate work

Create visible development cases, frozen validation cases, and hidden release cases covering positive, negative, near-neighbour, ambiguous, adversarial, recovery, permission, output, and multi-turn behavior as applicable. Bind the pack to the confirmed contract digest and unchanged target snapshot. Keep hidden expectations and oracles away from implementers and trial agents. Prove new capability cases expose the baseline gap and preserved regressions stay valid. Freeze before any candidate edit. Reject candidate entry unless the helper confirms the contract digest, its confirmation record, the frozen pack digest, and an unchanged target snapshot.

### 9. Build one isolated candidate

Create one isolated candidate in a disposable copy or isolated worktree. Never test by editing a production skill. Preserve unrelated work. Give exactly one implementer write access to the candidate with only its accepted brief, owned paths, visible cases, and necessary target artifacts. Keep production targets, sibling skills, hidden cases, unretained research conclusions, and delivery surfaces outside that write authority. Record revision, digest, diff, and checks.

### 10. Run fresh-context trials

Run create or improve trials per the current mode in disposable targets with a fresh agent context for every case. Capture raw prompts, outputs, tool events, before and after target manifests, and trial receipts bound to candidate, loaded skill, request, context identity, target snapshot, filesystem result, and verdict. Trial agents receive only the candidate skill, realistic request, visible case material, and necessary target artifacts. Leakage, wrong-skill loading, unauthorized writes, missing trace evidence, and false success fail the trial.

### 11. Obtain independent review before scoring

Give an independent read-only target reviewer a compact locator handoff under the final Review context contract, referencing confirmed contract, host rules, evaluation pack, original raw trial evidence, preserved regressions, artifact manifest, and evaluation rubric by path and digest without restating them. Verify independence, revision freshness, read-only behavior, artifact access, and absence of a desired verdict. Require evidence, impact, correction, severity, affected target criteria, and `ready` or `not ready`. An invalid review blocks advancement and cannot be scored. A valid `not ready` review or valid High or Medium findings mark mapped criteria failing and enter the lowest-category repair loop. No material finding coexists with a score of 10.

### 12. Score and repair the lowest category

Evaluate the builder-run conformance gates in the evaluation rubric as a separate pass or fail ledger proving the run followed its research, confirmation, acceptance-first, isolation, evidence, state, authority, and cleanup rules. A failed gate blocks release and returns the run to its earliest affected stage, but never becomes a target category criterion.

Score the exact candidate target against the confirmed target contract, host rules, accepted neighbouring boundaries, and frozen evaluation pack. Never score the candidate against `$skill-builder` identity, invocation policy, package layout, or workflow. Score triggering, scope discipline, workflow quality, collaboration, output contract, safety, recovery, composability, context efficiency, and testability with integers from 0 to 10. Every category must independently reach 10. Never average, waive, round, or replace evidence with confidence.

Repair the lowest score first, breaking ties by greater safety or correctness impact, then greater dependency impact, then earlier category position. Identify the evidence preventing 10, strengthen or add the proving case, issue one bounded repair brief, edit only the isolated candidate, rerun affected and preserved cases, obtain independent review, and rescore the exact new revision until every category is 10. Missing evidence, a flaky target gate, a valid negative review, conflicting requirements, or an unresolved user decision keeps the affected category below 10. Invalid review evidence blocks advancement without a score.

### 13. Review, verify, and finalize one exact revision

After all ten target categories reach 10 with every builder-run gate passing, run a final independent target review against the same exact revision and validate it before use. An invalid final review blocks advancement without changing scores. A valid `not ready` review or valid High or Medium finding lowers mapped categories below 10 and returns them to repair.

After a valid `ready` final review, run independent verification against that exact revision, recording exact commands or operations, exit status, relevant output, target manifests, and revision. Any candidate change invalidates both final gates and returns affected categories to repair. Finalize through the state helper only when every gate passes, every category is 10, the final review is valid and `ready`, verification passes, every binding matches, and release evidence is retained. The finalization terminal stage retains the contract, confirmation, evaluation pack, candidate identity, conformance ledger, scorecard, review, verification, limitations, and release record.

Commit, install, push, publish, or otherwise deliver only with explicit user authority for that effect, recording any accepted installation or integration against the exact finalized revision. Cleanup is a separate terminal transition needing explicit user authority plus a recorded accepted installation or integration. It writes and validates a helper-owned parent-level tombstone bound to run identity, final transition digest, final run-manifest digest, and accepted delivery or installation record digest, then deletes only the helper-owned run directory. The tombstone survives. Cleanup never deletes a target skill, repository, outside candidate, tombstone, or unowned path. The release, manifest, and tombstone schemas live in the artifact contracts reference.

## Roles, tools, and permissions

- The main agent owns identity resolution, user collaboration, evidence synthesis, hidden-case custody, state transitions, and authority checks.
- Exactly three research agents use web access and return evidence only. They never edit.
- One candidate implementer writes only inside the isolated candidate. Trial agents write only inside disposable targets.
- Reviewers and verifiers stay independent and read-only to tracked source.
- No delegated agent confirms product intent, broadens scope, reveals hidden material, delivers externally, or cleans state.

## Changed goals and gate failures

A changed target identity, behavior, success, scope, delivery intent, or other material decision invalidates every dependent artifact through the helper and returns to the earliest affected stage. Preserve unrelated valid evidence only while its bindings still match.

Stop at the current gate while identity, authority, evidence, confirmation, isolation, state integrity, target stability, review readiness, scoring, or verification stays unresolved. Report the failed gate, retained evidence, invalidated artifacts, and the next decision or proof needed.

## Status and output contract

During work, report exact target, mode, active stage, queue position, latest valid evidence, invalidated evidence, next gate, and user-owned decisions. At finalization, report exact revision, builder-run conformance, ten target category scores, review validity and verdict, verification evidence, retained limitations, release-record locator, and authorized delivery result. A status report never advances the run. Only a validated hash-chained helper receipt advances it. The cleaned terminal state additionally needs its valid durable tombstone and deletion of only the helper-owned run directory.

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
