# ExpSkill Design

## Purpose

ExpSkill is a private Codex plugin with eleven independently usable skills and
one optional lifecycle router. Users can invoke a skill
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
├── setup-ui-testing/
├── implement/
├── correct/
├── review/
├── test/
├── skill-builder/
├── unslop/
└── grill-me/
```

| Skill | Owns | Must not do |
| --- | --- | --- |
| `brainstorm` | Clarify and stress an idea into a confirmed Concept Brief | Write production code or plan implementation |
| `plan` | Ground the direction and create the private implementation/proof graph | Redesign the concept or write code/tests |
| `design` | Produce and approve isolated production-intended UI components | Implement the broader feature |
| `setup-ui-testing` | Establish or record one reusable project-native UI inspection capability | Redesign or implement production UI |
| `implement` | Coordinate TDD workers, independent review/spec gates, corrections, local joins, and final branch gates | Route the lifecycle, own a second state engine, push, or merge remotely |
| `correct` | Diagnose and repair concrete defects within the existing design | Make consequential structural or compatibility choices without the user, launch repair agents, or run the lifecycle |
| `review` | Inspect a user-defined pinned code scope and save evidence-backed issues and ratings | Fix code, prescribe corrections, use Implement's reviewer, or start another skill |
| `test` | Exercise already-implemented behavior through realistic composed product paths and report revision-bound evidence | Plan or implement work, repair production code, review source, route the lifecycle, or deliver remotely |
| `skill-builder` | Create or improve one exact agent skill | Implement unrelated product work |
| `unslop` | Rewrite prose without changing its meaning | Change product behavior |
| `grill-me` | Stress-test connected user-owned decisions after explicit consent | Select answers for the user |

Direct invocation runs only the named skill and stops at its boundary.
Setup UI Testing, Correct, Review, Test, Skill Builder, Unslop, and Grill Me
remain independently invokable. Router selection of Setup UI Testing is limited to an absent or
invalid project capability record and still requires its normal user
confirmation before writing setup.

## Lifecycle routing

`use-expskill` selects by the next unresolved decision:

- ambiguous outcome or experience maps to `brainstorm`
- a concrete defect with settled expected behavior maps to `correct`, which
  diagnoses whether a bounded repair is appropriate
- an absent or invalid `.ui-harness/README.md` maps only to `setup-ui-testing`
- concrete direction without an accepted technical execution maps to `plan`
- accepted UI work without approved components maps to `design`
- accepted implementation facts and required Design deliverables map to `implement`

Correct runs in the invoking agent without a profile or Plan Graph and checks
its own repair. At a structural or breaking boundary, it offers a sound
limited fix or Brainstorm and waits for the user to choose.

The router normally opens one skill per transition. When project UI testing is
ready and both Plan and Design remain unresolved, it may launch exactly one
planner and one designer concurrently. Both receive the same frozen baseline.
The planner runs in the target checkout and is the sole Plan Graph writer. The
designer runs in an isolated helper-owned worktree seeded only with the ignored
UI guide and agent-only support. The router serializes any user questions and
returns the approved Design receipt to the same Plan session before Plan can
become ready.

The public `review` skill is a directly invoked audit, not a lifecycle gate.
The invoking agent performs it without a new profile. Internal review,
verification, and integration remain owned by `implement`. The router never
grants remote-delivery or protected-branch merge authority.

## Canonical workflow state

`plan` owns the private versioned Plan Graph outside the repository through
`plugins/expskill/content/scripts/plan_graph.py`. It records accepted outcomes,
constraints, decisions, work, dependencies, ownership, proof, projections, and
logical Git topology against an exact non-protected branch and baseline.

The Plan helper is the only graph writer. Other skills and workers return
compact revision-bound results to the active coordinator. `implement` does not
create another persistence format. A complete direct implementation brief may
run without Plan ancestry.

For the parallel UI route, Design state binds a confirmed objective,
requirements, responsive expectations, non-goals, and source digest to one
isolated candidate commit. State records whether Design was invoked directly or
through the router. Only routed state can checkpoint a candidate, and routed
delivery requires that checkpoint. Plan validates the candidate-bearing Design
delivery receipt, requires the isolated branch tip to remain exactly one commit
above the frozen baseline until integration, and records the approved brief,
approval, and manifest digests in a typed Design join. Once the exact candidate
is an ancestor of the target HEAD, that ancestry preserves the join after
accepted temporary branch cleanup. Any semantic Plan change, including a
meaning-bearing typed evidence refresh, makes the join stale.

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
| `expskill-explorer` | Bounded repository evidence | Luna, max, read-only |
| `expskill-test-engineer` | Shared acceptance tests | Luna, max, read-only |
| `expskill-implementer` | One owned TDD implementation node | Luna, max, workspace-write |
| `expskill-planner` | One bounded routed Plan session and sole graph writer | Luna, max, workspace-write |
| `expskill-designer` | One bounded isolated Design candidate | Luna, max, workspace-write |
| `expskill-review` | Independent candidate review | Sol, xhigh, read-only |
| `expskill-spec` | Independent specification compliance | Sol, xhigh, read-only |

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
validates the package, installs the plugin, and links exactly the seven checked-in
agent profiles without overwriting foreign destinations.

`scripts/validate.py` rejects missing or extra skills/profiles, policy drift,
malformed metadata, symlinked package boundaries, and out-of-root paths. Focused
contract tests validate the orchestration and stop boundaries. Fresh behavioral
trials remain the release evidence for open-ended agent quality.

## Scope exclusions

The plugin does not provide automatic protected-branch merge, automatic remote
delivery, a generic repository cleaner, or a security-grade command-attestation
runtime. Those are intentionally outside the skill layer.
