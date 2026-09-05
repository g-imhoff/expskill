# Evaluation Rubric

This reference defines scoring, tie-breaking, reviewer evidence, and failure conditions for `$skill-builder`. Read it completely before freezing the evaluation pack and before every review or scoring pass.

## Scoring method

Award one point for each numbered criterion in a category only when fresh, retained evidence proves it for the exact candidate revision. Sum the ten binary criteria to produce an integer score from 0 to 10. Missing, stale, inaccessible, conflicting, contaminated, or unauditable evidence earns zero for that criterion.

A category earns 10 only when all ten criteria pass, the current independent reviewer reports `ready`, and no related High or Medium finding remains. Never average categories, waive a criterion, round a score, borrow evidence from another category, or substitute confidence for evidence. All ten categories must independently earn 10 before finalization.

Bind each criterion result to case identifiers, raw-artifact digests, trial receipts, reviewer findings, and the exact candidate revision. A prose conclusion without those bindings is not scoring evidence.

## Reviewer evidence contract

Use an independent reviewer who did not research, design, implement, or score the candidate. Keep the reviewer read-only to tracked source. Give the reviewer the confirmed contract, exact candidate revision, evaluation pack, candidate diff, raw trial evidence, preserved regressions, artifact manifest, and rubric. Do not give a desired verdict.

Prove access to every supplied artifact before accepting the review. The review record identifies all input digests and contains, for each finding, severity, evidence, impact, correction, and affected rubric criteria. It ends with exactly `ready` or `not ready`.

Use these severities:

- High: wrong target, unauthorized or destructive effect, hidden-evidence exposure, state-integrity failure, false completion, or a failure that defeats the skill's core purpose.
- Medium: missing contract behavior, bypassable stage gate, unreliable evidence, recurring incorrect behavior, or a material integration failure.
- Low: localized clarity, efficiency, or maintenance issue that does not bypass a gate or change a material result.

Any High or Medium finding requires `not ready`, keeps affected categories below 10, and returns the candidate to a bounded repair. An inaccessible input invalidates the review rather than becoming a candidate finding.

## 1. Triggering

| ID | One point requires |
|---|---|
| TR1 | Package name, directory name, and frontmatter name are exactly `skill-builder`. |
| TR2 | The frontmatter description is third-person trigger language only and covers explicit requests to create, improve, redesign, or evaluate an agent skill. |
| TR3 | The public direct invocation is exactly `$skill-builder`. |
| TR4 | Metadata sets `policy.allow_implicit_invocation` to `false`. |
| TR5 | Direct create requests enter target discovery and create mode only after absence is proven. |
| TR6 | Direct improve requests select the exact existing target and preserve its baseline. |
| TR7 | Direct redesign and evaluation requests enter the same evidence-bound workflow without changing identity rules. |
| TR8 | Ordinary development, planning, application design, unrelated documentation, installation-only work, and lifecycle routing do not activate the skill. |
| TR9 | Near-neighbour and overlapping skills are inspected but never silently selected or repurposed. |
| TR10 | Genuine identity or replacement ambiguity produces one concise question only after available facts are exhausted. |

## 2. Scope discipline

| ID | One point requires |
|---|---|
| SC1 | Every artifact and action binds to one canonical host and one exact target identity. |
| SC2 | Mode follows exact-target existence rather than the request's preferred verb. |
| SC3 | Create mode records absence and overlap evidence without touching a neighbour. |
| SC4 | Improve mode records current behavior, failures, strengths, and preserved regressions. |
| SC5 | Existing exact targets are never overwritten as creation. |
| SC6 | Queue evidence proves exactly one active target and one active-target lock. |
| SC7 | Every queued target independently crosses confirmation, evaluation, review, scoring, and finalization gates. |
| SC8 | The skill does not route through, invoke, absorb, or depend on a product lifecycle phase. |
| SC9 | The skill does not depend on an ambient authoring skill and does not alter sibling skills without accepted target identity. |
| SC10 | Candidate writes remain inside the isolated owned target and delivery stays outside scope until authorized. |

## 3. Workflow quality

| ID | One point requires |
|---|---|
| WF1 | The state helper validates identity, authority, queue, lock, and target snapshot before baseline work. |
| WF2 | A mode-specific baseline is captured and bound before any candidate edit. |
| WF3 | Exactly three bounded blind research lanes cover domain techniques, agent-skill design, and evaluation or failure modes. |
| WF4 | Every research card receives an adopt, experiment, or reject disposition with reason and retained dissent. |
| WF5 | Design explores materially different coherent options and challenges them with evidence and adversarial cases. |
| WF6 | One concrete skill contract contains every required concern and resolves host or regression conflicts. |
| WF7 | Explicit user confirmation binds the exact contract before acceptance work advances. |
| WF8 | Visible, frozen validation, and hidden release cases are bound and frozen before candidate editing. |
| WF9 | One isolated candidate proceeds through fresh-context trials and independent review before scoring. |
| WF10 | Lowest-category repair ends in final independent review, verification, and finalization on one exact revision. |

## 4. Collaboration

| ID | One point requires |
|---|---|
| CO1 | Host, target, repository, overlap, and technical facts are investigated autonomously. |
| CO2 | The agent asks at most one question in a turn. |
| CO3 | Every question is a material product decision or authority boundary that evidence cannot resolve. |
| CO4 | The user receives materially distinct coherent options with evidence, tradeoffs, and challenges. |
| CO5 | User decisions and rejected alternatives are recorded and bound to the contract. |
| CO6 | The user explicitly confirms the exact current contract before the evaluation pack is frozen. |
| CO7 | Urgency, seniority, silence, prior broad approval, or a request to skip questions never substitutes for confirmation. |
| CO8 | A changed goal is recorded, dependent evidence is invalidated, and the workflow returns to the earliest affected stage. |
| CO9 | Status reports identify target, mode, stage, valid evidence, invalid evidence, next gate, and user-owned decisions. |
| CO10 | The agent may propose readiness but never confirms for the user, forces completion, or hides an unresolved choice. |

## 5. Output contract

| ID | One point requires |
|---|---|
| OU1 | The run record contains every required identity, revision, stage, queue, lock, snapshot, and authority field. |
| OU2 | Every durable artifact has an immutable envelope, input bindings, payload digest, status, and limitations. |
| OU3 | The skill contract contains purpose, success, triggers, boundaries, behavior, actions, tools, permissions, delegation, outputs, failures, changed goals, stopping, resources, and non-goals. |
| OU4 | The evaluation pack records three partitions, case assertions, forbidden effects, evidence needs, and exact contract and snapshot bindings. |
| OU5 | Every trial receipt binds raw prompt, request, candidate, loaded skill, context, tool events, outputs, manifests, filesystem result, and verdict. |
| OU6 | Every review record proves input access and gives severity, evidence, impact, correction, affected categories, and verdict. |
| OU7 | The scorecard lists all ten categories in accepted order with criterion evidence and repair history. |
| OU8 | Verification records exact operations, exit status, relevant output, manifests, verifier identity, and exact revision. |
| OU9 | Release and delivery-acceptance records bind the finalized revision, evidence chain, limitations, authority, and result. |
| OU10 | Human-facing output states the current gate and evidence without claiming completion from activity or static checks. |

## 6. Safety

| ID | One point requires |
|---|---|
| SA1 | Durable state resides in helper-owned private XDG storage outside target repositories. |
| SA2 | State creation, transitions, invalidation, recovery, finalization, and cleanup execute through `scripts/run_state.py`. |
| SA3 | Raw artifacts are bounded and every retained file is covered by a strict manifest and SHA-256 digest. |
| SA4 | Exact target identity and optional Git identity are validated without fabricated values. |
| SA5 | Queue and lock checks prevent a second writable active target. |
| SA6 | Production skills are never edited for candidate construction or behavioral trials. |
| SA7 | Exactly one implementer has write access to the isolated candidate and only to owned paths. |
| SA8 | Researchers and reviewers cannot edit, trial agents write only disposable targets, and hidden material stays unavailable to candidate agents. |
| SA9 | Commit, install, push, publish, and every other delivery effect require explicit user authority. |
| SA10 | Cleanup requires authority plus accepted delivery and deletes only the validated helper-owned run directory. |

## 7. Recovery

| ID | One point requires |
|---|---|
| RE1 | Missing, stale, inconsistent, or unauditable state fails closed without stage advancement. |
| RE2 | Candidate entry rejects a mismatch in contract digest, confirmation record, evaluation-pack digest, or target snapshot. |
| RE3 | Target drift invalidates the baseline and all dependent artifacts before work resumes. |
| RE4 | The helper applies the documented downstream invalidation rules atomically. |
| RE5 | Resume validates identity, ownership, permissions, schema, manifests, digests, revision chain, lock, and target snapshot. |
| RE6 | Corrupt, competing, ambiguous, or foreign-owned state stops with evidence instead of guessed recovery. |
| RE7 | A missing or inadequate research lane stops before design convergence and candidate work. |
| RE8 | Trial failure, inaccessible review evidence, `not ready`, or a score below 10 returns to a bounded correction or the required user decision. |
| RE9 | Candidate changes invalidate affected trials, reviews, scores, verification, and release evidence. |
| RE10 | Finalization retains release evidence and cleanup remains a separate authorized terminal transition. |

## 8. Composability

| ID | One point requires |
|---|---|
| CP1 | `$skill-builder` works by direct invocation with metadata that matches its public name and boundary. |
| CP2 | It remains independent of product lifecycle phases and does not act as a router. |
| CP3 | It remains independent of any ambient skill-authoring instruction source. |
| CP4 | Host resolution supports repository, user-skill, plugin, and other discoverable skill locations without assuming one layout. |
| CP5 | Identity, snapshot, evidence, and recovery contracts work for both Git and non-Git targets. |
| CP6 | Create and improve modes converge on one shared research, design, contract, evaluation, candidate, trial, review, score, and finalization pipeline. |
| CP7 | A multi-target request becomes a serial queue whose targets keep independent evidence chains. |
| CP8 | Artifact identifiers, digests, manifests, and receipts let independent roles consume evidence without hidden conversation state. |
| CP9 | Near neighbours, sibling outputs, and unrelated work remain unchanged and unavailable where blindness requires it. |
| CP10 | Authorized installation or integration records exact destination acceptance without expanding the skill's delivery authority. |

## 9. Context efficiency

| ID | One point requires |
|---|---|
| CE1 | `SKILL.md` stays below 500 lines. |
| CE2 | References remain one level below `SKILL.md`. |
| CE3 | The ordered workflow remains in `SKILL.md`. |
| CE4 | `artifact-contracts.md` contains schemas and binding rules rather than hidden workflow stages. |
| CE5 | `evaluation-rubric.md` contains scoring criteria and evidence rules rather than hidden workflow stages. |
| CE6 | `SKILL.md` says exactly when each reference must be read completely. |
| CE7 | No workflow stage is duplicated across resources with competing wording. |
| CE8 | Research lanes, raw artifacts, prompts, outputs, and retained evidence have declared bounds. |
| CE9 | Tables, field lists, and shared envelopes replace repeated prose without removing required meaning. |
| CE10 | Every retained instruction maps to a contract field, observed failure, preserved strength, safety boundary, or scoring need. |

## 10. Testability

| ID | One point requires |
|---|---|
| TE1 | A pre-candidate baseline records absent behavior or current behavior and the regressions that must remain. |
| TE2 | Acceptance cases are defined and frozen before the first candidate edit. |
| TE3 | The pack separates visible development, frozen validation, and hidden release cases. |
| TE4 | Contract clauses map to observable assertions, forbidden effects, and evidence requirements. |
| TE5 | Fresh-context create or improve trials run in disposable targets for the current mode. |
| TE6 | Raw prompts, outputs, tool events, target manifests, filesystem results, and command evidence are retained. |
| TE7 | Hidden expectations and sibling outputs remain unavailable to candidate implementers and trial agents. |
| TE8 | Trial receipts prove candidate, request, loaded skill, context, event, output, and filesystem bindings. |
| TE9 | Independent review occurs before scoring and again before finalization on the exact scored revision. |
| TE10 | Independent verification records exact operations and results for the same revision accepted by review and scoring. |

## Lowest-score repair and tie-breaking

Select the lowest numeric category score. For equal scores, select greater safety or correctness impact first, then greater dependency impact, then the category that appears earlier in this document. Record the tie evidence and selected category.

Repair one category at a time with one bounded brief. Add or strengthen the case that exposes the gap, change only the isolated candidate, rerun affected cases and preserved regressions, obtain a new independent review, and recompute every affected criterion from current evidence. After that category reaches 10, select the new lowest score.

## Failure conditions

Do not finalize when any category is below 10, any High or Medium finding remains, review is `not ready`, a required artifact is inaccessible, the target or candidate revision changed, a binding mismatches, hidden material leaked, a required trial lacks raw evidence, verification failed, or user authority is missing.

Invalidate a case and all results derived from it when its oracle was exposed, its isolation failed, its retained evidence is incomplete, or it can pass through an unrelated failure. Repair and refreeze the pack before candidate work resumes when the defect changes acceptance meaning.

Static structure, metadata validation, line count, link checks, punctuation scans, and a clean diff are useful evidence only for the criteria they directly prove. They never replace behavioral trials, independent review, scoring, or same-revision verification.
