### 3. Run three blind research lanes

Launch exactly three bounded, blind, independent web research lanes. Assign GPT-6-Luna at max reasoning to each lane:

1. Domain techniques for the target's job.
2. Agent-skill design, instruction, interaction, and tooling practices.
3. Evaluation methods, adversarial cases, and failure modes.

Bound each lane's question, source scope, and evidence budget before launch, and give each lane only those. Cards cite direct primary or authoritative sources. A lane that cannot run or finds no direct evidence stops the run before design convergence.

### 4. Sieve every evidence card

Normalize and deduplicate the research pack, then classify every card as `adopt`, `experiment`, or `reject`. Pass only adopted and experimental evidence into design. Judge evidence by its source alone.

### 5. Explore and challenge coherent designs

Collaborate with the user on materially different, coherent designs covering triggering, behavior, tools, permissions, delegation, outputs, recovery, composition, and context cost. Challenge viable designs with near neighbours, hostile inputs, changed goals, missing evidence, and authority boundaries. Resolve facts autonomously and ask the user only the material product decisions that facts cannot settle, one question per turn. Record decisions and rejected alternatives.

### 6. Produce one concrete skill contract

Write one skill contract with its load policy and target hosts, resolving every conflict with host rules and preserved regressions.

### 7. Obtain user confirmation

Present the current contract, the planned description, and each host limitation its load policy meets, then wait for the user's explicit, unforced answer to that exact contract. Record each limitation in the confirmation's limitations. The confirmation binds the contract alone, leaving the description free to sharpen when selection cases fail. The main agent may propose readiness. Keep waiting through silence, urgency, seniority, prior broad approval, or instructions to skip questions. Return to this stage after correcting the contract.

### 8. Freeze acceptance before candidate work

Create visible development, frozen validation, and hidden release cases covering positive, negative, near-neighbour, ambiguous, adversarial, recovery, permission, output, and multi-turn behavior as applicable. Mark each case `selection` when it tests whether the host loads the candidate, as trigger, non-trigger, and near-neighbour requests do, and `loaded` when it tests behavior after loading. Give each selection case a target host the load-policy table runs selection trials on. Where no target host runs them, the static flag check stands in for selection cases. Prove new capability cases expose the baseline gap and preserved regressions stay valid. Freeze the evaluation pack, and enter candidate work only after the helper confirms the four candidate-entry bindings.

### 9. Build one isolated candidate

Write the implementer brief from the placement table. Build one candidate in a disposable copy or isolated worktree, edit only that candidate, and preserve unrelated work. Give the implementer only the brief and the isolated copy it edits. Write the candidate record bound to that brief.

### 10. Run fresh-context trials

Run every case in a disposable target with a fresh agent context. Run each selection case as the artifact contracts' selection trials describe, and each loaded case with the candidate preloaded, giving the trial agent only the candidate skill, the realistic request, visible case material, and the target artifacts it needs. Pass a run only when it shows the expected outcome, every write inside its disposable target, case material confined to its allowed context, and complete trace evidence. Write the trial pack.

### 11. Obtain independent review before scoring

Dispatch one target reviewer under the Review context contract, and use the review only when it is valid under the rubric's reviewer evidence contract.

### 12. Score and repair the lowest category

Record the conformance ledger against the rubric's gates BR1 to BR10. Score the exact candidate with the rubric, and repair with its lowest-score loop until every category is 10, writing a new implementer brief for each repaired candidate. An unresolved user decision keeps its affected category below 10.

### 13. Review, verify, and finalize one exact revision

Once every category is 10 and every builder-run gate passes, run a final target review of the same revision under stage 11's rules. After a valid `ready` final review, verify that revision, then finalize through the helper once every finalize condition in the rubric holds. Put the candidate's final description in the finalization report.

Commit, install, push, publish, or otherwise deliver only with explicit user authority for that effect, and record any accepted installation or integration against the finalized revision. Clean up through the helper only with explicit cleanup authority and a recorded accepted installation or integration.
