# Artifact Contracts

This reference defines the durable schemas and binding rules for one `$skill-builder` run. Read it completely at run start and before resuming persisted work. Treat every requirement as normative.

## Storage boundary

Store live run state under the private XDG root selected by `scripts/run_state.py`, outside every target repository. Let the helper create and own one run directory. Never direct the helper at a target skill, repository root, user home, or unowned path as its run directory. Keep raw artifacts bounded and record every retained file in a strict manifest with a digest. Reject symlinks, escaping paths, unmanifested raw files, unsafe permissions, ambiguous ownership, and unsupported schemas. Support Git and non-Git targets. A missing Git identity is valid only for a recorded non-Git target. Never invent repository, branch, or commit values.

## Durable source of truth

Use immutable hash-chained transition receipts as the append-only source of truth for every live run. Store each receipt as a new helper-owned file. Never rewrite an accepted receipt or remove one while the live run exists. Treat any current-state file, artifact index, stage pointer, or queue summary as a replaceable derived index rebuilt from the validated receipt chain and immutable artifacts. A derived index may accelerate discovery but never proves stage, status, recovery, or cleanup.

### Transition receipt schema

Every transition receipt contains exactly these fields:

| Field | Required content |
|---|---|
| `schema_version` | A helper-supported receipt schema identifier. |
| `workflow_id` | The opaque run identifier. |
| `target_identity` | The exact canonical target identity. |
| `sequence` | A zero-based integer that increases by exactly one. |
| `prior_receipt_digest` | `null` for sequence zero, otherwise the exact prior canonical receipt digest. |
| `event` | The accepted transition event. |
| `source_stage` | `null` for sequence zero, otherwise the stage derived from the prior receipt. |
| `destination_stage` | The stage established by this transition. |
| `relevant_artifact_digests` | Deterministically ordered identifiers and digests accepted, superseded, invalidated, or consumed by the event. |
| `target_snapshot_digest` | Exact target snapshot used to authorize the transition. |
| `authority_event_digest` | User-authority event digest when the transition requires authority, otherwise `null`. |
| `created_at` | RFC 3339 UTC timestamp. |
| `receipt_digest` | Canonical digest of this receipt with this field excluded. |

Require one genesis receipt at sequence zero. For every later receipt, require contiguous sequence, exact prior digest, source stage equal to the prior destination stage, current workflow and target bindings, and an allowed source-to-destination event. A fork, gap, duplicate sequence, changed prior receipt, or unsupported transition breaks the chain and fails closed.

### Derived run index schema

Derive the current index from the chain with these fields:

| Field | Required content |
|---|---|
| `schema_version` | A helper-supported index schema identifier. |
| `workflow_id` | Exact run identifier from the chain. |
| `head_sequence` | Sequence of the validated chain head. |
| `head_transition_digest` | Canonical digest of the validated chain head. |
| `stage` | Destination stage of the validated chain head. |
| `host_identity` | Host kind, canonical identifier, locator, and discovery evidence. |
| `target_identity` | Requested identity, canonical identity, name, invocation token, and locator. |
| `mode` | Exactly `create` or `improve`, plus the evidence that selected it. |
| `authority` | Allowed reads, writes, delegation, candidate effects, and delivery effects. |
| `git_identity` | Presence flag and, when present, repository identity, branch, commit, and dirty-state digest. |
| `target_snapshot` | Existence flag, snapshot kind, manifest digest, content digest, and capture sequence. |
| `queue` | Ordered target identities and derived pending, active, paused, finalized, or abandoned state. |
| `active_target_lock` | One target identity, owner identity, acquisition time, and lock nonce. |
| `artifact_index` | Artifact identifier, type, path, digest, derived status, producing sequence, and dependency identifiers. |
| `created_at` | Stable creation timestamp from sequence zero. |
| `updated_at` | Timestamp of the validated chain head. |

For an absent target snapshot, derive `exists: false` with the searched identity scope, overlap-map digest, and absence-evidence digest. For an existing target snapshot, derive `exists: true` with a manifest of the exact target files and metadata. The queue may hold many requested targets but exactly one entry may be active. A lock mismatch or competing active entry blocks mutation.

## Canonical digest serialization

Digest raw files over their exact bytes without newline conversion. Represent every structured durable record with JSON-compatible objects, arrays, strings, integers, booleans, and null. Reject floating-point values, duplicate object keys, non-finite numbers, and invalid Unicode. For a structured record digest, copy the record and exclude only the top-level digest field being computed, preserve array order with lexicographically sorted keys at every object level, serialize as UTF-8 JSON with no byte-order mark, unescaped non-ASCII text, and compact separators `,` and `:`, then append exactly one LF byte after the closing JSON value without inheriting an input file's trailing newline count. Compute SHA-256 over those bytes and encode the result as 64 lowercase hexadecimal characters. A dependency-free Python implementation must produce the same bytes as `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"` after the required digest-field exclusion. Other runtimes must match those bytes exactly. Record each timestamp as an RFC 3339 UTC string and each digest with its algorithm fixed by the schema. A digest claim without retained bytes or this canonical encoding is invalid.

## Artifact envelope

Wrap every durable artifact in an immutable envelope with these fields: `artifact_id` with a unique identifier within the run, `artifact_type` with one accepted type from the artifact index, `workflow_id` and `target_identity` with the exact owning run and canonical target, `mode` with the current create or improve mode, `created_stage` and `created_sequence` with the producing stage and presenting transition sequence, `producer` with the main-agent or bounded delegated role identity, `created_at` with the creation timestamp, `input_bindings` with identifier and digest for every artifact or snapshot used, `payload_path` with the run-relative payload path, `payload_digest` with the SHA-256 digest of the exact payload bytes, `manifest_digest` with the SHA-256 digest of the raw-artifact manifest when applicable, `limitations` with known gaps, truncation, unavailable evidence, and their gate effect at creation, and `envelope_digest` with the canonical digest of the envelope excluding itself. Never rewrite an envelope or payload in place. Bind the envelope digest in the accepting transition receipt and derive acceptance, supersession, invalidation, and terminal retention from transition receipts rather than mutating the envelope.

## Raw-artifact manifests

For each raw-artifact collection, retain an immutable manifest with schema identifier, owning workflow and target identities, collection type, declared and observed item-count and byte bounds, one entry per retained file with run-relative path, media kind, byte count, SHA-256 digest, source role, and retention class, an overflow or truncation record, and a `manifest_digest` computed with canonical digest serialization. A manifest must exclude its own file, any digest sidecar, mutable current-state indexes, current pointers, locks, temporary files, and parent-level tombstones. Apply digest-field exclusion only while hashing the manifest record itself. Keep prompts, outputs, tool events, source extracts, target manifests, diffs, and command results as raw artifacts when they support a claim. A summary never substitutes for required raw evidence.

## Required artifact payloads

### Resolution record

Record host evidence, exact-target evidence, overlap and near-neighbour map, selected mode, identity ambiguity, authority, queue order, and target snapshot. Bind every later artifact to this record.

### Baseline report

For create mode, record the absent-target proof, overlap map, host conventions, and preserved neighbour regressions. For improve mode, record current behavior, failures, strengths, context cost, tool and permission behavior, and preserved regressions. Include exact prompts, outputs, checks, and snapshot bindings used to support the baseline.

### Research pack

Record exactly three lane identities, each lane's bounded question, GPT-6-Luna with max reasoning, source scope, evidence budget, start and end state, and limitations. Each evidence card holds a claim, technique or practice, direct source and locator, applicable situation, limitation or failure mode, concrete experiment for this target, and lane identity and raw-source digest.

### Evidence sieve

List every evidence-card identifier exactly once with `adopt`, `experiment`, or `reject`, a reason, detected conflicts, retained dissent, and design relevance. Record deduplication links without deleting the source cards.

### Design record

Record coherent alternatives, mechanisms, tradeoffs, challenges, evidence links, factual conclusions, user-owned questions, user decisions, rejected alternatives, and unresolved issues.

### Skill contract

Record purpose, success signal, triggers, non-triggers, inputs and preconditions, ordered behavior and decisions, allowed and forbidden actions, tools, permissions, delegation, outputs and consumers, failures, changed goals, stopping conditions, resources, and non-goals. Bind every statement that depends on evidence or a user decision to its source artifact.

### User-confirmation record

Record user identity as available to the host, confirmation timestamp, exact confirmation text or event digest, canonical target identity, contract artifact identifier, contract digest, target-snapshot digest, and accepted status. Only an explicit user event can create an accepted confirmation artifact.

### Evaluation pack

Record the confirmed contract digest, target-snapshot digest, rubric digest, frozen target scoring parameters, freeze timestamp, and three case partitions: visible development, frozen validation, and hidden release. Each case holds an identifier, partition, purpose, raw request digest, allowed context, setup manifest, observable assertions, forbidden effects, evidence requirements, and pass or fail rule. Keep hidden prompts, expectations, and oracles in separately manifested helper-owned paths unavailable to candidate implementers and trial agents.

### Candidate record

Record candidate identifier, isolated locator, base snapshot digest, resulting revision and digest, writable role, owned paths, diff digest, local-check evidence, and the four candidate-entry bindings.

### Trial pack

For each fresh-context case, record candidate digest, case and request digests, raw prompt digest, loaded-skill digest, fresh context identity, tool-event digest, output digest, before and after target-manifest digests, filesystem-result digest, and verdict and limitation. The pack records case coverage, repeated-case identity where needed, isolation evidence, leakage checks, and an aggregate manifest digest.

### Builder-run conformance ledger

Record each process gate identifier, pass or fail, evidence digests, affected stage, repair state, and release-blocking result. Keep this ledger separate from target category scores.

### Review record

Record reviewer identity, independence and read-only attestation, exact candidate revision, input artifacts and their digests, access-check evidence, freshness, contamination check, validity, findings, severity, evidence, impact, correction, affected target criteria, and `ready` or `not ready` when valid. An invalid review has no scoring verdict.

### Target scorecard

Record the exact ten target category names in accepted order with, for each category, the integer score, each binary target criterion result, frozen parameter identifiers, evidence identifiers, related valid-review findings, and repair history. Bind the scorecard to the rubric digest, candidate revision, evaluation-pack digest, and current valid review record.

### Verification record

Record verifier identity, independence and read-only attestation, exact candidate revision, exact commands or operations, start and end timestamps, exit status, relevant raw-output digests, before and after manifests, and conclusion.

### Release record

Record the finalized target and candidate identities, contract and confirmation digests, evaluation-pack digest, builder-run conformance ledger digest, target scorecard digest, final review and verification digests, retained limitations, release evidence manifest, authorized delivery scope, and finalization receipt digest.

### Delivery-acceptance record

Before cleanup, record an accepted installation or integration with action, destination identity, exact finalized revision, resulting destination digest, acceptance evidence, user-authority event digest, actor, and timestamp.

### Final run manifest

Immediately before cleanup, create an immutable manifest of every retained immutable file in the helper-owned run directory, applying the manifest self-exclusion and mutable-pointer exclusions above, and bind it to the validated transition-chain head and accepted delivery or installation record.

## Binding rules

Bind every artifact to the current workflow, canonical target, mode, accepting transition, and input digests. Bind Git targets to repository identity, branch, commit, and dirty-state digest. Bind non-Git targets to host identity, canonical locator, and target-snapshot digest. Allow entry to candidate stage only when the current skill-contract digest, the accepted user-confirmation record bound to that digest, the frozen evaluation-pack digest bound to that contract and confirmation, and a recomputed target snapshot equal to the bound snapshot all match current state. The helper must reject the transition when any binding is absent, stale, duplicated, substituted, or mismatched.

## Downstream invalidation

Record invalidation as a new transition receipt. Never mutate the invalidated artifact:

| Material change | Invalidate at least |
|---|---|
| Host or exact target identity | Mode, baseline, research, sieve, design, contract, confirmation, evaluation, candidate, trials, reviews, scores, verification, and release. |
| Mode | Baseline and every downstream artifact. |
| Target snapshot | Baseline and every downstream artifact. |
| Research or sieve conclusion | Dependent design decisions and every dependent artifact. |
| Design decision or skill contract | Confirmation and every downstream artifact. |
| User confirmation | Evaluation pack and every downstream artifact. |
| Evaluation pack, target parameters, or rubric | Candidate and every downstream artifact. |
| Candidate content or revision | Trials, reviews, scores, verification, and release. |
| Trial or review evidence | Dependent scores, verification, and release. |
| Verification input or result | Verification and release. |
| Delivery intent | Authority and delivery portions of the release record. |

Retain invalidated artifacts for audit within configured bounds and derive their invalidated status from the new receipt. Never restore them merely because text appears unchanged. Recompute and rebind the affected evidence.

## Fail-closed recovery

On resume, execute the helper to discover state by canonical host and target identity, acquire the one active-target lock, validate helper ownership and permissions, reject symlinks, parse supported schemas, and load the transition receipts in sequence order. Validate the genesis receipt, contiguous sequences, every prior receipt digest, canonical receipt digests, source and destination stages, artifact bindings, immutable payloads, manifests, target identities, authority, queue, lock, and recomputed target snapshot. Rebuild the derived index and compare it with any stored current-state file, replacing a stale derived index only from the validated chain. A missing or broken chain, fork, receipt gap, altered artifact, invalid identity, changed target, ambiguous generation, or foreign active writer fails closed. Never use a current-state file to bridge missing history, patch the chain manually, or infer a later stage. If a valid cleanup tombstone exists while its run directory remains, validate its authority and bindings before retrying deletion. If the run directory is absent, accept `cleaned` only from a valid helper-owned tombstone.

## Terminal stages and cleanup tombstone

`finalized` is the first terminal stage. Its transition receipt retains release evidence and permits no candidate mutation. Delivery may follow only within recorded user authority. `cleaned` is the second terminal stage. Before deleting anything, require explicit cleanup authority and a valid accepted delivery or installation record for the exact finalized revision, then validate the live receipt chain, artifacts, final run manifest, run ownership, and deletion target. First write an immutable parent-level cleanup tombstone outside the owned run directory but inside helper-owned XDG storage with tombstone schema identifier, workflow ID and canonical target identity, helper-owned run-directory identity, final transition receipt digest, final run-manifest digest, accepted delivery or installation record digest, cleanup-authority event digest, creation timestamp, and `tombstone_digest` computed with canonical digest serialization. Write the tombstone atomically, synchronize it and its parent directory, read it back, validate its canonical digest and all bindings, and only then delete the helper-owned run directory. If tombstone creation or validation fails, delete nothing. The tombstone survives run-directory deletion and remains helper-owned durable evidence. Cleanup may delete only the validated helper-owned run directory, never the tombstone, target skill, repository, external candidate, or any unowned path.
