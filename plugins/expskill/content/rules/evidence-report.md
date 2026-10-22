# Evidence report

## Procedure
1. Root all retained state at `~/.expskill`, always. Write retained proof to home state; the only target-repository writes are approved owned paths like test owned code plus fixtures plus config, or setup owned record paths after confirmation.
2. Use one run folder per run: `~/.expskill/skills/<skill>/runs/<repo-hash>/<run-id>`. Worktrees live under `~/.expskill/worktrees`. Keep raw evidence private to that run folder. Keep permissions strict. Create a fresh run folder for each new head.
3. Bind every claim to exact pin or head. Record repository, branch, exact head, environment, scope, plus bundle digest where a finalizer exists. Report evidence as current only for its bound head. Copy only the final user facing report to a stable latest link after verify passes. Keep the link plus the run folder plus the receipt digest together.

## Evidence

Report the state root used, the run folder path, the repo hash plus head bound, the final report path plus latest link where present, and limitations with retained risks.
