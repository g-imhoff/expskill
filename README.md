# ExpSkill

ExpSkill is a private Codex plugin with thirteen independent skills and one
optional lifecycle router.

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

The plugin includes a `SessionStart` hook that applies Unslop to prose in root
conversations. Codex will not run a new or changed plugin hook until you review
and trust it. Inspect it through `/hooks`, then start a new conversation.

## Skills

Invoke a skill directly when you know what you want:

- `$brainstorm` explores uncertainty without writing production code.
- `$plan` produces an ordered, reviewable implementation-and-proof design with observable criteria, behavior, planned tests, and verification intent.
- `$design` creates grounded production-intended UI components and admits them to responsive, stateful review only after blocking quality gates pass.
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
```

The focused contract and runtime suites cover the currently implemented skill,
installation, security, concurrency, recovery, and lifecycle boundaries.

The live certification and release chain is still being hardened. The phase
surface and installer are available on the integration branch, but the plugin
must not be called release-certified until every gate in
[`docs/release-policy.md`](docs/release-policy.md) passes.
