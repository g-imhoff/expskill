# Codex Dev Flow Design

## Purpose

Codex Dev Flow is a private Codex plugin for development work. Its public
interface is a set of independent phase skills plus one optional orchestrator.
Users can invoke one phase directly without loading a pipeline, or invoke the
orchestrator when they want the plugin to choose the next phase.

Implicit `use-expand` activation applies only to code and
executable-configuration changes. It stays inactive for reports, research,
explanations, read-only analysis, and prose. An explicitly requested phase,
including the read-only `review` phase, remains available.

## Public skill surface

The complete public skill tree is:

```text
skills/
├── use-expand/
├── brainstorm/
├── plan/
├── implement/
├── review/
├── verify/
└── integrate/
```

Every directory is a real skill with its own `SKILL.md` and
`agents/openai.yaml`. Phase definitions are not reference pages hidden under
another skill. Only `use-expand` permits implicit invocation; every phase skill
requires explicit phase intent.

The phase boundaries are deliberately narrow:

| Skill | Owns | Must not do |
| --- | --- | --- |
| `brainstorm` | Ambiguity, alternatives, assumptions, decisions | Write production code |
| `plan` | Accepted direction, boundaries, dependencies, ordered work, observable criteria, positive and negative behavior, test surfaces, and proof design | Write code or tests |
| `implement` | One accepted brief on one owned branch | Delegate, expand scope, or silently change an interface |
| `review` | Read-only findings with evidence, impact, and correction | Apply fixes |
| `verify` | Exact commands, exit evidence, and checkout inspection | Edit tracked source |
| `integrate` | Accepted branches in dependency order | Integrate failed or unverified work |

Direct invocation executes exactly the named phase and stops. A user can ask
for `$brainstorm` or `$plan` without loading `use-expand` or any other phase.

## Orchestration

`use-expand` is the only public orchestrator. It classifies one transition,
explains one concrete reason, opens only the selected phase, validates that
phase's revision-bound receipt against the canonical Plan Graph when one
exists, and then either recommends the next transition or stops.

Its reason map is intentionally small:

- `requirements-ambiguous` selects `brainstorm`;
- `bounded-change` selects `plan`, or `implement` after an approach is accepted;
- `cross-cutting` selects `plan`, whose design must include implementation and proof before implementation;
- `tests-ready` selects `implement`;
- `completion-gates` selects `review`, followed by `verify`;
- `accepted-branches` selects `integrate`.

The orchestrator cannot skip planned tests, review findings, or verification.
Blocked and user-decision outcomes stop instead of guessing.

## Canonical workflow state and phase receipts

`plan` owns a private, versioned Plan Graph stored outside the repository by
`plugins/codex-dev-flow/scripts/plan_graph.py`. The graph binds one workflow to
the canonical Git common directory, non-protected target branch, exact baseline,
dirty-state fingerprint, and monotonic graph revision. It records outcomes,
evidence, material decisions and confirmations, executable work and joins,
proof obligations, logical Git topology, and concise user projections.

The helper is the only graph writer. Implementers, reviewers, verifiers, and
integration lanes return typed receipts bound to workflow ID, graph revision,
node, branch, commit, commands, and evidence appropriate to their role. The one
coordinator validates those receipts and applies compare-and-swap updates.
Findings, stale receipts, failed checks, blocked work, and unresolved user
decisions never advance. Direct `plan` use stops at graph readiness without a
typed next-skill route; transition selection remains solely an optional
`use-expand` responsibility.

`brainstorm` is intentionally independent of this runtime graph. It may produce
a confirmed Concept Brief as authoritative conceptual input, but `plan` also
accepts another sufficiently concrete direction.

## Named-agent isolation

The conversational agent owns user decisions, transition selection,
coordination, and final adjudication. Bounded work can be delegated through
checked-in named-agent profiles. Each dispatch is context-free and receives
only the accepted brief, owned scope, applicable repository instructions, and
the evidence needed for that role.

The checked-in roster is:

| Profile | Purpose | Runtime policy |
| --- | --- | --- |
| `devflow-explorer` | Bounded repository evidence | Terra, medium, read-only |
| `devflow-test-engineer` | Shared acceptance tests | Luna, high, workspace-write |
| `devflow-implementer` | Routine bounded implementation | Luna, medium, workspace-write |
| `devflow-implementer-high` | Difficult bounded implementation | Luna, high, workspace-write |
| `devflow-reviewer` | Routine independent review | Terra, medium, read-only |
| `devflow-critical-reviewer` | Escalated critical review | Sol, high, read-only |
| `devflow-verifier` | Independent checks | Luna, medium, workspace-write |
| `devflow-verifier-low` | Small independent checks | Luna, low, workspace-write |

The canonical roster and declared internal budget tiers live in one atomic artifact,
`plugins/codex-dev-flow/assets/execution-policy.json`. Those tiers control
the intended cost, concurrency, retries, depth, elapsed-time, and escalation
limits. Static validation is implemented; runtime consumption and enforcement
remain release work. The tiers are an implementation detail, not additional
user-facing workflow skills.

Profile dispatch uses the exact checked-in `agent_type`; callers do not add a
runtime override that silently defeats the policy. Reviewers do not implement,
implementers do not delegate, and verifiers do not accept another agent's
claim as evidence.

## Branch and worktree ownership

Independent implementation streams use separate branches and external Git
worktrees. `plugins/codex-dev-flow/scripts/worktrees.py` creates and finishes
them under the user's state directory, outside the product checkout.

One writer owns each branch and explicit file scope. Integration occurs in
dependency order only after the branch is ready and verification passes.
Unmerged or failing work is preserved with a recovery path. Unknown branches,
worktrees, profiles, and user files are never treated as disposable cleanup.

## Distribution and installation

The repository is a local Codex marketplace. The installable plugin lives at
`plugins/codex-dev-flow/`; marketplace metadata lives at
`.agents/plugins/marketplace.json`.

`scripts/install.py` validates the package first, registers the marketplace and
plugin through Codex, and links the exact eight repository-owned agent profiles
into the user's Codex agent directory. It refuses foreign destinations instead
of overwriting them and rolls back only state it owns. A fresh Codex session is
required after installation or profile changes.

`scripts/validate.py` rejects missing or extra skills and profiles, malformed
metadata, policy drift, duplicate policy artifacts, non-regular required files,
symlinked package boundaries, and paths that resolve outside the plugin root.

## Required certification boundary

Deterministic contract tests already validate structure and offline behavior.
The live certification chain is not yet release-ready. It must use fresh
`codex exec --ephemeral --ignore-user-config --json` sessions against
disposable repositories and an installed plugin cache. The parent must own the
oracle; prompts, public identifiers, paths, and child environments must not
reveal the expected phase or private policy identity.

Retained evidence must bind the source package, installed skill, process identity,
exact invocation, loaded skill, raw session output, tool and command events,
Plan Graph and receipt bindings, and repository/Git state before and after execution. A transport
fixture may carry evidence but cannot manufacture semantic success.

Release certification must additionally require compatibility evidence, a signed
receipt, quality checks, and measured cost/latency thresholds. Missing or
inconsistent evidence fails closed.

## Failure handling

Findings and failed checks return to the phase that owns the work. A new product
decision returns to the user. Repeated technical blockers are reported with the
exact preserved branch and recovery path.

No phase rewrites history, deletes unmerged work, overwrites an unknown agent
profile, exposes private oracle data to a child, or performs an external write
outside the user's authorized scope.

## Scope exclusions

Version 1 does not provide Claude support, a generic repository-cleaning
command, automatic pull-request publication, target-repository workflow
reports, or automatic commits for direct phase invocation.
