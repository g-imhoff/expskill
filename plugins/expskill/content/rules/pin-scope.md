# Pin scope

## Procedure

1. Resolve every Git target and baseline to exact commit IDs before inspection. Pin dirty or non Git content by path plus content hash. Pin related mutable evidence by content and hash as it enters.
2. Read committed content from immutable Git blobs. Verify mutable content against pinned hashes before use.
3. Rebind scope and environment only from frozen state when the skill uses one, such as the test charter or the plan graph, else from explicit user direction naming the new pin. Trust exact pins over moving ref names.
4. Recheck identities before finishing. Compare current branch, HEAD, and dirty fingerprint against the pin.
5. On drift, stop. Inspect work you did not do. Decide if it is relevant to your scope or in conflict with your work. Run one agent per job at a time. Notify the user that concurrent work appeared, name the original pin and the new identities, state what changed, state relevance and conflict assessment, and wait for user direction. Rebind only on explicit user direction, preserving history.
6. For read-only work, finish on the original evidence with disclosed old and new identities when the user says to proceed without rebind. State exactly what could not be reviewed and why when original content is gone.
7. For mutating work, proceed only after explicit user direction. Rebind scope and environment to the new head when the user confirms, rerun every check invalidated by the move, and require a full green scope on the final head before PASS. Retain prior evidence as history bound to its head.
8. Require a non-protected workflow or feature branch for any write, commit, or test side commit. Read-only work is exempt.
