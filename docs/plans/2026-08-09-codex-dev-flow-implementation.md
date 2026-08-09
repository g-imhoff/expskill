# Codex Dev Flow Implementation Plan

> **For agentic workers:** Implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, install, and certify a private Codex plugin that routes code changes through Quick or Full workflows with model-pinned custom agents, then remove the project-local workflow machinery it replaces in Expand.

**Architecture:** The private repository is a local Codex marketplace containing one skills-only plugin. An idempotent Python installer registers that marketplace and links repository-owned custom-agent TOML files into the user's Codex agent directory. Deterministic Python helpers validate package contracts, run reviewer and explorer roles as isolated read-only Codex processes, and keep Full-route Git worktrees outside product repositories.

**Tech Stack:** Codex plugin and skill manifests, custom-agent TOML, Python 3.11+ standard library, `unittest`, Git, Codex CLI 0.147+, GitHub CLI.

## Global Constraints

- The conversational orchestrator is GPT-5.6 Sol with max reasoning.
- The independent reviewer is GPT-5.6 Sol with xhigh reasoning and a read-only sandbox.
- Explorer, test engineer, implementer, and verifier agents use GPT-5.6 Luna with max reasoning.
- No profile uses Terra or any other model.
- The router asks for Quick or Full when the user did not explicitly choose; it performs no implementation before the answer.
- Quick stays available for localized API, database, CI, security, and configuration changes.
- Full uses coherent review-sized tasks, one writer per branch, and external worktrees.
- Runtime state and worktrees stay outside product repositories.
- No generic repository-cleaning feature, Claude support, GitHub PR automation, or target-repository workflow report is added.
- Unknown agent-profile paths and unmerged work are preserved rather than overwritten or deleted.
- Do not add third-party runtime dependencies.
- Standalone reviewer and explorer processes suppress plugin, personal, and project skill context while retaining built-in `.system` skills.
- Custom named agents always use the matching `agent_type` with `fork_turns: "none"` and no model or reasoning override.
- The base plugin version is `0.1.0`; local Codex cachebusters may append `+codex.<token>`.

---

## File map

- `.agents/plugins/marketplace.json`: private local marketplace entry for the plugin.
- `plugins/codex-dev-flow/.codex-plugin/plugin.json`: plugin identity and skills path.
- `plugins/codex-dev-flow/assets/agents/*.toml`: source-of-truth custom-agent profiles installed by symlink.
- `plugins/codex-dev-flow/scripts/read_only_agent.py`: isolated reviewer/explorer process runner whose role contracts come from the checked-in profiles.
- `plugins/codex-dev-flow/skills/route-code-change/`: implicit code-change router and UI metadata.
- `plugins/codex-dev-flow/skills/quick-code-change/`: explicit Quick workflow and UI metadata.
- `plugins/codex-dev-flow/skills/full-code-change/`: explicit Full workflow, UI metadata, and worktree helper.
- `scripts/validate.py`: repository-wide manifest, skill, and custom-agent contract validator.
- `scripts/install.py`: transactional marketplace/plugin registration and agent-link lifecycle.
- `tests/test_contracts.py`: package, skill, and profile contract tests.
- `tests/test_install.py`: installer ownership and rollback tests.
- `tests/test_worktrees.py`: real-Git external worktree lifecycle tests.
- `tests/scenarios/`: stable prompts and observable expectations used for baseline and skill-enabled forward tests.
- Evaluation transcripts live under the user's state directory and are never committed.

### Task 1: Package contract and model-pinned agent profiles

**Files:**
- Create: `.agents/plugins/marketplace.json`
- Create: `plugins/codex-dev-flow/.codex-plugin/plugin.json`
- Create: `plugins/codex-dev-flow/assets/agents/devflow-explorer.toml`
- Create: `plugins/codex-dev-flow/assets/agents/devflow-test-engineer.toml`
- Create: `plugins/codex-dev-flow/assets/agents/devflow-implementer.toml`
- Create: `plugins/codex-dev-flow/assets/agents/devflow-reviewer.toml`
- Create: `plugins/codex-dev-flow/assets/agents/devflow-verifier.toml`
- Create: `scripts/validate.py`
- Create: `tests/test_contracts.py`
- Create: `.gitignore`

**Interfaces:**
- Produces: `validate_repository(root: Path) -> tuple[str, ...]`, returning all deterministic contract errors.
- Produces: five `devflow-*` profiles whose `name` fields are the dispatch source of truth.
- Produces: marketplace name `codex-dev-flow` and plugin selector `codex-dev-flow@codex-dev-flow`.

- [ ] **Step 1: Write the failing contract tests**

Create `tests/test_contracts.py` with `unittest` cases that require the exact plugin and marketplace identities, unique skill names, and this roster:

```python
EXPECTED_AGENTS = {
    "devflow-explorer": ("gpt-5.6-luna", "max", "read-only"),
    "devflow-test-engineer": ("gpt-5.6-luna", "max", "workspace-write"),
    "devflow-implementer": ("gpt-5.6-luna", "max", "workspace-write"),
    "devflow-reviewer": ("gpt-5.6-sol", "xhigh", "read-only"),
    "devflow-verifier": ("gpt-5.6-luna", "max", "workspace-write"),
}
```

Mutation tests copy the repository contract to a temporary directory, then prove validation rejects a missing profile, a duplicate skill name, a Terra model, a writable reviewer, an invalid manifest path, and an unexpected `devflow-*` profile.

- [ ] **Step 2: Run the tests and confirm RED**

Run: `python3 -m unittest tests.test_contracts -v`

Expected: FAIL because `scripts.validate` and the plugin files do not exist.

- [ ] **Step 3: Scaffold the marketplace-backed plugin**

Run the canonical scaffold from the plugin-creator skill:

```bash
python3 /home/gimhoff/.codex/skills/.system/plugin-creator/scripts/create_basic_plugin.py \
  codex-dev-flow \
  --path /home/gimhoff/projects/codex-dev-flow/plugins \
  --marketplace-path /home/gimhoff/projects/codex-dev-flow/.agents/plugins/marketplace.json \
  --marketplace-name codex-dev-flow \
  --with-skills \
  --with-assets \
  --with-marketplace
```

Set plugin version `0.1.0`, repository `https://github.com/g-imhoff/codex-dev-flow`, category `Developer Tools`, and skills path `./skills/`. Do not add hooks, MCP, apps, icons, or authentication dependencies.

- [ ] **Step 4: Implement the validator and profiles**

Use `json`, `tomllib`, `pathlib`, and a small frontmatter parser. `validate_repository` must aggregate errors rather than stop at the first one. Each profile must define `name`, `description`, `model`, `model_reasoning_effort`, `sandbox_mode`, and `developer_instructions`.

Agent instructions must enforce these boundaries:

- explorer: read-only evidence gathering, no fixes or delegation;
- test engineer: test strategy plus shared acceptance tests, named regression targets, no product implementation;
- implementer: exactly one brief, task-local red-green-refactor, one owned branch, no delegation or scope expansion;
- reviewer: read-only actionable findings with severity, evidence, impact, correction, and a Ready/Not ready verdict;
- verifier: exact commands and exit evidence, no tracked-source edits, no reliance on another agent's claims.

- [ ] **Step 5: Run contract validation and canonical plugin validation**

Run:

```bash
python3 -m unittest tests.test_contracts -v
python3 scripts/validate.py
python3 /home/gimhoff/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/codex-dev-flow
```

Expected: all commands exit `0`.

- [ ] **Step 6: Commit the package contract**

```bash
git add .gitignore .agents plugins/codex-dev-flow/.codex-plugin plugins/codex-dev-flow/assets scripts/validate.py tests/test_contracts.py
git commit -m "feat: define plugin and agent contracts"
```

### Task 2: Transactional installer and owned uninstall

**Files:**
- Create: `scripts/install.py`
- Create: `tests/test_install.py`

**Interfaces:**
- Produces: `ProfileLink(source: Path, destination: Path)`.
- Produces: `preflight_links(repo_root: Path, codex_home: Path) -> tuple[ProfileLink, ...]`.
- Produces: `install(repo_root: Path, codex_home: Path, state_home: Path, run: Runner) -> InstallResult`.
- Produces: `uninstall(repo_root: Path, codex_home: Path, state_home: Path, run: Runner) -> InstallResult`.
- CLI: `python3 scripts/install.py [--dry-run | --uninstall]`.

- [ ] **Step 1: Write failing installer lifecycle tests**

Use temporary repository, Codex home, and state directories plus an injected fake command runner. The conflict test must assert the transaction boundary directly:

```python
def test_regular_file_conflict_refuses_without_partial_links(self):
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        repo = seed_repository(root / "repo")
        codex_home = root / "codex"
        state_home = root / "state"
        conflict = codex_home / "agents" / "devflow-reviewer.toml"
        conflict.parent.mkdir(parents=True)
        conflict.write_text("user-owned\n")

        with self.assertRaisesRegex(InstallError, "devflow-reviewer.toml"):
            install(repo, codex_home, state_home, FakeRunner())

        self.assertEqual(
            sorted(path.name for path in conflict.parent.iterdir()),
            ["devflow-reviewer.toml"],
        )
```

Add equally explicit methods named `test_install_links_every_profile_and_registers_plugin_once`, `test_second_install_is_a_no_op_for_owned_links`, `test_unrelated_and_broken_symlink_conflicts_refuse`, `test_plugin_failure_rolls_back_links_created_by_this_run`, `test_uninstall_removes_only_links_still_owned_by_receipt`, and `test_uninstall_preserves_retargeted_links_and_preexisting_marketplace`. Each method asserts the exact link set, fake-runner command sequence, receipt state, and preserved paths relevant to its name.

- [ ] **Step 2: Run the tests and confirm RED**

Run: `python3 -m unittest tests.test_install -v`

Expected: FAIL because `scripts.install` does not exist.

- [ ] **Step 3: Implement atomic profile preflight and receipts**

Preflight every destination with `Path.lexists` semantics before creating any link. Existing paths are accepted only when they are symlinks resolving to the exact repository-owned source. Store the installation receipt at `${XDG_STATE_HOME:-~/.local/state}/codex-dev-flow/install.json` using a temporary file plus `os.replace`.

The receipt records the canonical repository path, links created by this installer, whether the marketplace was newly registered, and whether the plugin was newly installed. Uninstall removes an agent path only when it is still a symlink to the recorded source.

- [ ] **Step 4: Implement Codex CLI registration**

Run these commands with captured JSON and checked exit codes:

```text
codex plugin marketplace add <canonical-repository-root> --json
codex plugin add codex-dev-flow@codex-dev-flow --json
```

Re-adding the same marketplace and plugin is the normal idempotent path. A marketplace-name conflict from another source is fatal. On owned uninstall, run `codex plugin remove codex-dev-flow@codex-dev-flow --json`; remove the marketplace only when the receipt proves this installer added it and it still resolves to the same repository.

- [ ] **Step 5: Verify installer behavior**

Run:

```bash
python3 -m unittest tests.test_install -v
python3 scripts/install.py --dry-run
python3 scripts/validate.py
```

Expected: all tests pass; dry-run lists five links and two Codex commands without changing user state.

- [ ] **Step 6: Commit the installer**

```bash
git add scripts/install.py tests/test_install.py
git commit -m "feat: install plugin and agent profiles safely"
```

### Task 3: Route and Quick workflow skills

**Files:**
- Create: `plugins/codex-dev-flow/skills/route-code-change/SKILL.md`
- Create: `plugins/codex-dev-flow/skills/route-code-change/agents/openai.yaml`
- Create: `plugins/codex-dev-flow/skills/quick-code-change/SKILL.md`
- Create: `plugins/codex-dev-flow/skills/quick-code-change/agents/openai.yaml`
- Create: `tests/scenarios/route-code-change.md`
- Create: `tests/scenarios/quick-code-change.md`
- Modify: `tests/test_contracts.py`

**Interfaces:**
- Router output before consent: one recommendation, one concrete reason, and one Quick-or-Full question.
- Router selection: explicit route wins; otherwise Quick is the default unless a Full trigger adds concrete value.
- Quick output: concise plan, implementation evidence, independent review result, independent verification result, and remaining concerns.

- [ ] **Step 1: Run five no-skill routing baselines**

Use fresh Luna Max agents and a disposable repository. The pressure prompt requests a small API timeout-default change, claims it is urgent, and asks the agent to start without questions. Record exact baseline actions and rationalizations under the user's state directory, not in Git.

Expected RED: at least one agent begins implementation, recommends Full based only on the API label, or fails to present the required route question. If all controls already comply, tighten the scenario before authoring the skill.

- [ ] **Step 2: Create and implement `route-code-change`**

Initialize with `init_skill.py`, including UI metadata:

```bash
python3 /home/gimhoff/.codex/skills/.system/skill-creator/scripts/init_skill.py \
  route-code-change \
  --path plugins/codex-dev-flow/skills \
  --interface 'display_name=Route Code Change' \
  --interface 'short_description=Choose the proportionate development route' \
  --interface 'default_prompt=Use $route-code-change to route this coding change.'
```

Keep the frequently loaded skill below 200 words. Its description triggers only for requests that implement, fix, refactor, or build code and executable configuration. The body defines inactive work, the six Full predicates, the Quick default, the consent gate, and the exact sub-skill handoff.

- [ ] **Step 3: Forward-test and validate the router**

Run five fresh Luna Max samples with the skill. Cover small API, database, CI, security, and configuration changes as Quick; ambiguous cross-component work as Full; and non-coding or read-only requests as inactive. Confirm no tool mutation occurs before consent.

Run:

```bash
python3 /home/gimhoff/.codex/skills/.system/skill-creator/scripts/quick_validate.py plugins/codex-dev-flow/skills/route-code-change
python3 scripts/validate.py
```

- [ ] **Step 4: Run five no-skill Quick baselines**

Use a tiny disposable bug with an explicit Quick selection plus time, authority, and sunk-cost pressure to skip independent review or verification. Record baseline behavior outside Git.

Expected RED: at least one sample substitutes self-review, commits without authorization, or stops before independent gates.

- [ ] **Step 5: Create and implement `quick-code-change`**

Initialize it with implicit invocation disabled in `agents/openai.yaml`. Define the Sol Max orchestrator's short plan and implementation, then concurrent execution of exactly one isolated reviewer process and one `devflow-verifier`. Require fixes to return to the orchestrator, both gates to repeat after changes, no automatic commit, and a three-occurrence blocker threshold.

- [ ] **Step 6: Forward-test and validate Quick**

Run five fresh skill-enabled samples. Assert from tool activity and Git state that implementation precedes two independent gates, the reviewer remains read-only, the verifier does not edit tracked source, and no commit appears without authorization.

Run both skill validators, `python3 scripts/validate.py`, and `python3 -m unittest tests.test_contracts -v`.

- [ ] **Step 7: Commit the router and Quick skill**

```bash
git add plugins/codex-dev-flow/skills/route-code-change plugins/codex-dev-flow/skills/quick-code-change tests
git commit -m "feat: add route and quick workflows"
```

### Task 4: Full workflow and external worktree state machine

**Files:**
- Create: `plugins/codex-dev-flow/skills/full-code-change/SKILL.md`
- Create: `plugins/codex-dev-flow/skills/full-code-change/agents/openai.yaml`
- Create: `plugins/codex-dev-flow/skills/full-code-change/scripts/worktrees.py`
- Create: `tests/test_worktrees.py`
- Create: `tests/scenarios/full-code-change.md`
- Modify: `tests/test_contracts.py`

**Interfaces:**
- Produces: `create_worktree(repo: Path, base: str, run_id: str, task: str, state_home: Path) -> WorktreeRecord`.
- Produces: `finish_worktree(repo: Path, path: Path, branch: str, integrated_ref: str) -> None`.
- CLI: `worktrees.py create --repo <repository> --base <ref> --run-id <run> --task <task>` and `worktrees.py finish --repo <repository> --path <worktree> --branch <branch> --integrated-ref <ref>`.

- [ ] **Step 1: Write failing real-Git worktree tests**

Create disposable Git repositories and cover distinct external paths, sanitized names, duplicate refusal, dirty-worktree preservation, unmerged-commit preservation, integrated clean removal, exact branch deletion, and refusal when the supplied worktree is not owned by the computed state root.

- [ ] **Step 2: Run the worktree tests and confirm RED**

Run: `python3 -m unittest tests.test_worktrees -v`

Expected: FAIL because the helper does not exist.

- [ ] **Step 3: Implement the worktree helper**

Resolve the repository with `git rev-parse --show-toplevel`. Derive the state path from a SHA-256 hash of the canonical repository root under `${XDG_STATE_HOME:-~/.local/state}/codex-dev-flow/worktrees/<repo-hash>/<run-id>/<task>`. Reject empty, absolute, dot-segment, or non-slug run/task identifiers.

Before removal require: the path is an owned registered worktree, `git status --porcelain` is empty, the branch tip is an ancestor of `integrated_ref`, and the integration worktree is clean. Use `git worktree remove` followed by `git branch -d`. On any failed predicate, print the exact worktree and branch recovery path and preserve both.

- [ ] **Step 4: Run five no-skill Full baselines**

Use a disposable feature with two independent streams plus time, authority, and exhaustion pressure to share one working tree, skip the test specialist, accept self-verification, or delete failed work. Store transcripts outside Git.

Expected RED: at least one sample violates a Full invariant.

- [ ] **Step 5: Create and implement `full-code-change`**

Initialize it with implicit invocation disabled. Define acceptance-gated design, dependency-aware planning, isolated Luna Max exploration for bounded unknowns, Luna test-engineer dispatch before implementation, one Luna implementer per coherent task, external worktrees, concurrent isolated Sol XHigh review and Luna verification per task, fix loops, dependency-order integration, final concurrent review and verification, and safe worktree finishing.

The test engineer works in its own external worktree and commits the accepted shared tests while they fail for the expected missing behavior. Its Sol review checks the test contract and its Luna verification confirms the failure is behavioral rather than environmental. The orchestrator integrates that test commit before creating implementation task branches. Task verifiers run the shared subset owned by their brief; the integrated final verification requires the complete suite to become green.

The orchestrator asks immediately for a product decision outside the accepted design and after the same blocker occurs three times. It never treats a failed task as cleanup-eligible.

- [ ] **Step 6: Forward-test and validate Full**

Run five skill-enabled fresh samples and inspect agent dispatch, branches, worktree paths, commits, review/verification ordering, and preserved failure state. Run:

```bash
python3 -m unittest tests.test_worktrees -v
python3 -m unittest tests.test_contracts -v
python3 /home/gimhoff/.codex/skills/.system/skill-creator/scripts/quick_validate.py plugins/codex-dev-flow/skills/full-code-change
python3 scripts/validate.py
```

- [ ] **Step 7: Commit the Full workflow**

```bash
git add plugins/codex-dev-flow/skills/full-code-change tests
git commit -m "feat: add full parallel workflow"
```

### Task 5: Integrated certification and installation

**Files:**
- Modify only files required by confirmed review findings.
- Store runtime evaluation transcripts under `${XDG_STATE_HOME:-~/.local/state}/codex-dev-flow/evals/`.

**Interfaces:**
- Produces: installed marketplace `codex-dev-flow`, enabled plugin `codex-dev-flow`, and five discoverable custom-agent profiles.

- [ ] **Step 1: Run repository gates**

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate.py
python3 /home/gimhoff/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/codex-dev-flow
git diff --check
```

- [ ] **Step 2: Install from the private marketplace**

Run `python3 scripts/install.py`. Confirm marketplace registration JSON reports the canonical repository, plugin installation JSON reports an enabled installed path, and all five `~/.codex/agents/devflow-*.toml` paths resolve to repository-owned sources.

- [ ] **Step 3: Certify a genuinely fresh Codex session**

Start a new non-interactive Codex task against a disposable Git repository. Explicitly invoke `$route-code-change`, select Quick, and make a tiny tested change. Confirm the fresh session can start the isolated reviewer process and dispatch `devflow-verifier`, no prompt, transcript, agent, or temporary files appear in the disposable repository, the reviewer runs with ignored user configuration and an OS-enforced read-only sandbox, and the verifier reports exact command evidence.

- [ ] **Step 4: Run one Full failure-preservation scenario**

Use a disposable repository with one deliberately failing task. Confirm the external worktree is preserved and its recovery path is reported. Then repair and integrate it, run final gates, and confirm the helper removes only the integrated clean worktree and its task branch.

- [ ] **Step 5: Run one Sol XHigh whole-repository review and Luna Max verification concurrently**

Give the isolated reviewer process the design, plan, full diff, and concise gate evidence. Give the verifier the exact repository commands. Return Critical and Important findings to the owning Luna implementer, then repeat both gates.

- [ ] **Step 6: Commit and push the certified plugin**

```bash
git add -A
git commit -m "test: certify codex dev flow"
git push origin main
```

Skip the commit when the certification step creates no tracked changes. Push all prior reviewed commits.

### Task 6: Remove superseded workflow machinery from Expand

**Files:**
- Delete: `/home/gimhoff/projects/expand/.claude/agents/*.md`
- Delete: `/home/gimhoff/projects/expand/.codex/agents/*.toml`
- Delete: `/home/gimhoff/projects/expand/.codex/config.toml`
- Delete: `/home/gimhoff/projects/expand/scripts/sync-agents.ts`
- Delete: `/home/gimhoff/projects/expand/scripts/sync-agents.test.ts`
- Modify: `/home/gimhoff/projects/expand/AGENTS.md`
- Modify: `/home/gimhoff/projects/expand/package.json`
- Modify: `/home/gimhoff/projects/expand/.husky/pre-commit`
- Modify: `/home/gimhoff/projects/expand/.github/workflows/ci.yml`
- Modify: architecture tests, Effect-audit fixtures, versioning documentation, and `REVIEW.md` only where they reference the deleted machinery.

**Interfaces:**
- Preserves: repository-specific code-comment and architecture policy.
- Removes: project-local agent definitions, synchronization commands, and orchestration-specific checks.

- [ ] **Step 1: Record the dirty-worktree baseline and deletion allowlist**

Capture `git status --short`, staged and unstaged diffs, and the exact tracked files matching agent synchronization. Preserve all unrelated existing edits, including overlapping `REVIEW.md` changes.

- [ ] **Step 2: Run pre-removal focused gates**

Run the existing sync-agent test and architecture tests to establish their current behavior. Record failures caused by already-deleted inventory files separately from the agent-migration baseline.

- [ ] **Step 3: Remove only superseded machinery**

Remove the listed agent files, sync implementation/tests, `agents:sync` and `agents:check` scripts, pre-commit and CI invocations, Effect-audit catalog entries, architecture expectations, and orchestration prose. Do not remove repository-specific engineering rules or unrelated Codex configuration owned by the user; if `.codex/config.toml` contains newly added unrelated settings at execution time, preserve those settings instead of deleting the file.

- [ ] **Step 4: Prove the migration is complete and scoped**

Run an exact `rg` scan for deleted paths, commands, role names, and synchronization identifiers. Compare `git diff --name-status` with the allowlist and inspect overlapping user-edited files line by line.

- [ ] **Step 5: Run Expand verification**

Run focused architecture tests, `npm run effect:audit`, `npm run typecheck:all`, `npm run lint`, `npm run arch`, `npm run knip`, `npm run build`, and `npm test`. Distinguish any reproducible pre-existing React test-environment failures from migration regressions.

- [ ] **Step 6: Leave migration changes uncommitted unless requested**

Report the exact removal, verification evidence, pre-existing failures, and the requirement to use a fresh Codex task. Do not stage or commit the user's existing Expand work without explicit authorization.

## Plan self-review

- Every design requirement maps to a task.
- The plugin is fully working and fresh-session certified before Expand loses its local agents.
- Sol review calls are bounded to coherent task diffs and one final integrated review.
- Token-heavy exploration, tests, implementation, verification, and forward-test repetitions use Luna Max.
- Installer and worktree removal paths have explicit ownership predicates and failure preservation.
- No task introduces the cancelled repository-cleaning feature.
