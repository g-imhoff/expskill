# Codex Dev Flow

Codex Dev Flow is a private Codex plugin with seven independent development
phases and one optional orchestrator.

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

Invoke a phase directly when you know what you want:

- `$brainstorm` explores uncertainty without writing production code.
- `$plan` produces an ordered, reviewable approach.
- `$acceptance` defines observable criteria and failing tests.
- `$implement` applies one accepted brief.
- `$review` reports read-only findings and concrete corrections.
- `$verify` runs exact checks without editing tracked source.
- `$integrate` combines accepted branches in dependency order.

Invoke `$use-expand` when you want the plugin to select and explain the next
phase. It opens one phase per transition, consumes that phase's typed handoff,
and protects acceptance, review, and verification gates. It is the only skill
that may activate implicitly.

Direct phase invocation never loads the entire pipeline. For example:

```text
Use $brainstorm to compare storage approaches for this feature.
Use $plan to turn the accepted API decision into bounded tasks.
Use $review to inspect this branch against the accepted brief.
```

## Handoffs and evidence

Every phase stops with a machine-readable `phase-handoff-v1` containing exactly
`schema`, `selected_phase`, `reason_code`, `next_skill`, and `status`.
Findings return to their owning phase; blocked work and unresolved user
decisions do not advance.

The live certification chain is unfinished. Its required boundary must use
fresh `codex exec --ephemeral --ignore-user-config --json` sessions in
disposable repositories. It must retain evidence binding the installed skill,
invocation, loaded skill, typed handoff, commands, and repository state, and it
must fail closed on missing or inconsistent evidence.

## Repository commands

```bash
python3 scripts/validate.py
python3 -m pytest -q tests/test_contracts.py tests/test_install.py tests/test_worktrees.py
python3 -m pytest -q tests/test_phase_skills_acceptance.py
```

The broader acceptance suites cover budgets, security, behavior, portability,
and maturity. See
[`tests/ACCEPTANCE_MATRIX.md`](tests/ACCEPTANCE_MATRIX.md) for the regression
each group is intended to prevent.

The live certification and release chain is still being hardened. The phase
surface and installer are available on the integration branch, but the plugin
must not be called release-certified until every gate in
[`docs/release-policy.md`](docs/release-policy.md) passes.
