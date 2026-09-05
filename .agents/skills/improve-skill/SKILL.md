---
name: improve-skill
description: Use only when explicitly asked to improve, redesign, or evaluate agent skills in this repository. Run the evidence-gated stack improvement program one target at a time and stay inactive for ordinary development work.
---

# Improve Skill

Improve an entire agent-skill stack through one controlled program while changing and certifying exactly one target skill at a time.

Keep this maintainer workflow separate from the product skill surface. Do not add it to the plugin being improved. Do not load a product phase merely to imitate it. Preserve the stack's accepted public boundaries until a reviewed contract explicitly changes them.

## Operating rules

- Treat the user as the product-decision owner and the coordinator as the evidence owner.
- Keep one active target. Complete one target before selecting another.
- Store working artifacts in a dedicated run directory outside production skill folders.
- Use disposable copies for behavioral trials. Never test by editing a production skill.
- When testing this workflow itself, enter **workflow-validation mode** and apply its substitutions below instead of the production queue and audit.
- Separate research, design, test definition, implementation, review, and verification roles.
- Preserve raw prompts, agent outputs, commands, revisions, and results needed to audit a claim.
- Treat an absent, stale, inconsistent, or unauditable artifact as a failed gate. Missing evidence fails closed.
- Stop the run when authority, a product decision, or reliable evidence is missing. Do not manufacture a passing score.

## Workflow-validation mode

Use this mode only to test `$improve-skill`, never to improve or certify the production stack. Its rules override the production-specific instructions in stages 1–13:

1. Create a unique temporary run directory outside the checkout. Copy one disposable fixture into it without symlinks, keep the checked-in fixture read-only, and snapshot the production skill surface before any trial.
2. In stage 1, build a fixture-only stack map, contract, and one-entry fixture-only queue bound to that copy. This queue substitutes for the production queue. Do not select or advance any of the seven production skills.
3. In stages 2–12, write only inside the temporary run directory. Treat every target, candidate, test, repair, review, and verification result as disposable workflow evidence.
4. In stage 13, replace the production-stack audit with a fixture-only terminal audit. Check the copied fixture, the complete evidence chain, sentinel files, and the unchanged production snapshot.
5. Emit only a `workflow-validation` record, not a release record. It cannot satisfy target completion, stack completion, or release, even when every fixture gate passes. Never call this stack completion.

## Artifact chain

Maintain these named artifacts for the program. They may be concise, but each must identify its target, revision, status, and evidence:

1. **stack map**: public skills, responsibilities, dependencies, and current routing.
2. **stack contract**: invariants shared by every skill and rules that may not drift silently.
3. **upgrade queue**: each target exactly once, with the router last.
4. **baseline report**: current behavior, existing strengths, failures, and preserved regressions.
5. **research pack**: independent evidence cards from every research lane.
6. **evidence sieve**: every card classified as `adopt`, `experiment`, or `reject`, with a reason.
7. **design record**: explored alternatives, tradeoffs, questions, answers, and decisions.
8. **skill contract**: accepted behavior and boundaries for the target.
9. **evaluation pack**: visible, frozen, and hidden cases plus their objective gates.
10. **candidate diff**: the bounded implementation associated with the current evidence.
11. **scorecard**: ten category scores on the current revision, never an average.
12. **review record**: independent findings, impacts, corrections, and readiness.
13. **verification record**: exact commands, revision, exits, and relevant outputs.
14. **release record**: accepted artifacts and evidence binding the completed target.

Do not advance on an artifact's existence alone. Check its content, provenance, target, and revision.

## 1. Establish the stack program

Perform this setup once for the stack, then reuse it for every target:

1. Inventory every product skill, its entrypoint, metadata, invocation policy, tools, outputs, neighbouring phases, and tests.
2. Record shared invariants in the stack contract. Include direct-invocation rules, side-effect boundaries, handoff schemas, public naming, installation surface, and router constraints.
3. Record the initial upgrade queue as:

   `brainstorm → plan → design → implement → grill-me → unslop → use-expand`

4. Change that order only when dependency evidence requires it. Preserve the rule to upgrade `use-expand` last because it must route against the final phase contracts.
5. Define the ten-category rubric and release commands before selecting a target.

If a proposed change alters a shared invariant, treat it as a stack-contract decision. Ask the user before applying it to any target.

## 2. Select one target skill

Select the first unfinished entry in the upgrade queue. Bind the run to the target path and current revision.

State:

- the target and its single job.
- why it is next.
- owned files.
- neighbouring skills that must not be absorbed.
- the shared regressions that must remain green.

Do not begin a second target, including research for it, while the current target remains unfinished.

## 3. Capture the baseline

Inspect the current target without changing it. Build the baseline report from:

- its `SKILL.md`, metadata, bundled resources, validators, and tests.
- at least five realistic should-use prompts.
- at least five should-not-use or near-neighbour prompts.
- ambiguous and multi-turn cases.
- current output, tool, permission, failure, and handoff behavior.
- line and context cost.
- known user complaints and preserved strengths.

Run existing deterministic checks. When safe, run the current skill on disposable inputs so later claims compare against real behavior rather than memory. Record current failures and current green regressions separately.

## 4. Run blind research

Spawn at least three independent research agents in parallel or in waves allowed by available concurrency. Use `gpt-5.6-luna` with max reasoning for every research lane unless the user explicitly changes that policy.

Assign distinct lanes:

1. **domain techniques**: current professional methods relevant to the target's job.
2. **agent-skill design**: instruction, interaction, tooling, and comparable skill practices.
3. **evaluation and failure modes**: adversarial cases, measurement, and common breakdowns.

Start with those three lanes. Enforce a per-target maximum of four research-agent sessions across at most two research waves, 24 evidence cards, and 6,000 words. The only optional fourth session answers one unresolved, decision-relevant question identified by the evidence sieve. Before exceeding any limit, ask exactly one user authorization question and stop.

Give researchers the target purpose and their bounded question. Do not reveal the candidate design, expected conclusion, hidden cases, desired scores, or another researcher's output. Tell them: return evidence only. Do not edit the repository. Do not draft the skill. Cite direct sources. State applicability and limitations.

Bound each lane to at most six of its strongest evidence cards and 1,500 words. Stop browsing when the lane has direct evidence for its bounded question or additional sources only repeat known claims. Do not start another research wave except for the single allowed follow-up above.

Require each evidence card to contain:

```text
claim
technique or practice
source and locator
applicable situation
limitation or failure mode
concrete experiment for this target
```

Reject unsourced generic advice. Verify time-sensitive claims on the web and prefer primary or official sources.

## 5. Sieve the evidence

Normalize and deduplicate the research pack without exposing future decisions to the researchers.

Classify each card:

- `adopt`: direct fit with adequate evidence.
- `experiment`: plausible but requires a target-specific trial.
- `reject`: irrelevant, duplicated, unsupported, ceremonial, or disproportionate.

Record the reason and conflicts for every classification. Preserve useful dissent instead of resolving it by majority vote. Pass only retained evidence into design.

## 6. Facilitate the design workshop

Use the retained evidence to facilitate, not dictate, the design. Keep divergence separate from convergence.

1. Restate the target outcome, non-goals, current failures, and preserved strengths.
2. Ask exactly one question per turn when a product decision is needed. Do not guess, batch unrelated questions, or advance a dependent stage before the answer.
3. Explore alternatives across triggering, inputs, responsibilities, interaction cadence, tools and permissions, outputs, failure recovery, stopping rules, handoffs, and context cost.
4. Generate distinct mechanisms before judging them. Change perspective when options merely rephrase the same design.
5. Challenge the strongest options with counterexamples, near-neighbour tasks, hostile inputs, and cross-stack effects.
6. Compare two to four coherent designs using explicit criteria and tradeoffs.
7. Record the user's decisions and rejected alternatives in the design record.

If the user declines or cannot resolve a behavior-changing decision, stop the run with `decision-needed`. Do not guess.

## 7. Lock the skill contract

Translate the accepted direction into a skill contract containing exactly these concerns:

- purpose and success signal.
- triggers and non-triggers.
- inputs and preconditions.
- ordered behavior and decision rules.
- allowed and forbidden actions.
- tool, permission, and delegation boundaries.
- output and handoff contract.
- failure, blocked, and changed-goal behavior.
- stopping conditions.
- required resources and explicit non-goals.

Resolve contradictions with the stack contract before proceeding. Obtain the user's decision for material product choices. Freeze the contract for evaluation work. Later changes return here.

## 8. Define acceptance before implementation

Create the evaluation pack before any implementation edit. Cover four layers:

1. **static**: package structure, metadata, resource, naming, and forbidden-dependency checks.
2. **triggering**: positive, negative, near-miss, ambiguous, and conflicting requests.
3. **behavior**: realistic single-turn and multi-turn trajectories, outputs, tools, writes, recovery, and stop behavior.
4. **integration**: direct invocation, neighbouring skills, handoffs, installation, and router constraints.

Divide cases into visible development cases, frozen validation cases, and hidden release cases. Keep hidden cases, expected scores, and planted defects outside candidate and trial contexts. Use deterministic assertions for exact invariants and calibrated human or independent review for open-ended quality.

Run the baseline against the pack. Demonstrate that new capability cases expose the intended gap and that preserved regressions stay green. If a test can pass through an unrelated failure or by reading its oracle, repair the test before implementation.

## 9. Implement one candidate

Give one writable implementer one accepted brief, one owned target, and the failing acceptance evidence. Work in an owned branch or disposable copy as appropriate.

Require the implementer to:

- preserve unrelated work and the public stack contract.
- change only the target, its target-specific resources and metadata, and tests or validators required by the accepted contract.
- prefer concise instructions over new resources until repeated evidence justifies them.
- return changed product decisions instead of silently expanding scope.
- record the candidate diff and exact local checks.

Do not let researchers implement, implementers self-certify, or one repair alter a different skill.

## 10. Run fresh-context trials

Test the candidate only in a disposable repository made from a copy or isolated worktree. Never test by editing a production skill. Run every model trial with `codex exec --ephemeral --ignore-user-config --json`. Retain the full invocation and JSON event stream. A run missing any isolation flag is invalid evidence.

For every trial:

- start a fresh agent context.
- pass only the candidate skill, realistic request, and necessary task artifacts.
- withhold the intended answer, diagnosis, hidden cases, prior transcript, sibling output, and scorecard.
- bind the raw prompt, loaded skill, target revision, tool events, outputs, and resulting filesystem state.
- repeat variable scenarios enough to distinguish reliable behavior from one lucky pass.

Write a trial receipt that binds the candidate digest, exact request and prompt digest, loaded-skill digest, fresh context or session identity, tool events, output digest, before-and-after filesystem manifest, and verdict. Reject a receipt when any binding is missing or does not match the retained raw evidence.

Cover typical, ambiguous, edge, adversarial, and multi-turn cases. Treat context leakage, wrong-skill loading, unauthorized writes, false success, and missing trace evidence as failures.

After the fresh trials, obtain an initial independent review of the current contract, candidate, evaluation pack, and raw evidence before initial scoring, even when no repair was needed. Apply the evidence-access, severity, verdict, and blocking rules from stage 12. Use its findings as scoring evidence. Do not advance a `not ready` candidate to verification.

## 11. Repair the lowest category

Score the current revision against these exact categories:

- `triggering`: intended requests select the skill and near neighbours do not.
- `scope discipline`: the skill performs one job without absorbing another phase.
- `workflow quality`: necessary decisions occur in a useful order without empty ceremony.
- `collaboration`: autonomy, questions, and user decisions are handled at the right boundary.
- `output contract`: outputs are complete, attributable, and consumable by the next actor.
- `safety`: tools, writes, delegation, approvals, and authority remain bounded.
- `recovery`: missing information, failures, changed goals, and blocked work are explicit.
- `composability`: direct use, neighbouring skills, handoffs, and routing remain compatible.
- `context efficiency`: every instruction and resource earns its context cost.
- `testability`: important behavior has independent, repeatable evidence.

Use integer scores from 0 to 10. A category is exactly 10 only when all predefined criteria for the current revision pass with fresh evidence, the reviewer reports `ready`, and no High or Medium finding remains. Do not average category scores, waive a category, round up, or use confidence as a substitute for evidence. All ten categories must independently reach 10 before release.

Apply this repair loop:

1. Select the lowest-scoring category.
2. For a tie-break, choose the category with greater safety or correctness impact, then greater dependency impact, then the earlier category in the list above.
3. Identify the evidence preventing 10 and write one bounded repair brief.
4. Add or strengthen the acceptance case that proves the gap.
5. Implement only that repair.
6. Rerun affected checks, fresh trials, and full preserved regressions.
7. Obtain independent review and rescore from current evidence.
8. Repeat until the category reaches 10, then select the new lowest category.

Stop rather than score 10 when evidence is missing, a gate is flaky, requirements conflict, or a user decision is unresolved.

## 12. Review, verify, and release one skill

After all ten categories reach 10:

1. Run a final same-revision review, even when no repair was needed. Give an independent read-only reviewer the accepted contract, candidate diff, tests, and raw evidence, not the desired verdict. Embed the required artifacts in its prompt or provide explicit read-only access to their exact paths. Verify access before review. An inaccessible artifact invalidates the review instead of counting as a product finding.
2. Require severity, evidence, impact, correction, and `ready` or `not ready` for every finding set.
3. Treat every High or Medium finding as release-blocking. Any `not ready` verdict blocks verification, release, and target completion. Lower each affected category below 10, return it to the repair loop, and rerun affected gates. Only a fresh independent review of the repaired revision can clear the block.
4. Give an independent verifier the exact accepted commands and current revision. Require exit status and relevant output without tracked-source edits or reliance on another agent's claims.
5. Run cross-stack regressions for direct invocation, neighbouring handoffs, public roster, installer, and router boundaries.
6. Build the review record, verification record, scorecard, and release record from the same revision.

Commit, push, merge, publish, or install only when the user has authorized that action. Mark the target complete only after every gate passes. Then select the next queued skill.

## 13. Audit the completed stack

After every target is complete, audit the stack as a system:

- each product skill works through direct invocation.
- each phase stays independent and performs one job.
- shared handoffs and evidence remain compatible.
- the public plugin contains exactly its accepted skills.
- the router selects and opens exactly one next phase.
- no phase silently opens the whole pipeline.
- complete development journeys preserve acceptance, review, verification, and integration gates.
- documentation, metadata, installer, validator, and tests describe the same product.

Run the full release commands from a clean checkout. Preserve the final stack evidence and unresolved limitations. Do not call the program complete when any target, category, hidden case, review, verification command, or shared invariant is unresolved.

## Status updates

During a long run, report the active target, active stage, lowest category, latest evidence, and next gate. Do not claim progress merely because agents ran or files changed.

When pausing for the user, ask only the blocking product question and preserve the current artifact chain. When resuming, rebind evidence to the current revision before continuing.
