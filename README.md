# ExpSkill

ExpSkill is a private Codex plugin with nine independent skills and one
optional lifecycle router. The same skill base also ships as the
`opencode-expskill` npm package for opencode.

## Install and validate

From the repository root:

```bash
python3 scripts/validate.py
python3 scripts/install.py
python3 scripts/install.py --target opencode
```

Start a new Codex session after installation so the skills and linked agent
profiles are rediscovered. Use `python3 scripts/install.py --dry-run` to inspect
the planned changes and `python3 scripts/install.py --uninstall` to remove only
repository-owned installation state. The Codex and opencode installers keep
separate receipts and separate destinations, so both targets can be installed
at once.

The plugin includes a `SessionStart` hook that applies Unslop to prose in root
conversations. Codex will not run a new or changed plugin hook until you review
and trust it. Inspect it through `/hooks`, then start a new conversation.

## Install through the Codex plugin CLI

The plugin itself installs through plain Codex commands with no script
involved. Point the marketplace at a local checkout or at a reachable Git
source, then add the plugin:

```bash
codex plugin marketplace add /path/to/expskill
codex plugin add expskill@expskill
```

That CLI flow installs the skills and the hook. The seven agent profiles
cannot ride along because Codex loads custom profiles only from the agents
directory, so link them with the installer in agents-only mode:

```bash
python3 scripts/install.py --agents-only
python3 scripts/install.py --agents-only --uninstall
```

Agents-only mode never calls the plugin CLI. It only creates the profile
links and records them in its own receipt, and a later full
`python3 scripts/install.py` run keeps those links while claiming the CLI
ownership it performed.

## Skills

Invoke a skill directly when you know what you want:

- `$brainstorm` explores uncertainty without writing production code.
- `$plan` produces an ordered, reviewable implementation-and-proof design with observable criteria, behavior, planned tests, and verification intent.
- `$design` creates grounded production-intended UI components and admits them to responsive, stateful review only after blocking quality gates pass.
- `$setup-ui-testing` establishes or records one reusable, project-native
  isolated UI inspection method without redesigning production UI.
- `$implement` coordinates isolated TDD workers, independent review and spec
  gates, corrections, local integration, and final whole-branch gates.
- `$test` exercises already-implemented behavior through realistic composed
  product paths and reports revision-bound evidence without repairing production
  code.
- `$skill-builder` creates or improves one exact agent skill through evidence-gated research, trials, review, and verification.
- `$unslop` rewrites prose to remove common AI tells and remains directly
  invokable even when the conversation hook is unavailable.
- `$grill-me` stress-tests a connected set of user-owned decisions through a
  fact-grounded interview and mandatory final confirmation.

Invoke `$use-expskill` when you want the plugin to select and explain the next
skill. It normally opens one skill per transition. For unresolved UI work with
a ready project UI testing setup, it may launch one Plan session and one Design
session concurrently from the same baseline. It coordinates against the
canonical Plan Graph when one exists, validates revision-bound receipts, and
preserves the implementation gates. It is the only skill that may activate
implicitly.

Before routing UI work, the router inspects the constant project-local
`.ui-harness/README.md` capability record. An absent or invalid record routes
only to `$setup-ui-testing`. A ready record can be copied with agent-only
support into the isolated Design worktree. Those temporary copies and their
evidence disappear when the accepted worktree is integrated and cleaned up.

When a routed phase is blocked by several connected, consequential decisions
that only the user can make, `$use-expskill` may offer `$grill-me`. It waits for
explicit consent and never launches that interview automatically.

Direct skill invocation never loads the entire pipeline. For example:

```text
Use $brainstorm to compare storage approaches for this feature.
Use $plan to turn the accepted API decision into bounded tasks.
Use $setup-ui-testing to establish this project's reusable isolated UI inspection method.
Use $implement to execute this accepted implementation work.
Use $test to exercise this implemented change through realistic product behavior.
Use $skill-builder to create or improve one exact agent skill with retained evidence.
Use $unslop to rewrite this explanation in a natural voice.
Use $grill-me to stress-test these connected product decisions.
```

## Workflow state and evidence

`$plan` creates the private canonical Plan Graph outside the repository. Later
skills consume its exact workflow ID, graph revision, branch, commit, work, and
proof obligations. Workers and independent judges return bound receipts; only
the coordinator validates and applies them. Direct `$plan` use stops when the
plan is ready and never emits a next-skill route. Findings, blocked work, stale
evidence, and unresolved user decisions do not advance.

During bounded parallel UI preparation, Plan remains the sole graph writer and
Design works on one isolated candidate commit. The graph cannot become ready
until Plan validates and records the approved Design receipt from that same
baseline.

`$brainstorm`, `$setup-ui-testing`, and `$test` remain independently usable
without requiring the graph. Brainstorm produces a confirmed Concept Brief,
Setup UI Testing records the reusable project-specific inspection method, and
Test returns evidence for the implemented behavior it exercised. `$use-expskill`
owns optional transition selection; the individual skills do not silently open
the whole pipeline.

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
python3 -m pytest -q tests/test_opencode_contract.py tests/test_opencode_install.py tests/test_opencode_runtime.py
```

The networked integration suite obtains pinned Codex `0.153.4` and opencode
`1.18.29` executables into the repository-local `.testbin` directory, verifies
the official release archive SHA-256, retains or reacquires that verified
archive, checks the expected executable member before reusing cached bytes,
installs both targets through the real CLIs, and verifies detection. Run it in
the required, fail-closed mode:

```bash
EXPSKILL_CLI_MODE=required python3 -m pytest -q tests/test_cli_install_integration.py
```

The checked-in manifest records the archive digests and their authoritative
release metadata URLs from the Codex and opencode GitHub release APIs. A
missing binary, unsupported platform, download failure, corrupt archive or
cache, unexpected archive layout, and any digest mismatch fails this command;
none can become a skip. The suite runs only install/list/remove and
config-startup smoke commands, never a model call.

An explicit executable override is permitted only with an independently
verified companion digest. Do not compute a digest from an untrusted file and
use it as proof. Relative overrides are resolved to a stable absolute path
before verification; a bare executable name is resolved through `PATH` at that
time:

```bash
export EXPSKILL_TEST_CODEX_BIN="/path/to/codex"
export EXPSKILL_TEST_CODEX_BIN_SHA256="independently-verified-sha256"
export EXPSKILL_TEST_OPENCODE_BIN="/path/to/opencode"
export EXPSKILL_TEST_OPENCODE_BIN_SHA256="independently-verified-sha256"
export EXPSKILL_CLI_MODE=required
python3 -m pytest -q tests/test_cli_install_integration.py
```

For a deliberately local/offline check, opt in explicitly with
`EXPSKILL_CLI_MODE=optional`; only unavailable acquisition is skippable there.
It must not be used as a substitute for required verification.

## opencode package

`packages/opencode` distributes the same ten skills to opencode as
`opencode-expskill`. The canonical skill source remains
`packages/codex/skills`, and `packages/opencode/skills` is a generated,
byte-identical regular-file mirror for npm packaging. The package adds ten thin
`/name` commands, seven permission-scoped
subagents, the Unslop session injector, and budget counters driven by the
shared execution policy. Agent descriptions and instructions render from the
canonical Codex profiles through `packages/opencode/agents.json`, which also
pins the model and reasoning effort behind one switchable provider profile.
See `packages/opencode/README.md` for the install
commands and the hook trust divergence.

The focused contract and runtime suites cover the currently implemented skill,
installation, security, concurrency, recovery, and lifecycle boundaries.

The live certification and release chain is still being hardened. The phase
surface and installer are available on the integration branch, but the plugin
must not be called release-certified until every gate in
[`docs/release-policy.md`](docs/release-policy.md) passes.
