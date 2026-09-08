---
name: plan
description: Plan a sufficiently concrete direction through explicit $plan invocation or a bounded ExpSkill router session.
metadata:
  opencode/slash: "true"
  opencode/autoinvoke: "false"
---
# Plan

## Boundary

`$plan` is a standalone, concept-read-only and source-read-only planning skill
that may also run as one router-owned `expskill-planner` session. Accept a
completed Concept Brief or another sufficiently concrete direction. Preserve
settled conceptual decisions. Technical evidence may expose a contradiction,
but planning must not repeat brainstorm, simulate brainstorm, or silently
redesign the concept.

Return `not-ready` for a vague direction. Do not route, invoke, open, select, or recommend another skill, and do not begin production or test implementation, review, verification, integration, or GitHub delivery. Plan writes no production code, tests, executable configuration, or repository planning document. Its only writes are private graph operations through the helper and the one confirmed branch operation defined below.

## Workflow

Follow this order, scaling depth to the work:

1. Inspect branch policy, the exact repository revision, and relevant uncommitted work. Resolve protection from repository instructions, authoritative hosted policy when available, and remote/default-branch metadata. Use common protected names only as a conservative fallback. The live integration target must be a non-protected workflow branch. If HEAD is protected or detached, propose one collision-free new local non-protected branch and its exact starting commit, explain how dirty work is treated, and wait for explicit user confirmation. Creating and switching to that branch after confirmation is the only repository Git mutation allowed to plan. Never overwrite a branch or commit, push, merge, rebase, stash, reset, delete a branch, or create a task worktree.
2. Once the branch is eligible, create or resume its canonical graph. Extract fixed outcomes, constraints, non-goals, and accepted conceptual decisions. Keep them distinct from repository facts and proposed mechanisms.
3. The main agent grounds the direction in the code before shaping the plan. Start at the likely implementation seam, then follow only relevant callers, state and effects, interfaces and consumers, dependencies, configuration, tests, runtime constraints, and history. Local grounding is never delegated and does not require confirmation. Before asking anything factual, exhaust supplied context, repository evidence, prior accepted evidence, and authoritative sources. Ask exactly one focused question per turn only for a material user-owned decision, preference, authorization, or confirmation. Use an explicit safe inference for routine reversible details.
4. Classify feasibility as feasible, conditionally feasible, contradicted, or not assessable. When code contradicts an accepted concept, cite the exact evidence and consequence, propose the smallest necessary amendment, and require approval instead of reopening broad ideation.
5. Create the adaptive minimum after the first grounding pass: one outcome, one grounded work slice or leaf task, and one proof obligation. Expand only for material decisions, boundaries, dependencies, consequences, or proof. Do not manufacture hierarchy, research, concurrency, rollback, or specialized lenses.
6. Show a concise current technical reality orientation: the existing shape, the important gap or constraint, and its planning consequence. This is context, not a confirmation gate.
7. Resolve remaining technical uncertainty. Use a targeted official or primary lookup when the mechanism and source family are already selected. Use broad research only when meaningful alternatives or consequential uncertainty remain: exactly three independent high-reasoning research lanes on distinct questions, with no repository access, preferred answer, sibling output, edit authority, user interaction, or planning role. Use an executable route exposed by the current host. On Codex, request `gpt-5.6-luna` at max reasoning for each lane when the dispatch mechanism supports choosing the engine and reasoning effort. Otherwise, use a fresh subagent at the host's highest available reasoning tier and record the actual engine and effort. On OpenCode, select the named `expskill-explorer` subagent in each Task call. Its generated active provider setting supplies the engine and `xhigh` reasoning effort, while its external-research overlay supplies the evidence-only role. Never invent unsupported engine or reasoning fields. If the host exposes no high-reasoning route, stop and report the capability failure. Add at most one fourth lane through the same host route, kept independent, for one unresolved decision-relevant conflict or gap. The main agent reconciles findings with repository truth, presents the materially distinct feasible or conditional options and one recommendation, and waits for the user to select. Preserve rejected alternatives and their evidence-based reasons.
8. Build the complete implementation-facing graph. Connect outcomes, material evidence and decisions, slices, optional independently ownable leaf tasks, explicit joins, proof obligations, user projections, and the logical Git topology. Attach tests and proof to the behavior they validate. Classify work as `serial`, `parallel-candidate`, or `parallel-safe`. Prove stable inputs, ownership, isolation, independent proof, and a join before calling work parallel-safe. Make one cheap concurrency pass and stop when extra analysis would save little. Never spend more effort proving parallelism than it is expected to save.
9. Run deterministic structural validation and a brief main-agent semantic coverage closure. For broad, complex, or high-consequence plans, run exactly one bounded independent adversarial audit. The auditor may find concrete omissions or unsupported claims but may not research, redesign, edit, question the user, or approve the plan. Tiny plans receive no audit ceremony.
10. Only after the backend graph is complete, derive concise progressive user projections in dependency order. The user never sees YAML. Present one coherent part per turn, include every material decision in plain language, and ask the user to confirm or correct its meaning. Optimize decision-relevant information per word instead of enforcing a word, bullet, or heading count. On correction, update canonical meaning first, explain the affected subgraph, invalidate and regenerate only affected evidence, decisions, work, proof, and projections, and preserve unrelated confirmations. Wording-only clarification is non-material. Derive `ready` automatically when every current part is confirmed and all invariants pass. Ask no redundant final confirmation.

In routed parallel mode, work from the exact target checkout and baseline given
by the router. Remain the only Plan Graph writer. Return user questions to the
router instead of asking them directly. Mark the optional typed Design join as
required when production UI approval is part of the accepted outcome. The graph
must remain `not-ready` while that receipt is absent or stale. When the same
router returns the sibling Design result, verify its frozen baseline, isolated
branch, one-commit candidate, Design workflow revision, confirmed brief digest,
approval digest, and manifest digest against the unchanged candidate-bearing
Design delivery receipt. Require its delivered lifecycle and require the
isolated branch tip to remain the exact candidate commit until integration.
After the exact candidate becomes an ancestor of the target HEAD, validate
that integrated ancestry so accepted cleanup may remove the isolated branch. Record it only
through the packaged helper's typed Design-join operation. Do not read or write
the Design state directly. A malformed, failed, unapproved, or stale result
stops the join.

## Canonical private graph

The deterministic helper is the only graph writer. Resolve it relative to the loaded skill package at `../../scripts/plan_graph.py`. Its source-package path is `packages/expskill/scripts/plan_graph.py`. Use its dependency-free API or CLI for initialize, discover, load, apply, recover, pause, resume, discard, confirmed branch creation, and terminal completion. Omit `--state-home` in normal use so it owns a private XDG state root. Explicit roots exist only for isolated tests.

The graph is strict JSON-compatible YAML private state outside the repository. It permits one active graph per canonical repository identity and target branch, binds an opaque workflow ID, exact repository baseline, dirty-state fingerprint, and monotonic graph revision, and derives lifecycle state rather than trusting an edited status. It records outcomes, evidence, decisions, work, proof, Git topology, delivery state, and user projections without storing secrets, source copies, transcripts, or unbounded output.

Every mutation uses a short identity lock, expected-revision compare-and-swap, strict validation, atomic synchronized replacement, and at most one previous valid generation. Refuse unsafe permissions, symlinks, non-regular or ambiguous state, duplicate or substituted identity, unsupported schemas, and stale receipts. Recover only an unambiguous valid generation. Pause preserves state. Resume revalidates it. Discard requires explicit user authority and claims no success. Workers never write canonical state: they return receipts bound to workflow ID, graph revision, node, branch, commit, and evidence. Reapply only demonstrably disjoint stale updates. Competing semantic updates require reconciliation and user input only when incompatible intent remains.

Readiness requires every outcome and constraint to have grounded work and planned proof, every material decision to be supported and currently confirmed, complete acyclic dependencies and joins, credible ownership and concurrency, a reproducible eligible baseline, and no relevant stale, unresolved, conflicting, or awaiting-user state.

## Implementation, proof, and Git execution contract

The complete graph is the detailed implementation and proof design. It records observable acceptance criteria, positive and negative behavior, verification intent, relevant test surfaces and commands, and test work, while planning itself writes no code or tests and never marks an unexecuted obligation proven. Later evidence must identify the executed command, result, and exact repository revision. Relevant changes stale prior evidence.

The graph records later Git execution without performing it. The non-protected workflow branch remains the live integration target. Serial work may later commit directly there. Concurrent writing lanes may later use external worktrees created from exact accepted target commits. One writer owns each lane, shared interfaces are stabilized before fan-out, and a coordinator serializes incremental target-side integration. The graph records the gates under which a later authorized phase may verify a clean accepted lane, remove its worktree, and safely delete only a branch that is committed-to-target and accepted-on-target. Dirty, failing, unmerged, unknown, or still-needed state is preserved. Future executors revalidate the exact repository revision and all parallel-safety claims.

## Stop and downstream boundary

On standalone success, say concisely that the plan is ready, summarize its high-level execution shape, material protections, proof intent, and workflow branch in human terms, and disclose honest non-blocking risks. Keep the private graph for later direct phases or `use-expskill`. Emit no typed handoff and do not begin implementation.

A later authorized lifecycle, not `$plan`, may push after the first coherent implementation commit, open a draft request against a protected destination, and bind checks to its exact head. It may reach `ready-for-human-review` only after planned work, review findings, independent verification, target and join proof, required checks, and lane cleanup are current. At that terminal gate the helper emits a durable receipt and deletes owned graph generations. AI must never approve, merge, enable auto-merge, or enter a merge queue. Manual review and merge remain outside observable AI control.
