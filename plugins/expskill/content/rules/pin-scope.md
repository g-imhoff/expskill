# Pin scope

Single rule for pinning review and execution scope to an exact revision.

## Trigger

Use when scope names a diff, branch, commit, PR or MR, file set, subsystem, or named behavior.

## Procedure

1. Resolve every Git target and baseline to exact commit IDs before inspection. Pin dirty or non Git content by path plus content hash. Pin related mutable evidence by content and hash as it enters.
2. Read committed content from immutable Git blobs. Verify mutable content against pinned hashes before use.
3. Rebind scope and environment only from the frozen charter or graph state. Never trust a moving ref name.
4. Recheck identities before finishing. Compare current branch, HEAD, and dirty fingerprint against the pin.
5. On drift, stop. Inspect work you did not do. Decide if it is relevant to your scope or in conflict with your work. There is never a case with two agents or a human and an agent working the same job at the same time. Notify the user that concurrent work appeared, name the original pin and the new identities, state what changed, state relevance and conflict assessment, and wait for user direction. Do not silently rebind and continue. Do not erase history.
6. For read-only work, finish on the original evidence with disclosed old and new identities when the user says to proceed without rebind. State exactly what could not be reviewed and why when original content is gone.
7. For mutating work, proceed only after explicit user direction. Rebind scope and environment to the new head when the user confirms, rerun every check invalidated by the move, and require a full green scope on the final head before PASS. Retain prior evidence as history bound to its head.
8. Require a non-protected workflow or feature branch for any write, commit, or test side commit. Read-only work is exempt.

## Boundary

Do not pin by branch name alone. Do not finish mutating work on a stale head. Do not claim coverage of a new head from old evidence. Missing source content means disclose what is missing and why. Missing reviewer reply means fail closed. Missing runtime prerequisite means BLOCKED.

## Evidence

Report the original pin, the current identities, drift assessment with relevance and conflict verdict, user direction received, and the final head all verdicts bind to. Point at exact pins reviewed.
