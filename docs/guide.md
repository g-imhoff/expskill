# ExpSkill guide

ExpSkill is a private Codex plugin with fourteen independent skills and one
optional lifecycle router. The same skill base also ships as the
`opencode-expskill` npm package for OpenCode and as an Agent Plugins package for Hermes.

## Quick install

Use the [launcher in the README](../README.md) from any directory, choose Codex,
OpenCode, or Hermes, and let the selected CLI install its published package.
The private GitHub download requires authenticated `gh` access. Codex and Hermes
also require GitHub SSH access and Git. Codex requires Python 3 to read its CLI
response and install the seven packaged agent profiles.

Codex profiles go to `${CODEX_HOME:-$HOME/.codex}/agents`. Differing files and old
symlinks are preserved in an `expskill-backup-*` directory there before replacement.
Unrelated profiles are left in place. Review and trust the hook through `/hooks`,
then start a new session. The installer stops on CLI conflicts and prints removal
commands for a deliberate reinstall. It does not automatically update existing
registrations. The manual commands below are for building from a source checkout.

## Install and validate

From the repository root:

```bash
python3 scripts/validate.py
```

## Install through the Codex plugin CLI

Build the Codex marketplace outside the checkout, then pass that generated
directory to the Codex CLI:

```bash
codex_marketplace="$(mktemp -d)/expskill-marketplace"
python3 scripts/build_codex_marketplace.py "$codex_marketplace"
codex plugin marketplace add "$codex_marketplace"
codex plugin add expskill@expskill
```

The build combines the canonical content with the Codex adapters as regular
files. The authored checkout is not a marketplace because Codex copies one
plugin tree and does not merge a separate adapter tree into it.

That CLI flow installs the skills, their Codex UI metadata, and the hook. The
seven agent profiles cannot ride along because Codex loads custom profiles
only from the agents directory. Copy them from the built marketplace package:

```bash
mkdir -p ~/.codex/agents
rm -f ~/.codex/agents/expskill-designer.toml ~/.codex/agents/expskill-explorer.toml \
  ~/.codex/agents/expskill-implementer.toml ~/.codex/agents/expskill-planner.toml \
  ~/.codex/agents/expskill-review.toml ~/.codex/agents/expskill-spec.toml \
  ~/.codex/agents/expskill-test-engineer.toml
cp "$codex_marketplace/plugins/expskill/agents/"*.toml ~/.codex/agents/
```

Removing the seven paths first matters: the retired installer left symlinks
there, and copying over a symlink writes through it instead of replacing it.
The `rm -f` list names exactly the seven installed profiles, so unrelated
files are untouched — but back up any custom content under those same names
first. Start a new Codex session after installation so the skills and agent
profiles are rediscovered. To remove:

```bash
codex plugin remove expskill@expskill
codex plugin marketplace remove expskill
rm -f ~/.codex/agents/expskill-designer.toml ~/.codex/agents/expskill-explorer.toml \
  ~/.codex/agents/expskill-implementer.toml ~/.codex/agents/expskill-planner.toml \
  ~/.codex/agents/expskill-review.toml ~/.codex/agents/expskill-spec.toml \
  ~/.codex/agents/expskill-test-engineer.toml
rm -rf "$codex_marketplace"
```

The `~/.codex/agents` directory itself is left in place; only the
`expskill-*.toml` copies are removed.

To update, rebuild and reinstall (the CLI has no per-plugin upgrade; the
`marketplace upgrade` command only refreshes Git marketplaces, and this one
is a local directory):

```bash
codex_marketplace="$(mktemp -d)/expskill-marketplace"
python3 scripts/build_codex_marketplace.py "$codex_marketplace"
codex plugin remove expskill@expskill
codex plugin marketplace remove expskill
codex plugin marketplace add "$codex_marketplace"
codex plugin add expskill@expskill
rm -f ~/.codex/agents/expskill-designer.toml ~/.codex/agents/expskill-explorer.toml \
  ~/.codex/agents/expskill-implementer.toml ~/.codex/agents/expskill-planner.toml \
  ~/.codex/agents/expskill-review.toml ~/.codex/agents/expskill-spec.toml \
  ~/.codex/agents/expskill-test-engineer.toml
cp "$codex_marketplace/plugins/expskill/agents/"*.toml ~/.codex/agents/
rm -rf "$codex_marketplace"
```

Install a published release with the Codex CLI (each tag publishes the
built marketplace to the `codex-dist` branch; resolve its exact SHA first):

```bash
sha="$(git ls-remote git@github.com:g-imhoff/expskill.git refs/heads/codex-dist | cut -f1)"
codex plugin marketplace add git@github.com:g-imhoff/expskill.git --ref "$sha"
codex plugin add expskill@expskill
```

The plugin includes a `SessionStart` hook that applies Unslop to prose in root
conversations. Codex will not run a new or changed plugin hook until you review
and trust it. Inspect it through `/hooks`, then start a new conversation.

State left by the retired installer (`$XDG_STATE_HOME/expskill` receipts and
journals, `codex-marketplace` and recovery packages) is inert without it;
remove those directories by hand if they exist. The retired Codex agent
symlinks are not inert either: besides the seven current profiles, the
installer may have left `expskill-critical-reviewer.toml`,
`expskill-implementer-high.toml`, `expskill-reviewer.toml`,
`expskill-verifier.toml`, and `expskill-verifier-low.toml` in
`~/.codex/agents` pointing at removed state — delete those five as well.
The retired opencode links are not inert: entries the installer created
under `$OPENCODE_CONFIG_DIR` (`skills/`, `commands/`, `agents/`, `plugins/`)
and the state-home `expskill/opencode-artifact` directory keep pointing at
removed state, so delete those expskill entries and the artifact directory
by hand as well.

## Install through the opencode plugin CLI

Build the native npm artifact outside the checkout, publish it, then install
it with the opencode CLI:

```bash
artifact_root="$(mktemp -d)/opencode-expskill"
python3 scripts/build_opencode_package.py "$artifact_root"
pack_dir="$(mktemp -d)"
npm pack "$artifact_root" --pack-destination "$pack_dir"
npm publish "$pack_dir"/opencode-expskill-*.tgz
opencode plugin add opencode-expskill
```

To remove:

```bash
opencode plugin remove opencode-expskill
rm -rf "$artifact_root" "$pack_dir"
```

To update:

```bash
opencode plugin check
opencode plugin update opencode-expskill
```

## Source layout

`plugins/expskill/content` is the only authored source for shared skill and
agent Markdown. It also owns shared runtime prose and policies.

`plugins/expskill/codex` contains Codex-only mechanics. Its
`skill-adapters/` tree holds `openai.yaml` UI metadata, while `hooks/` and
`agents.json` define Codex behavior. It contains no copy of a skill or agent
Markdown file.

`plugins/expskill/opencode` contains OpenCode-only mechanics such as model and
permission overlays, package metadata, and JavaScript plugins. The renderers
and builders combine each adapter with `content/` into a complete host
package. Generated packages stay outside the checkout.

`plugins/expskill/hermes` contains Hermes-only mechanics: the Agent Plugins
v1 `plugin.json` manifest and the role/sandbox/model-policy overlay in
`agents.json`. The Hermes renderer and builder combine them with `content/`
into a package installable with `hermes plugins install`. There is no
`install.py` target for Hermes.

## Skills

Invoke a skill directly when you know what you want:

- `$brainstorm` explores uncertainty without writing production code.
- `$plan` produces an ordered, reviewable implementation-and-proof design with observable criteria, behavior, planned tests, and verification intent.
- `$design` creates grounded production-intended UI components and publishes a Yodea preview for responsive, stateful review after blocking quality gates pass. Preview links accompany approval requests, handoffs, and PR descriptions and stay available through PR review.
- `$setup-design` establishes or records one reusable, project-native
  isolated design-sketch method without approving feature sketches.
- `$setup-test` establishes or records the project's test-method matrix
  (test types to commands, canary versus full suite, human versus agent
  execution, routine post-setup path) without running routine tests.
- `$implement` coordinates isolated TDD workers, independent review and spec
  gates, corrections, local integration, and final whole-branch gates.
- `$correct` repairs concrete bugs within the existing design and asks for a
  choice before structural or breaking changes.
- `$review` adversarially inspects a user-defined code scope and saves an
  evidence-backed Markdown report with ratings, without fixes or fix advice.
- `$review-loop` runs adversarial category reviewers, fixes each top issue,
  and repeats until every score reaches 9 of 10 before a PR.
- `$test` exercises already-implemented behavior through realistic composed
  product paths and reports revision-bound evidence without repairing production
  code.
- `$skill-builder` creates or improves one exact agent skill through evidence-gated research, trials, review, and verification.
- `$unslop` rewrites prose to remove common AI tells and remains directly
  invokable even when the conversation hook is unavailable.
- `$grill-me` stress-tests a connected set of user-owned decisions through a
  fact-grounded interview and mandatory final confirmation.
- `$autonomous-run` acts as an AI user on your behalf. It launches one initial
  `$use-expskill` conversation and follows the workflow's instructions and
  conversation transitions to a review-ready draft PR.
  It answers questions and confirms decisions within your scope and constraints.
  After the workflow delivers the PR, it runs `$review-loop` in its own
  conversation before handing the result to you for review and merge.

Invoke `$use-expskill` when you want the plugin to select and explain the next
skill. It normally opens one skill per transition. For unresolved UI work, it
may launch one Plan session and one Design
session concurrently from the same baseline. It coordinates against the
canonical Plan Graph when one exists, validates revision-bound receipts, and
preserves the implementation gates. It is the only skill that may activate
implicitly.

There is no setup gate. Neither `$setup-design` nor `$setup-test` is
auto-loaded or router-selected merely because a setup record is absent or
invalid. If UI or test work arrives with no setup record and no explicit
setup intent, the router reports "not configured, run `$setup-design` /
`$setup-test`" and stops before Plan, Design, or feature implementation.
A ready `.expskill/setup-design.md` or `.expskill/setup-test.md` record is
read by its owning skill only and is never a routing precondition. Those
tracked records and their methods persist across worktrees and runs.

When a routed phase is blocked by several connected, consequential decisions
that only the user can make, `$use-expskill` may offer `$grill-me`. It waits for
explicit consent and never launches that interview automatically.

Direct skill invocation never loads the entire pipeline. For example:

```text
Use $brainstorm to compare storage approaches for this feature.
Use $plan to turn the accepted API decision into bounded tasks.
Use $setup-design to establish this project's reusable isolated design-sketch method.
Use $implement to execute this accepted implementation work.
Use $correct to repair this bounded regression before the branch is merged.
Use $review to inspect the changes between this branch and main and save a report.
Use $review-loop to drive this change to 9 of 10 before opening a PR.
Use $test to exercise this implemented change through realistic product behavior.
Use $skill-builder to create or improve one exact agent skill with retained evidence.
Use $unslop to rewrite this explanation in a natural voice.
Use $grill-me to stress-test these connected product decisions.
Use $autonomous-run to carry this idea to a review-ready draft PR.
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

`$brainstorm`, `$setup-design`, `$setup-test`, `$correct`, `$review`, `$review-loop`, and
`$test` remain
independently usable
without requiring the graph. Brainstorm produces a confirmed Concept Brief,
Setup Design records the reusable project-specific sketch method, Setup Test
records the project test-method matrix, and
Test returns evidence for the implemented behavior it exercised. `$use-expskill`
owns optional transition selection; the individual skills do not silently open
the whole pipeline.

Correct checks its own repairs. When a repair needs a consequential choice,
it explains a sound limited fix and its limitations, or offers Brainstorm.

Review runs through the invoking agent and leaves Implement's internal reviewer
alone. Reports live in the installed Review skill's `tmp/reports/` directory,
outside every repository. Filenames include the date and time through seconds.
The user deletes reports manually. Review does not apply corrections or start
another skill.

`$review-loop` coordinates per-category reviewers and fixers as the pre-PR gate.
It runs up to three review cycles and passes when every category scores at
least 9 of 10.

The live certification chain is unfinished. Its required boundary must use
fresh `codex exec --ephemeral --ignore-user-config --json` sessions in
disposable repositories. It must retain evidence binding the installed skill,
invocation, loaded skill and helper, Plan Graph observations, skill results,
commands, and repository state, and it must fail closed on missing or
inconsistent evidence.

## Repository commands

```bash
python3 scripts/validate.py
python3 -m pytest -q tests/test_codex_source_correction.py tests/test_worktrees.py
python3 -m pytest -q tests/test_brainstorm_contract.py tests/test_plan_contract.py tests/test_plan_graph.py tests/test_plan_graph_stage10.py
python3 -m pytest -q tests/test_opencode_contract.py tests/test_opencode_package.py tests/test_opencode_runtime.py
python3 -m pytest -q tests/test_hermes_contract.py tests/test_hermes_package.py
```

The networked integration suite obtains pinned Codex `0.153.4` and opencode
`1.18.29` executables into the repository-local `.testbin` directory, verifies
the official release archive SHA-256, retains or reacquires that verified
archive, checks the expected executable member before reusing cached bytes,
exercises Codex through the real Codex CLI marketplace and plugin commands
and exercises OpenCode through npm pack/install plus file-URI config wiring,
and verifies detection. Run it in
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

## OpenCode package

The OpenCode source lives under `plugins/expskill`:
shared skills, agent profiles, and package assets are there, while native
OpenCode metadata, documentation, and plugins are under
`plugins/expskill/opencode`. The pure renderer in
`scripts/render_opencode.py` derives agent Markdown, command Markdown, and the
native runtime catalog. The explicit-output builder in
`scripts/build_opencode_package.py` materializes a self-contained npm artifact
with regular files and sorted SHA-256 provenance.

Build into a new directory, then pack that artifact:

```bash
artifact_root="$(mktemp -d)/opencode-expskill"
python3 scripts/build_opencode_package.py "$artifact_root"
npm pack --dry-run --json "$artifact_root"
```

See `plugins/expskill/content/docs/opencode.md` for the source, renderer, builder,
and plugin details.

## Hermes package

The Hermes source lives under `plugins/expskill`:
shared skills, agent profiles, and package assets are there, while the native
Agent Plugins v1 manifest and agent overlay are under
`plugins/expskill/hermes`. The pure renderer in
`scripts/render_hermes.py` derives agent Markdown from the canonical bodies.
The explicit-output builder in
`scripts/build_hermes_package.py` materializes a self-contained plugin
artifact with regular files and sorted SHA-256 provenance.

Build into a new directory, then validate that artifact:

```bash
artifact_root="$(mktemp -d)/hermes-expskill"
python3 scripts/build_hermes_package.py "$artifact_root"
hermes plugins validate "$artifact_root"
```

Install a published release with the Hermes CLI (each release publishes the
built artifact to the `hermes-dist` branch; resolve its exact SHA first):

```bash
sha="$(git ls-remote git@github.com:g-imhoff/expskill.git refs/heads/hermes-dist | cut -f1)"
hermes plugins install git@github.com:g-imhoff/expskill.git --ref "$sha"
```

To update to the latest published release:

```bash
hermes plugins update expskill
```

To remove:

```bash
hermes plugins remove expskill
```

There is no `install.py` target for Hermes.

See `plugins/expskill/content/docs/hermes.md` for the source, renderer, builder,
and plugin details.

The focused contract and runtime suites cover the currently implemented skill,
installation, security, concurrency, recovery, and lifecycle boundaries.

The live certification and release chain is still being hardened. The phase
surface is available on the integration branch (the quick installer delegates
to the host plugin CLIs), but the
plugin must not be called release-certified until every gate passes.
