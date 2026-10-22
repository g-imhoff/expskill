# Evidence report

## Procedure
1. Root all retained state at `~/.expskill`, always. Write retained proof to home state; the only target-repository writes are approved owned paths like test owned code plus fixtures plus config, or setup owned record paths after confirmation.
2. Use one run folder per run: `~/.expskill/skills/<skill>/runs/<repo-hash>/<run-id>`. Worktrees live under `~/.expskill/worktrees`. Keep raw evidence private to that run folder. Keep permissions strict. Create a fresh run folder for each new head.
3. Bind every claim to exact pin or head. Record repository, branch, exact head, environment, scope, plus bundle digest where a finalizer exists. Report evidence as current only for its bound head. Copy only the final user facing report to a stable latest link after verify passes. Keep the link plus the run folder plus the receipt digest together.
4. Use closed verdicts only. Review ranks BLOCKING, HIGH, MEDIUM, LOW with overall math. Test returns PASS, FAIL, BLOCKED, EXEMPT. Roast passes at 9 or higher in every category. Deep research closes saturated versus incomplete. Brainstorm closes with a six section Concept Brief or an explicit Incomplete concept.
5. Disclose drift with old plus new identities. State exactly what could not be reviewed and why when original content is gone. Keep routine green stages silent and raw logs out of the reply. Cite raw bundle plus receipt only through compact probe entries.

## Boundary

Store retained proof in home state; approved owned paths are the only target-checkout writes. Give each run and repo its own report file. Tie every coverage claim to the head its evidence ran on. Keep secrets, credentials, and private context out of mirrored reports.

## Migration

Prior XDG live state (`$XDG_STATE_HOME/expskill`, `~/.local/state/expskill`), repo `.test-evidence/`, and review `tmp/reports` are retired and not read. They are orphaned on upgrade with no auto-migration. Remove them by hand after confirming the new `~/.expskill` runs are green. In-flight runs started before the move must restart.

## Evidence

Report the state root used, the run folder path, the repo hash plus head bound, the final report path plus latest link where present, verdicts with math, and limitations with retained risks.
