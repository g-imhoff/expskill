# Artifact Contracts

This reference defines the durable schemas and binding rules for one `$skill-builder` run. Read it completely at run start and before resuming persisted work. Treat every requirement as normative.

## Storage boundary

Store state under the private XDG root selected by `scripts/run_state.py`, outside every target repository. Let the helper create and own one run directory. Never direct the helper at a target skill, repository root, user home, or unowned path as its run directory.

Keep raw artifacts bounded. Record every retained file in a strict manifest with a digest. Reject symlinks, paths that escape the run directory, unmanifested files, unsafe permissions, ambiguous ownership, and unsupported schemas.

Support Git and non-Git targets. A missing Git identity is valid only when the target is recorded as non-Git. Never invent repository, branch, or commit values.

## Run record schema

Maintain one current run record with these fields:

| Field | Required content |
|---|---|
| `schema_version` | A helper-supported schema identifier. |
| `workflow_id` | An opaque unique run identifier. |
| `state_revision` | A monotonic integer changed by helper transitions only. |
| `stage` | One allowed workflow stage or terminal stage. |
| `host_identity` | Host kind, canonical identifier, locator, and discovery evidence. |
| `target_identity` | Requested identity, canonical identity, name, invocation token, and locator. |
| `mode` | Exactly `create` or `improve`, plus the evidence that selected it. |
| `authority` | Allowed reads, writes, delegation, candidate effects, and delivery effects. |
| `git_identity` | Presence flag and, when present, repository identity, branch, commit, and dirty-state digest. |
| `target_snapshot` | Existence flag, snapshot kind, manifest digest, content digest, and capture revision. |
| `queue` | Ordered target identities and each target's pending, active, paused, finalized, or abandoned state. |
| `active_target_lock` | One target identity, owner identity, acquisition time, and lock nonce. |
| `artifact_index` | Artifact identifier, type, path, digest, status, revision, and dependency identifiers. |
| `created_at` | Stable creation timestamp. |
| `updated_at` | Timestamp of the latest validated helper transition. |

For an absent target snapshot, record `exists: false`, the searched identity scope, overlap-map digest, and absence-evidence digest. For an existing target snapshot, record `exists: true` and a manifest of the exact target files and metadata.

The queue may contain many requested targets but exactly one entry may be active. A lock mismatch or competing active entry blocks mutation.

## Artifact envelope

Wrap every durable artifact in an immutable envelope:

| Field | Required content |
|---|---|
| `artifact_id` | Unique identifier within the run. |
| `artifact_type` | One accepted type from the artifact index. |
| `workflow_id` | Exact owning run identifier. |
| `target_identity` | Exact canonical target identity. |
| `mode` | Current create or improve mode. |
| `stage` | Stage that produced the artifact. |
| `state_revision` | Run revision that accepted it. |
| `producer` | Main-agent or bounded delegated role identity. |
| `created_at` | Creation timestamp. |
| `status` | `current`, `superseded`, `invalidated`, or `terminal`. |
| `input_bindings` | Identifier and digest for every artifact or snapshot used. |
| `payload_path` | Run-relative path to the payload. |
| `payload_digest` | SHA-256 digest of the exact payload bytes. |
| `manifest_digest` | SHA-256 digest of the raw-artifact manifest, when applicable. |
| `limitations` | Known gaps, truncation, unavailable evidence, and their gate effect. |

Never rewrite an accepted artifact in place. Create a new envelope, mark the prior artifact superseded or invalidated, and advance the state revision atomically.

## Raw-artifact manifest

For each raw-artifact collection, retain a manifest containing:

- manifest schema identifier
- owning workflow and target identities
- collection type
- declared item-count and byte bounds
- observed item count and byte count
- one entry per retained file with run-relative path, media kind, byte count, SHA-256 digest, source role, and retention class
- overflow or truncation record
- digest of the canonical manifest bytes

Keep prompts, outputs, tool events, source extracts, target manifests, diffs, and command results as raw artifacts when they support a claim. A summary never substitutes for required raw evidence.

## Required artifact payloads

### Resolution record

Record host evidence, exact-target evidence, overlap and near-neighbour map, selected mode, identity ambiguity, authority, queue order, and target snapshot. Bind every later artifact to this record.

### Baseline report

For create mode, record the absent-target proof, overlap map, host conventions, and preserved neighbour regressions. For improve mode, record current behavior, failures, strengths, context cost, tool and permission behavior, and preserved regressions. Include exact prompts, outputs, checks, and snapshot bindings used to support the baseline.

### Research pack

Record exactly three lane identities, each lane's bounded question, GPT-5.6-Luna with max reasoning, source scope, evidence budget, start and end state, and limitations. Each evidence card contains:

- claim
- technique or practice
- direct source and locator
- applicable situation
- limitation or failure mode
- concrete experiment for this target
- lane identity and raw-source digest

### Evidence sieve

List every evidence-card identifier exactly once with `adopt`, `experiment`, or `reject`, a reason, detected conflicts, retained dissent, and design relevance. Record deduplication links without deleting the source cards.

### Design record

Record coherent alternatives, mechanisms, tradeoffs, challenges, evidence links, factual conclusions, user-owned questions, user decisions, rejected alternatives, and unresolved issues.

### Skill contract

Record purpose, success signal, triggers, non-triggers, inputs and preconditions, ordered behavior and decisions, allowed and forbidden actions, tools, permissions, delegation, outputs and consumers, failures, changed goals, stopping conditions, resources, and non-goals. Bind every statement that depends on evidence or a user decision to its source artifact.

### User-confirmation record

Record user identity as available to the host, confirmation timestamp, exact confirmation text or event digest, canonical target identity, contract artifact identifier, contract digest, target-snapshot digest, and accepted status. Only an explicit user event can set accepted status.

### Evaluation pack

Record the confirmed contract digest, target-snapshot digest, rubric digest, freeze timestamp, and three case partitions: visible development, frozen validation, and hidden release. Each case contains an identifier, partition, purpose, raw request digest, allowed context, setup manifest, observable assertions, forbidden effects, evidence requirements, and pass or fail rule.

Keep hidden prompts, expectations, and oracles in separately manifested helper-owned paths unavailable to candidate implementers and trial agents.

### Candidate record

Record candidate identifier, isolated locator, base snapshot digest, resulting revision and digest, writable role, owned paths, diff digest, local-check evidence, and the four candidate-entry bindings.

### Trial pack

For each fresh-context case, record:

- candidate digest
- case and request digests
- raw prompt digest
- loaded-skill digest
- fresh context identity
- tool-event digest
- output digest
- before and after target-manifest digests
- filesystem-result digest
- verdict and limitation

The pack records case coverage, repeated-case identity where needed, isolation evidence, leakage checks, and an aggregate manifest digest.

### Review record

Record reviewer identity, independence and read-only attestation, exact candidate revision, accessible input artifacts and their digests, access-check evidence, findings, severity, evidence, impact, correction, affected categories, and `ready` or `not ready`.

### Scorecard

Record the exact ten category names in accepted order. For each category, record the integer score, each binary criterion result, evidence identifiers, related findings, and repair history. Bind the scorecard to the rubric digest, candidate revision, evaluation-pack digest, and current review record.

### Verification record

Record verifier identity, independence and read-only attestation, exact candidate revision, exact commands or operations, start and end timestamps, exit status, relevant raw-output digests, before and after manifests, and conclusion.

### Release record

Record the finalized target and candidate identities, contract and confirmation digests, evaluation-pack digest, scorecard digest, final review and verification digests, retained limitations, release evidence manifest, authorized delivery scope, and finalization receipt.

### Delivery-acceptance record

Before cleanup, record an accepted installation or integration with action, destination identity, exact finalized revision, resulting destination digest, acceptance evidence, user authority event digest, actor, and timestamp.

## Binding rules

Compute SHA-256 over exact retained bytes. Define a deterministic canonical encoding for structured payloads and record its schema version. A digest claim without retained bytes or a canonical encoding is invalid.

Bind every artifact to the current workflow, canonical target, mode, state revision, and input digests. Bind Git targets to repository identity, branch, commit, and dirty-state digest. Bind non-Git targets to host identity, canonical locator, and target-snapshot digest.

Allow entry to candidate stage only when all of these match current state:

1. Current skill-contract digest.
2. Accepted user-confirmation record bound to that digest.
3. Frozen evaluation-pack digest bound to that contract and confirmation.
4. Recomputed target snapshot equal to the bound snapshot.

The helper must reject the transition when any binding is absent, stale, duplicated, substituted, or mismatched.

## Downstream invalidation

Apply invalidation atomically through the helper:

| Material change | Invalidate at least |
|---|---|
| Host or exact target identity | Mode, baseline, research, sieve, design, contract, confirmation, evaluation, candidate, trials, reviews, scores, verification, and release. |
| Mode | Baseline and every downstream artifact. |
| Target snapshot | Baseline and every downstream artifact. |
| Research or sieve conclusion | Dependent design decisions and every dependent artifact. |
| Design decision or skill contract | Confirmation and every downstream artifact. |
| User confirmation | Evaluation pack and every downstream artifact. |
| Evaluation pack or rubric | Candidate and every downstream artifact. |
| Candidate content or revision | Trials, reviews, scores, verification, and release. |
| Trial or review evidence | Dependent scores, verification, and release. |
| Verification input or result | Verification and release. |
| Delivery intent | Authority and delivery portions of the release record. |

Retain invalidated artifacts for audit within configured bounds. Never restore them merely because text appears unchanged. Recompute and rebind the affected evidence.

## Fail-closed recovery

On resume, execute the helper to discover state by canonical host and target identity, acquire the one active-target lock, validate ownership and permissions, reject symlinks, parse the supported schema, verify manifest contents and digests, verify the monotonic revision chain, and recompute the target snapshot.

Resume only from one unambiguous validated current generation. If state is missing, corrupt, split across competing generations, bound to a changed target, or owned by another active writer, stop and report recovery evidence. Never patch state manually or infer a later stage.

## Terminal stages

`finalized` is the first terminal stage. It retains release evidence and permits no candidate mutation. Delivery may follow only within recorded user authority.

`cleaned` is the second terminal stage. Enter it only after explicit cleanup authority and a valid delivery-acceptance record for the exact finalized revision. Validate the run directory as helper-owned immediately before deletion. Delete only that run directory, emit a cleanup receipt, and leave every target, repository, external candidate, and unowned path untouched.
