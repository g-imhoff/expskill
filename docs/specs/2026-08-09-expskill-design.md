# ExpSkill Design

## Purpose

ExpSkill is a private Codex plugin with five independently usable
development skills and one optional lifecycle router. Users can invoke a skill
directly without loading a pipeline, or invoke `use-expskill` when they want the
plugin to select the next lifecycle step.

Only `use-expskill` permits implicit invocation. It stays inactive for explicit
skills, read-only analysis, reports, explanations, and non-code work.

## Public skill surface

```text
skills/
├── use-expskill/
├── brainstorm/
├── plan/
├── design/
├── implement/
└── test/
```

| Skill | Owns | Must not do |
| --- | --- | --- |
| `brainstorm` | Clarify and stress an idea into a confirmed Concept Brief | Write production code or plan implementation |
| `plan` | Ground the direction and create the private implementation/proof graph | Redesign the concept or write code/tests |
| `design` | Produce and approve isolated production-intended UI components | Implement the broader feature |
| `implement` | Coordinate TDD workers, independent review/spec gates, corrections, local joins, and final branch gates | Route the lifecycle, own a second state engine, push, or merge remotely |
| `test` | Exercise already-implemented behavior through realistic composed product paths and report revision-bound evidence | Plan or implement work, repair production code, review source, route the lifecycle, or deliver remotely |

Direct invocation runs only the named skill and stops at its boundary.
Test is explicit-only in this standalone slice. The current router mapping stays
unchanged until its later lifecycle migration.

## Lifecycle routing

`use-expskill` selects by the next unresolved decision:

- ambiguous outcome or experience → `brainstorm`;
- concrete direction without an accepted technical execution → `plan`;
- accepted UI work without approved components → `design`;
- accepted implementation facts and required Design deliverables → `implement`.

The router opens one skill per transition. There are no standalone review,
verification, or integration skills: those are internal gates owned by
`implement`. The router never grants remote-delivery or protected-branch merge
authority.

## Canonical workflow state

`plan` owns the private versioned Plan Graph outside the repository through
`plugins/expskill/scripts/plan_graph.py`. It records accepted outcomes,
constraints, decisions, work, dependencies, ownership, proof, projections, and
logical Git topology against an exact non-protected branch and baseline.

The Plan helper is the only graph writer. Other skills and workers return
compact revision-bound results to the active coordinator. `implement` does not
create another persistence format. A complete direct implementation brief may
run without Plan ancestry.

## Implement orchestration

The conversational coordinator grounds the accepted work and makes one cheap
parallelism pass. Independent nodes receive distinct ownership and external
worktrees; serial work may use the target checkout.

Each node uses a context-free `expskill-implementer` with only its accepted brief,
owned scope, relevant repository instructions, and required evidence. The worker
uses TDD, produces one coherent local commit, and cannot delegate or expand
scope.

The exact candidate is then judged concurrently by two fresh agents:

- `expskill-review` inspects code quality and regressions read-only;
- `expskill-spec` checks every accepted criterion and may run checks without
  editing tracked source.

Any finding goes to a new implementer. Both judges rerun on every correction.
Three consecutive non-improving attempts block only that node. Accepted nodes
join the target in dependency order and receive affected checks. Final fresh
review and spec gates cover the whole target branch.

## Named-agent profiles

| Profile | Purpose | Runtime policy |
| --- | --- | --- |
| `expskill-explorer` | Bounded repository evidence | Terra, medium, read-only |
| `expskill-test-engineer` | Shared acceptance tests | Luna, high, workspace-write |
| `expskill-implementer` | One owned TDD implementation node | Luna, medium, workspace-write |
| `expskill-review` | Independent candidate review | Terra, medium, read-only |
| `expskill-spec` | Independent specification compliance | Luna, high, workspace-write |

Checked-in profiles are the dispatch authority. Workers do not self-certify,
judges do not implement, and no delegated agent launches another agent.

## Branch and worktree safety

Implementation targets an existing non-protected workflow branch. Parallel
writers use external worktrees from exact accepted commits. One writer owns each
lane. A task lane is removed only after its exact commit is accepted on the
target, affected checks pass, and the lane is clean. Dirty, failing, unmerged,
unknown, or still-needed work is preserved.

AI may create local implementation commits and perform authorized local joins.
It never pushes, opens or updates a merge request, approves, merges, enables
auto-merge, or enters a merge queue under the Implement skill.

## Distribution and validation

The installable plugin lives under `plugins/expskill/`; marketplace
metadata lives at `.agents/plugins/marketplace.json`. `scripts/install.py`
validates the package, installs the plugin, and links exactly the five checked-in
agent profiles without overwriting foreign destinations.

`scripts/validate.py` rejects missing or extra skills/profiles, policy drift,
malformed metadata, symlinked package boundaries, and out-of-root paths. Focused
contract tests validate the orchestration and stop boundaries. Fresh behavioral
trials remain the release evidence for open-ended agent quality.

## Scope exclusions

The plugin does not provide automatic protected-branch merge, automatic remote
delivery, a generic repository cleaner, or a security-grade command-attestation
runtime. Those are intentionally outside the skill layer.
