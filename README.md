# ExpSkill

ExpSkill is a private Codex plugin with thirteen independent skills and one
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

The OpenCode target builds generated agents, commands, the catalog, and copied
assets into receipt-owned state under the configured state home, then links
those regular files into the OpenCode config directory. It does not require or
create generated mirrors in the repository. Inspect that plan with
`python3 scripts/install.py --target opencode --dry-run`; remove it with
`python3 scripts/install.py --target opencode --uninstall`.

The plugin includes a `SessionStart` hook that applies Unslop to prose in root
conversations. Codex will not run a new or changed plugin hook until you review
and trust it. Inspect it through `/hooks`, then start a new conversation.

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
only from the agents directory. Link them with the installer in agents-only
mode:

```bash
python3 scripts/install.py --agents-only
python3 scripts/install.py --agents-only --uninstall
```

Agents-only mode never calls the plugin CLI. It only creates the profile
links and records them in its own receipt, and a later full
`python3 scripts/install.py` run keeps those links while claiming the CLI
ownership it performed.
Agents-only uninstall retains the generated package and its identity in the
receipt, so reinstall and later full uninstall can still verify ownership.

Codex receipts from the older path-only format are migrated on install or
uninstall: a valid receipt authorizes only its recorded source/destination
pairs, and migration freezes the observed symlink identities before creating
private anchors. Regular files and links to other targets are preserved.
The old format cannot distinguish a same-target replacement made before that
identity checkpoint from the original link; it retains its historical
path-based ownership contract for that one migration. Newly written receipts
use explicit identity metadata and do not grant ownership to unproven entries.
A later same-target replacement is preserved, along with receipt evidence
and its package dependency until that replacement is removed.
Matching profile symlinks that already exist at installation are also recorded
as dependencies, without granting permission to delete them. Full uninstall
retains their package and receipt until those links are removed or redirected.
This includes links spelled through directory aliases. New installations also
record the generated package's identity before publication. Refresh and full
uninstall retain a package whose receipt has no package identity, including
older marker-only packages; move that package aside before retrying installation
or teardown. Install retries finish pending package retirement before publishing
a replacement. A failed install retains the generated package's recorded identity
when clearing its install journal, even if no links or CLI registrations remain.
Upgrades freeze old receipt ownership before package construction, so a failed
upgrade retains its CLI ownership flags and profile evidence. A restored package
keeps its complete prior identity across rollback and process exit. Receipt and
journal publication and cleanup are bound to the files read or published by the transaction;
replacement files are preserved and reported for reconciliation before retry.
Interrupted record publication recovers before state files are read. Receipt and
journal records retain a hard-link identity witness until retirement, including
through rollback to older records and publication cleanup, so retry rejects a
copied replacement. Marker creation and syncing use the directory descriptor
retained by the builder before publication. Failed publication cleanup checks
the exact temporary file and candidate directory identities, preserving and
reporting replacement entries for reconciliation.
The marketplace builder removes only empty temporary containers. A failed build
can leave a nonempty `.codex-marketplace-build-*` directory for manual inspection;
cleanup never recursively deletes a replacement at that pathname.
Fresh profile symlinks are constructed in a private creation directory and
hard-linked into staging with their identity already known. The installer lock
covers private creation and retirement names; arbitrary concurrent writes inside
those private directories are outside that boundary.
If a process exits after creating a staged profile symlink but before recording
its inode identity, install and uninstall preserve that unproven stage and stop.
This also applies to legacy restoration. Reconcile the reported staging path and
any adjacent `.create` directory manually before retrying; a matching target
alone cannot prove that the symlink belongs to the interrupted install.
If a failed legacy migration restores CLI registrations to a recovery package,
its migration journal retains that package's frozen identity. Retry verifies
and reuses the recovery package; a copied marker cannot authorize a replacement.
Older recovery journals without a frozen directory identity fail closed and
retain the recovery package and journal for manual reconciliation.

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

## Skills

Invoke a skill directly when you know what you want:

- `$brainstorm` explores uncertainty without writing production code.
- `$plan` produces an ordered, reviewable implementation-and-proof design with observable criteria, behavior, planned tests, and verification intent.
- `$design` creates grounded production-intended UI components and publishes a Yodea preview for responsive, stateful review after blocking quality gates pass. Preview links accompany approval requests, handoffs, and PR descriptions and stay available through PR review.
- `$setup-ui-testing` establishes or records one reusable, project-native
  isolated UI inspection method without redesigning production UI.
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

`$brainstorm`, `$setup-ui-testing`, `$correct`, `$review`, `$review-loop`, and
`$test` remain
independently usable
without requiring the graph. Brainstorm produces a confirmed Concept Brief,
Setup UI Testing records the reusable project-specific inspection method, and
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

The focused contract and runtime suites cover the currently implemented skill,
installation, security, concurrency, recovery, and lifecycle boundaries.

The live certification and release chain is still being hardened. The phase
surface and installer are available on the integration branch, but the plugin
must not be called release-certified until every gate in
[`docs/release-policy.md`](docs/release-policy.md) passes.
