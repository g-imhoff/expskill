# Design to Plan handoff contract

A Design phase once ended with output the Plan join would not accept. The
baseline had drifted, the branch assumptions did not match, and a digest was
missing. Getting to implement took manual repair inside the repository. This
file exists so that never happens again. It states exactly what Design must
deliver and what Plan checks, and both helpers enforce it before anyone
touches implement.

## What I chose and why

I looked at two ways to fix this. One was a shared handoff folder created at
workflow start, holding the mutual expectations for both phases to validate
against. The other was a lighter declared contract with checks on both
sides and no new state.

I went with the contract. Both helpers already own private state roots with
their own locking. A third shared folder adds staleness, permission, and
sync failure modes without fixing the actual problem, which was mistyped or
missing receipt fields and unchecked branch assumptions. The receipt that
already travels from Design to Plan carries everything the join needs, as
long as both sides agree on its shape and check it early.

## The receipt

Design hands Plan one delivery receipt. It has exactly these fields, no
more and no fewer:

schema_version, operation, workflow_id, revision, lifecycle, identity,
state_digest, candidate_digest, candidate_inventory_digest,
review_evidence_digest, manifest_digest, evidence_digest, approval_digest,
dependency_digest, brief_digest, candidate_commit.

Its identity block has exactly these fields: repository, branch, worktree,
baseline, head, dirty_fingerprint, ui_contract_digest.

The rules are strict on purpose. operation is `deliver`. lifecycle is
`delivered`. Anything else stops at the join. Workflow ids are 32 lowercase
hex characters. Digests are 64. Commits are full SHAs. identity.head equals
candidate_commit. Workflow IDs, digests, and SHAs must be strings.
schema_version must be the integer `1`, and revision must be an integer of
at least `1`. Booleans and floats do not count as integers. Repository and
worktree names must be nonblank strings of at most 16,384 characters, and
branch names have a 244-character limit. None may contain NUL. If a field
is missing or mistyped, the error names it.

## What each digest means

brief_digest is the digest of the confirmed Design brief. No confirmed
brief, no digest, no join. candidate_commit is the checkpointed candidate
commit. Direct-mode Design never checkpoints a candidate, so its output is
never join-eligible. That is by design, not a gap. approval_digest covers
the approval set, manifest_digest covers the manifest. The join compares all
three digests plus the baseline, branch, and candidate against the receipt
byte for byte. Copy these values from the receipt. Retyping them caused the
original mismatch, and the new `record_design_join_from_delivery` helper
exists so the router never retypes them again.

## Branch and baseline rules

The delivery baseline must equal the Plan graph baseline exactly. The
delivery branch must be isolated, meaning it differs from the Plan target
branch. The candidate is exactly one commit above the baseline, and the
isolated branch tip must stay the candidate commit until integration. Once
the candidate becomes an ancestor of the target HEAD, accepted cleanup may
remove the isolated branch. If the branch moves after delivery, the join
fails and names the branch tip as the cause.

## Who checks what

Design checks first. `preflight_plan_join` in `design_state.py` takes the
workflow plus the Plan baseline and target branch, and returns every
problem it finds, each naming the exact field. Before delivery, `eligible`
is false and the CLI exits with status `1` because the lifecycle is still
active. Proceed to delivery only when that active lifecycle is the sole
remaining problem. Fix every other problem first. Missing or malformed
preflight output blocks delivery.

Use the `deliver` CLI stdout as the receipt, unchanged. After delivery, run
preflight again and require `eligible: true`, no problems, and CLI exit
status `0` before passing the receipt to Plan. Routed delivery refuses to finish
without a candidate checkpoint and a confirmed brief, because without those
two the receipt could never join.

Plan checks second. `_validate_design_delivery_receipt` in `plan_graph.py`
rejects malformed, undelivered, unapproved, or stale receipts with the exact
field in the message. `issue_design_join_receipt` still accepts hand-built
bindings for callers who need them, but new code should use
`record_design_join_from_delivery`, which derives every binding from the
receipt and removes the transcription step entirely.

The machine-readable version of this contract is
`plugins/expskill/content/scripts/design_plan_handoff.py`. Both helpers stay
dependency-free and load standalone, so they carry their own copies of
these field names. `tests/test_design_plan_handoff_contract.py` fails if
either copy drifts from the module.

## What still can go wrong

A malicious or buggy router can still hand Plan a receipt from a different
workflow. The join catches that through the binding comparison, but read
the error carefully before retrying. Clock skew or parallel delivery can
mark a good receipt stale if the graph revision moved underneath it. That
is correct behavior. Rebase the join onto the current revision instead of
editing the receipt. And if the Design branch was deleted before the
candidate merged into the target, the ancestry check fails. That means the
candidate never integrated, so do not work around it. Integrate first.

Keep the original delivery request until Plan accepts the receipt. If Design
stops after saving delivery but before returning stdout, retry `deliver` with
the identical request, including its original `expected_revision` and all
three inventories. Design revalidates the workspace and returns the same
receipt without rewriting state or incrementing the revision. Changed inputs
or a different revision still fail. Do not reconstruct the receipt by hand.

If Plan stops after saving the join but before acknowledging it, load the
current graph and compare its embedded Design delivery receipt with the
original. A matching receipt and `ready` state confirm success. Reapplying the
old operation raises a revision conflict and leaves that graph unchanged.

While saving an unintegrated join, Plan holds a Git verify transaction on the
Design branch. This locks the candidate ref without changing its value. A
branch that already moved blocks the join before any graph write; a competing
Git update cannot move it during the save. Plan releases the lock on success
or failure. Later operations still revalidate the live branch or integrated
ancestry.
