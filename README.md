# Codex Dev Flow

Codex Dev Flow is a private Codex plugin with four independent development
skills and one optional lifecycle router.

## Install and validate

From the repository root:

```bash
python3 scripts/validate.py
python3 scripts/install.py
```

Start a new Codex session after installation so the skills and linked agent
profiles are rediscovered. Use `python3 scripts/install.py --dry-run` to inspect
the planned changes and `python3 scripts/install.py --uninstall` to remove only
repository-owned installation state.

## Skills

Invoke a skill directly when you know what you want:

- `$brainstorm` explores uncertainty without writing production code.
- `$plan` produces an ordered, reviewable implementation-and-proof design with observable criteria, behavior, planned tests, and verification intent.
- `$design` creates grounded production-intended UI components and admits them to responsive, stateful review only after blocking quality gates pass.
- `$implement` coordinates isolated TDD workers, independent review and spec
  gates, corrections, local integration, and final whole-branch gates.

Invoke `$use-expand` when you want the plugin to select and explain the next
skill. It opens one skill per transition, coordinates against the canonical
Plan Graph when one exists, validates revision-bound receipts, and preserves
the implementation gates. It is the only
skill that may activate implicitly.

Direct skill invocation never loads the entire pipeline. For example:

```text
Use $brainstorm to compare storage approaches for this feature.
Use $plan to turn the accepted API decision into bounded tasks.
Use $implement to execute this accepted implementation work.
```

## Workflow state and evidence

`$plan` creates the private canonical Plan Graph outside the repository. Later
skills consume its exact workflow ID, graph revision, branch, commit, work, and
proof obligations. Workers and independent judges return bound receipts; only
the coordinator validates and applies them. Direct `$plan` use stops when the
plan is ready and never emits a next-skill route. Findings, blocked work, stale
evidence, and unresolved user decisions do not advance.

`$brainstorm` remains independently usable and produces a confirmed Concept
Brief without requiring the graph. `$use-expand` owns optional transition
selection; the individual skills do not silently open the whole pipeline.

The live certification chain is unfinished. Its required boundary must use
fresh `codex exec --ephemeral --ignore-user-config --json` sessions in
disposable repositories. It must retain evidence binding the installed skill,
invocation, loaded skill and helper, Plan Graph observations, skill results,
commands, and repository state, and it must fail closed on missing or
inconsistent evidence.

## Repository commands

```bash
python3 scripts/validate.py
python3 -m pytest -q tests/test_contracts.py tests/test_install.py tests/test_worktrees.py
python3 -m pytest -q tests/test_brainstorm_contract.py tests/test_plan_contract.py tests/test_plan_graph.py tests/test_plan_graph_stage10.py
```

The focused contract and runtime suites cover the currently implemented skill,
installation, security, concurrency, recovery, and lifecycle boundaries.

The live certification and release chain is still being hardened. The phase
surface and installer are available on the integration branch, but the plugin
must not be called release-certified until every gate in
[`docs/release-policy.md`](docs/release-policy.md) passes.
