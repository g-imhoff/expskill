# Evidence report

Single rule for where reports and evidence live. Home state is the root. One run folder per run. No shared mutable file across runs.

## Trigger

Use when saving a report, bundle, receipt, charter, ledger, or any retained proof artifact.

## Procedure
1. Root all retained state at home state. Use XDG state expskill when XDG_STATE_HOME is set, else use HOME slash dot expskill. Never write retained proof into the target repository except for approved owned paths like test owned code plus fixtures plus config, or setup owned record paths after confirmation.
2. Use one run folder per run. Shape is state root, then skills, then skill name, then repo hash, then run id. Example skill homes are dot expskill slash skills slash review, dot expskill slash skills slash test, dot expskill slash skills slash roast, dot expskill slash skills slash deep-research, dot expskill slash skills slash brainstorm. Keep raw evidence private to that run folder. Keep permissions strict. Never reuse a run folder for a new head.
3. Bind every claim to exact pin or head. Record repository, branch, exact head, environment, scope, plus bundle digest where a finalizer exists. Report evidence as current only for its bound head. Copy only the final user facing report to a stable latest link after verify passes. Keep the link plus the run folder plus the receipt digest together.
4. Use closed verdicts only. Review ranks BLOCKING, HIGH, MEDIUM, LOW with overall math. Test returns PASS, FAIL, BLOCKED, EXEMPT. Roast passes at 9 or higher in every category. Deep research closes saturated versus incomplete. Brainstorm closes with a six section Concept Brief or an explicit Incomplete concept.
5. Disclose drift with old plus new identities. State exactly what could not be reviewed and why when original content is gone. Keep routine green stages silent and raw logs out of the reply. Cite raw bundle plus receipt only through compact probe entries.

## Boundary

Do not store retained proof in the target checkout except for approved owned paths. Do not share one mutable report file across runs or repos. Do not claim coverage of a new head from old evidence. Do not publish secrets, credentials, or private context in mirrored reports.

## Evidence

Report the state root used, the run folder path, the repo hash plus head bound, the final report path plus latest link where present, verdicts with math, and limitations with retained risks.
