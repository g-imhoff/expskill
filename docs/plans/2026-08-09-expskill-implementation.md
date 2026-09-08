# ExpSkill Implementation Plan

## Goal

Ship a private Codex plugin with nine independently invokable skills and one
optional lifecycle router. Keep durable planning, UI design, local
implementation orchestration, installation, and behavioral validation separate.

## Non-negotiable architecture

- Public skills are `use-expskill`, `brainstorm`, `plan`, `design`,
  `setup-ui-testing`, `implement`, `test`, `skill-builder`, `unslop`, and
  `grill-me`.
- Only `use-expskill` activates implicitly. It normally opens one skill per
  transition, with one bounded parallel Plan and Design route for UI work.
- `plan` owns the canonical private graph; no later skill invents another state
  engine.
- `implement` owns TDD workers, independent review/spec gates, corrections,
  local joins, affected checks, cleanup, and final whole-branch gates.
- Named-agent dispatch is context-free and uses exact checked-in profiles.
- Parallel writers use separate external worktrees and non-overlapping ownership.
- Unknown state, dirty work, conflicts, and unmerged lanes are preserved.
- AI never performs the final remote merge.

## File map

```text
.agents/plugins/marketplace.json
plugins/expskill/
├── .codex-plugin/plugin.json
├── assets/
│   ├── execution-policy.json
│   └── agents/
│       ├── expskill-explorer.toml
│       ├── expskill-test-engineer.toml
│       ├── expskill-implementer.toml
│       ├── expskill-planner.toml
│       ├── expskill-designer.toml
│       ├── expskill-review.toml
│       └── expskill-spec.toml
├── scripts/
│   ├── design_state.py
│   ├── plan_graph.py
│   └── worktrees.py
└── skills/
    ├── use-expskill/{SKILL.md,agents/openai.yaml}
    ├── brainstorm/{SKILL.md,agents/openai.yaml,references/brainstorm-techniques.csv}
    ├── plan/{SKILL.md,agents/openai.yaml}
    ├── design/{SKILL.md,agents/openai.yaml,references/*}
    ├── setup-ui-testing/{SKILL.md,agents/openai.yaml,references/*,scripts/*}
    ├── implement/{SKILL.md,agents/openai.yaml}
    ├── test/{SKILL.md,agents/openai.yaml}
    ├── skill-builder/{SKILL.md,agents/openai.yaml,references/*,scripts/*}
    ├── unslop/{SKILL.md,agents/openai.yaml,references/*}
    └── grill-me/{SKILL.md,agents/openai.yaml,references/*}
scripts/{install.py,validate.py}
tests/{test_contracts.py,test_implement_contract.py,test_install.py,...}
```

## Delivery sequence

### 1. Keep the public surface independent

- [x] Give every product skill a real entrypoint and explicit invocation policy.
- [x] Keep brainstorming, planning, and design independently usable.
- [x] Remove duplicate review, verification, and integration public skills.
- [x] Route only by the next unresolved lifecycle decision.

### 2. Keep Implement lean

- [x] Express orchestration in `skills/implement/SKILL.md`.
- [x] Use fresh implementer, review, and spec profiles.
- [x] Require task-local TDD and one coherent node commit.
- [x] Run review and spec concurrently on the same immutable candidate.
- [x] Send findings to a new implementer and bound non-improving retries.
- [x] Integrate accepted nodes locally and gate the whole target branch.
- [x] Exclude remote delivery and protected-branch merge.
- [x] Keep `implement_state.py` and command-attestation machinery out of the
  product package.

### 3. Preserve durable ownership boundaries

- [x] Keep the Plan Graph in the route-neutral Plan helper.
- [x] Keep Design state in the Design helper.
- [x] Keep worktree lifecycle in the route-neutral worktree helper.
- [x] Let workers return compact results rather than write canonical state.

### 4. Validate packaging and installation

- [x] Validate the exact ten-entry skill and seven-profile rosters.
- [x] Validate bounded Implement and parallel Plan and Design execution policy.
- [x] Reject symlinked or malformed package entries.
- [x] Link owned profiles transactionally and preserve foreign destinations.
- [ ] Run the complete repository suite from the final revision.

### 5. Validate behavior

- [x] Add focused static tests for TDD dispatch, independent gates, corrections,
  local integration, cleanup, and remote boundaries.
- [ ] Run bounded fresh-context trials for direct and Plan-backed work.
- [ ] Obtain one independent read-only review of the final revision.
- [ ] Repair only concrete behavior or contract failures.

### 6. Add optional UI setup routing and parallel preparation

- [x] Inspect the constant ignored `.ui-harness/README.md` record before UI
  routing.
- [x] Route only to Setup UI Testing when the record is absent or invalid.
- [x] Launch one planner and one isolated designer from the same baseline when
  both are unresolved and the setup is ready.
- [x] Bind the confirmed Design brief and candidate commit in private state.
- [x] Require a current approved Design join before Plan becomes ready.
- [x] Seed only the guide and agent-only support into the Design worktree.
- [x] Remove temporary harness copies and evidence after accepted integration.

### 7. Publish

- [ ] Merge the reviewed branch into `main`.
- [ ] Push `main`.
- [ ] Update the plugin cachebuster with the canonical helper.
- [ ] Reinstall the plugin and verify the installed skill/profile roster.
- [ ] Start a fresh session for user testing.

## Required verification commands

```text
python3 scripts/validate.py
python3 /home/gimhoff/.codex/skills/.system/skill-creator/scripts/quick_validate.py plugins/expskill/skills/implement
python3 /home/gimhoff/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/expskill
python3 -m pytest -q tests/test_implement_contract.py tests/test_contracts.py tests/test_install.py tests/test_worktrees.py
python3 -m pytest -q
```

## Completion definition

The reset is complete when the lean public surface validates, fresh behavior
uses the three Implement agents correctly, the full suite passes, one independent
review is ready, `main` is pushed, and the cachebusted plugin is reinstalled.
