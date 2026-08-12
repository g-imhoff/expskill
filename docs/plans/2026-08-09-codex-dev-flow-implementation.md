# Codex Dev Flow Implementation Plan

## Goal

Ship a private Codex plugin with six independently invokable development
phases and one optional orchestrator. Keep phase behavior, internal execution
policy, installation, and live certification independently testable.

## Non-negotiable architecture

- The public skills are exactly `use-expand`, `brainstorm`, `plan`,
  `implement`, `review`, `verify`, and `integrate`.
- Every phase is a real skill. No phase is stored as a reference file beneath
  another skill.
- Only `use-expand` can activate implicitly.
- Direct phase invocation runs exactly one phase and stops at that phase's own
  output boundary; direct `plan` never emits a next-skill route.
- `use-expand` opens exactly one selected phase per transition and never skips
  a required gate.
- Named-agent dispatch is context-free and uses the exact checked-in profile.
- Independent writers use separate external worktrees and non-overlapping file
  ownership.
- Unknown user state and unmerged work are preserved.
- Runtime code uses the Python standard library only.

## File map

```text
.agents/plugins/marketplace.json
plugins/codex-dev-flow/
├── .codex-plugin/plugin.json
├── assets/
│   ├── execution-policy.json
│   └── agents/devflow-*.toml
├── scripts/worktrees.py
└── skills/
    ├── use-expand/{SKILL.md,agents/openai.yaml}
    ├── brainstorm/{SKILL.md,agents/openai.yaml}
    ├── plan/{SKILL.md,agents/openai.yaml}
    ├── implement/{SKILL.md,agents/openai.yaml}
    ├── review/{SKILL.md,agents/openai.yaml}
    ├── verify/{SKILL.md,agents/openai.yaml}
    └── integrate/{SKILL.md,agents/openai.yaml}
scripts/
├── install.py
└── validate.py
tests/
├── test_brainstorm_contract.py
├── test_contracts.py
├── test_install.py
├── test_plan_contract.py
├── test_plan_graph.py
├── test_plan_graph_stage10.py
├── test_worktrees.py
└── test_improve_skill.py
```

## Delivery sequence

### 1. Lock the public phase contract

- [x] Add acceptance tests for the exact seven-skill roster.
- [x] Require one `SKILL.md` and one `agents/openai.yaml` per skill.
- [x] Require only `use-expand` to allow implicit invocation.
- [x] Reject phase cross-invocation and legacy route-skill directories.
- [x] Replace the early generic handoff draft with the private Plan Graph and
  revision-bound role receipts used by graph-backed phases.
- [x] Cover ready, findings, blocked, and user-decision outcomes.

Gate: contract tests must fail when a phase is a nested reference, silently
loads another phase, advances after failure, or changes the handoff schema.

### 2. Implement the independent skills

- [x] Scaffold all seven skills with the canonical skill creator.
- [x] Give every phase a narrow ownership boundary and explicit stop behavior.
- [x] Implement one-transition routing in `use-expand`.
- [x] Delete the former public route-skill directories.
- [x] Move worktree support to the route-neutral plugin scripts directory.
- [x] Validate every skill with the canonical skill validator.

Gate: a user can invoke `$brainstorm`, `$plan`, or any other phase without
loading the orchestrator or the rest of the pipeline.

### 3. Align execution policy and installed profiles

- [x] Add the exact eight named-agent profiles.
- [x] Bind role, profile, `agent_type`, runtime, effort, sandbox, and escalation
  in one canonical execution-policy artifact.
- [x] Keep routine exploration and review read-only.
- [x] Reserve critical review for explicit escalation.
- [x] Declare and statically validate call, concurrency, depth, retry, and
  elapsed-time budgets.
- [ ] Enforce those budgets in the production execution boundary.

Gate: any missing, extra, renamed, reordered, or mismatched selected profile is
rejected before an agent or Codex child starts.

### 4. Harden validation and installation

- [x] Validate the exact skill/profile roster and policy schema.
- [x] Require exactly one canonical execution-policy artifact.
- [x] Reject symlinked package components and out-of-root resolution.
- [x] Link every owned profile transactionally.
- [x] Refuse foreign destinations and preserve unrelated user state.
- [x] Make uninstall remove only links still owned by this repository.

Gate: malformed packages fail before marketplace registration, Codex calls, or
agent-destination mutation.

### 5. Certify real phase sessions

- [ ] Run five fresh installed-plugin sessions in disposable repositories.
- [ ] Resolve the real Codex launcher and bind the exact observed process chain.
- [ ] Keep the expected phase in a randomized parent-only oracle.
- [ ] Reject every unexpected router tool or command event.
- [ ] Compare complete before/after repository and Git snapshots.
- [ ] Prove direct-phase controls remain bounded.
- [ ] Retain raw session, handoff, loaded-skill, process, tool, command, and
  repository evidence.

Gate: the live runner must not accept source-tree substitution, resumed
sessions, leaked user configuration, fabricated transport semantics, stale
artifacts, hidden skill loads, or unapproved repository mutation.

### 6. Make CI and release gates executable

- [ ] Execute the complete acceptance root without recursively invoking the job
  harness itself.
- [ ] Run compatibility, receipt, and certification gates from a checked-in job
  specification.
- [ ] Require release to depend on all four gates.
- [ ] Retain newly produced evidence under the job workspace for a positive,
  exact number of days.
- [ ] Add Linux, Python, Git, and Codex compatibility coverage.
- [ ] Require the release quality, receipt, cost, and latency thresholds.

Gate: forcing any dependency to fail prevents release, and no stale or
repository-local artifact can satisfy retention.

### 7. Publish and reinstall

- [ ] Run repository validation and canonical plugin validation.
- [ ] Run focused phase, installer, worktree, portability, security, budget,
  behavioral, and maturity suites.
- [ ] Obtain an independent read-only review and independent verification.
- [ ] Update the plugin cachebuster with the canonical helper.
- [ ] Reinstall the marketplace plugin and confirm all eight profile links.
- [ ] Start a fresh Codex session and run the installed-plugin smoke test.

Gate: publication occurs only from a clean integrated branch with passing
review, verification, and certification evidence.

## Required verification commands

The final command set is intentionally explicit:

```text
python3 scripts/validate.py
python3 /home/gimhoff/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/codex-dev-flow
python3 -m pytest -q tests/test_contracts.py tests/test_install.py tests/test_worktrees.py tests/test_improve_skill.py
python3 -m pytest -q tests/test_brainstorm_contract.py tests/test_plan_contract.py
python3 -m pytest -q tests/test_plan_graph.py tests/test_plan_graph_stage10.py
```

The frozen fresh-context evaluator is an additional release gate; a passing
deterministic subset is not live certification evidence.

## Completion definition

The migration is complete only when the public surface contains exactly the
seven skills, direct invocation remains independent, the installed profile and
policy artifacts validate, real sessions produce independently judged typed
handoffs, CI exercises every gate, release evidence is retained, and the
cachebusted plugin is reinstalled in a fresh session.
