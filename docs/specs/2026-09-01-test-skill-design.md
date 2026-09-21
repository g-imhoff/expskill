# Test Skill Design

## Status

This specification records the progressively confirmed design for the new public
`$test` skill. It defines the standalone target and the later stack migration
that `$use-expskill` must perform. Implementation has not started.

## Purpose

`$test` exercises an already implemented exact repository head through
realistic, composed product behavior. It closes the evidence gap between
implementation-coupled TDD and confidence that the assembled application still
works when used through a real UI, API, CLI, library consumer, installer, or
equivalent client and its material dependencies.

Implement remains responsible for production code and node-local TDD. Test is
responsible for realistic integration/E2E exercise, application hand testing,
bounded exploration, high-quality durable test additions, and an honest
revision-bound result. It never repairs production code or certifies its own
whole-branch review/spec compliance.

## Public boundary and triggering

`test` is a directly invocable explicit-only public skill. It runs when the user
invokes `$test` or when a later authorized coordinator deliberately selects it
after implementation. It never depends on `$use-expskill`, a Plan Graph, or a
prior lifecycle session in order to run directly.

Every completed implementation enters Test. Even an apparently tiny change is
grounded by Test before an exemption decision; no upstream skill or router may
casually skip it. Test returns `EXEMPT` only when exact-revision repository
evidence proves the change cannot affect runtime behavior, interfaces,
configuration, dependencies, schemas, data, security, packaging, deployment,
generated artifacts, test validity, or a user/client journey. Comments,
formatting, and non-runtime metadata are representative candidates. Diff size,
convenience, time pressure, and green unit tests are not exemption evidence.

Test does not activate for test planning or framework selection, production
implementation, implementation-coupled TDD, source review, specification
judging, one exact-command replay, testing-tool research, branch integration,
remote delivery, or merge work.

## Package architecture

The selected architecture is one adaptive public skill, two declarative
supporting contracts, four deterministic protocol helpers, and one evidence
finalizer:

```text
plugins/expskill/content/skills/test/
├── SKILL.md
├── references/
│   ├── quality-rules.json
│   └── evidence-contract.json
└── scripts/
    ├── append_ledger.py
    ├── bootstrap_run.py
    ├── finalize_evidence.py
    ├── freeze_charter.py
    └── record_final_action.py
```

`SKILL.md` owns grounding, adaptive scope, execution, exploration,
classification, correction boundaries, and verdicts. `quality-rules.json` is a
compact fully loaded catalog; it has no search, embedding, or retrieval layer.
`evidence-contract.json` defines the raw bundle, compact receipt, findings, and
terminal states, but the running agent does not load it: the entrypoint's
authoring contract and the finalizer own those mechanical details. Neither
reference is a hidden product skill.
`finalize_evidence.py` validates frozen Test-owned inputs, derives the bundle
and receipt, publishes them atomically, and performs terminal preflight. It is
not a testing engine: it never discovers scope, selects or executes product
actions, classifies findings, or chooses the result.
`bootstrap_run.py` securely allocates the single private run root, derives
portable UTC deadline values and the named branch, emits the complete
first-party inventory plus bounded text contents, and seals the immediate
evidence parent against unbootstrapped sibling roots between allocations.
`freeze_charter.py` derives
the immutable revision identity. `append_ledger.py` validates a complete
headless candidate batch before it can become immutable evidence, derives every
entry head from the frozen charter, and atomically appends it.
`record_final_action.py` executes only a caller-selected literal argv without a
shell, compares it to a predeclared exact-text or SHA-256 predicate, retains the
unedited combined output, and performs predeclared narrow test-runtime cleanup
and revision/source-integrity checks. It derives revision identity from the
frozen charter instead of accepting copied identity fields. Its `handoff` mode
authenticates one run root, validates and composes the conditional candidate,
records the final action, and immediately finalizes a match without returning
control between stages. These helpers select no scope, action, oracle, finding,
or result and therefore are not test engines.

The rejected alternatives are a monolithic entrypoint, which would waste
context and resist maintenance, and a script-driven test engine, which would
become a rigid second testing framework. The narrow finalizer replaces only
bespoke serialization and preflight; runtime judgment remains adaptive.

## Repository grounding and preconditions

The main agent first binds the run to the canonical repository, current branch
and full head commit, accepted change boundary, relevant source and callers,
state/effects, interfaces and consumers, dependencies, project instructions,
existing tests and commands, startup path, available clients, environment, and
prior evidence. A Plan Graph, implementation receipts, approved Design
deliverables, direct brief, or prior finding is consumed when present but is not
required for direct Test.

Grounding begins with one parallel batch of three common calls and uses at most
four pre-charter command calls in total.
The shipped Python root bootstrap is the only run-root allocator. It captures
allocation, portable UTC start and cutoff values, and the literal current branch
with `git branch --show-current`; it never infers a branch name or depends on
shell-specific timestamp or random-ID expansion. It prints the complete sorted
first-party inventory obtained from Git before bounded contents—not only names
or search matches. The already-loaded
`.agents/skills/test/**` support package is the sole excluded path; product
directories are never selected by an allowlist. When the remaining bounded
first-party text fits the call output, it prints every file. Otherwise it follows
direct first-party client, caller, command, and harness references in the same
call instead of filtering only by feature name. Test executes only literal
commands and paths discovered in that repository evidence; it never guesses one.
The bootstrap creates and hardens the owned evidence parent and one unpredictable
private root before their first file write. It leaves the exact root writable at
`0700` but seals the parent at `0500`; only another bootstrap invocation may
temporarily reopen it to allocate another legitimate root. Every immediate
evidence child therefore has one successful bootstrap lineage, and callers use
the returned path byte-for-byte rather than creating or repairing siblings. The
appender and recorder likewise reject non-owned or non-private evidence inputs.
Separate standalone literal direct calls run `git rev-parse HEAD`, exactly
`git show --no-ext-diff --no-renames --format=fuller --stat --patch HEAD`, and
the root bootstrap. The Git calls contain no second command, shell chain,
branch query, or inner wrapper. These three results keep revision and change
evidence independently observable and determine an immediate exemption fork.
If exemption is proven, Test skips the quality catalog and every unit, harness,
product-journey, and behavioral-probe command. It prepares the exempt charter
from revision/reachability evidence and may reserve at most one discovered
read-only non-behavioral description command for terminal recording. If
exemption is not proven, Test loads the complete quality catalog as the fourth
grounding call. No further grounding call follows.

Repository-local facts are discovered autonomously. The user is asked only for
an unavailable credential, external or consequential authority, destructive
effect, or genuinely subjective oracle that cannot be obtained from accepted
behavior and product evidence.

Ordinary execution uses an isolated, resettable, non-production environment and
minimum synthetic data. Customer and production data are prohibited. Test-side
commits require an eligible non-protected feature branch without unrelated
dirty work. Test does not create branch topology, move integration targets,
absorb existing changes, or commit on a protected branch.

## Adaptive three-ring scope

Every non-exempt run constructs the smallest credible scope from repository
impact rather than a universal checklist:

1. **Changed behavior:** exercise the implemented outcome through its real
   client and material backend dependencies.
2. **Affected neighbours:** exercise behaviors identified from relevant
   callers, consumers, state, effects, and dependencies.
3. **Application canary:** run a short representative whole-product journey to
   detect broad regression.

When repository evidence exposes a distinct literal canary action, Test selects
and runs it instead of treating the changed-behavior action as that canary. A
single action may cover both rings only when no separate canary action is
declared or discoverable and it genuinely exercises a representative
whole-product path.

The ordinary changed-behavior observation is protected and executes before
terminal preparation; the final repetition is never its sole required action.
Changed behavior, neighbours, a distinct canary, bounded exploration,
repository-required suites, and required diagnostic or repaired-harness
confirmation take precedence over auxiliary description probes. A `describe`
action is dropped first when a real journey already binds the consumer surface.

Relevant existing automated suites run when available. Focused checks may run
first for fast feedback, but repository-required suites are not replaced by
manual confidence. Manual coverage remains bounded to the rings rather than an
exhaustive replay of the product.

Each selected action has one material purpose. Test does not invoke every
discoverable capability, describe, readiness, preflight, or harness command:
it omits a probe when a required suite or real journey supplies the same
evidence, and retains one only for an otherwise unproven prerequisite.
An advertised-surface `describe` remains material when it is the only public
binding from one composed journey to named logical layers.
The complete run has at most eight semantic actions, including exactly one
final action and at most seven ordinary actions. A coherent repository suite or
product journey may contain several compatible commands and satisfy several
oracles without being artificially split.
Before charter freeze, Test budgets the worst-case executed branch rather than
only the happy path and reserves one ordinary-action slot for every permitted
conditional diagnostic action. When a reversible local prerequisite may be
unavailable, its observed pre-recovery status probe and literal recovery
command are reserved as two distinct ordinary actions. Configuration, request
text, and an expected initial state cannot replace that observation. If the
pair would overflow the budget, Test removes a redundant suite or probe before
charter freeze, or chooses a coherent suite or journey; terminal preflight
never discovers that overflow.

Backend-only work is not exempt from product exercise. When the system has a
user-facing client, Test checks that it still works and observes its integration
with the changed backend. A product without a graphical UI is exercised through
its real API, CLI, consumer harness, install/upgrade path, or equivalent public
surface.

In a controlled or synthetic repository, a repository-declared public fixture
client is the real executable consumer surface for the represented behavior.
Test exercises every declared layer through that client without inventing an
absent browser, server, or database requirement. In a production repository,
concrete clients and dependencies that actually exist remain required.

## Mandatory bounded exploration

Every non-exempt run includes one short exploratory variation against the
running product. The main agent observes relevant navigation, rendering,
messages, console, network, persistence, recovery, and independent backend
state, then tries one realistic unplanned variation chosen from the current
risk. Exploration has a compact mission, evidence budget, teardown, and stop
condition; it cannot wander outside the selected rings.

An exploratory anomaly becomes a finding only after safe reproduction and an
explicit expected-versus-actual oracle. Test adds a durable automated check only
when the behavior is stable, valuable, and protected by every active quality
rule.

## Declarative quality catalog

Every non-exempt run loads the complete catalog in one command before selecting
checks; it never searches, samples, chunks, or rereads the catalog. The agent
does not load `evidence-contract.json` during execution.

Applicability follows the actual runtime modality, not request nouns,
filenames, constants, or modeled logical layers. A modeled UI or persistence
layer does not activate modality-specific rules without its runtime capability.
When a real public composed path and accepted outcome are proven, unavailable
optional per-layer telemetry is retained as a limitation rather than promoted
to `BLOCKED`; only an unproved material oracle blocks the result.
An unavailable prerequisite is not an unsatisfied rule: `unsatisfied` is
reserved for an observed defect and therefore forces `FAIL`. A capability that
cannot be instantiated leaves its unexercised modality conditions inactive;
the unavailable evidence is represented by a blocked action and an
`environment-blocker` finding without weakening the oracle.

Each catalog rule has this closed shape:

```json
{
  "id": "universal.deterministic-synchronization",
  "level": "hard",
  "applies_when": ["always"],
  "requirement": "Wait for observable application state, never arbitrary elapsed time.",
  "failure_prevented": "Flaky timing-dependent results.",
  "required_evidence": ["The awaited state or retrying assertion used."],
  "allowed_exceptions": []
}
```

Package validation rejects unknown fields, duplicate identifiers, invalid
levels or applicability conditions, empty requirements/evidence, and ambiguous
exceptions. For every conditional rule, the agent records `active`, `inactive`,
or `unknown` with evidence. Unknown requires investigation and can never
silently become inactive. An exception applies only when its catalog predicate
is observable and its required evidence is recorded.

The initial catalog accepts only `hard`: once its applicability condition is
true, failure to satisfy the rule blocks `PASS`. Conditionality changes when a
rule applies, never how seriously an active rule is enforced. Non-gating tips do
not belong in this integrity catalog.

Universal hard gates cover:

- exact-head, environment, behavior, and oracle binding;
- user-visible or consumer-visible outcome assertions;
- isolated state, data ownership, cleanup, and repeatability;
- state-based synchronization instead of sleeps or retry-to-green;
- realistic composition at material dependency boundaries;
- independent tests without order or shared mutable-state dependence;
- useful failure diagnostics and reproducible evidence;
- safe effects, minimum synthetic data, and secret exclusion;
- honest handling of unexplained contradictory outcomes;
- durable-test value rather than implementation-detail or duplicate coverage.

Conditional packs cover UI/client interaction, database persistence, messaging
and asynchronous work, API/consumer contracts, external services, parallel
execution, and accessibility/visual behavior. Project conventions select the
framework, commands, paths, fixture style, and naming. They cannot silently
waive an active integrity rule.

Representative conditional requirements include semantic/user-facing locators
and assertions for UI work; production-equivalent database engines and real
migrations where persistence semantics matter; eventual assertions,
idempotency, and real broker behavior for messaging; consumer/provider boundary
evidence for contracts; sandboxed service behavior and explicit failure paths
for external systems; unique data and environment isolation for parallel runs;
and scoped keyboard, semantic, viewport, theme, motion, or visual evidence when
those claims are material.

## Execution model

After grounding and scope selection, Test creates a compact conceptual charter
preparation containing accepted behavior, three-ring scope, oracles, and workflow
ancestry. Before the first product/runtime action, it writes private
`charter-preparation.json` with schema `test-charter-preparation.v1` and invokes
the shipped charter freezer once. The helper derives canonical repository,
branch, exact HEAD, and run ID and exclusively publishes `charter.json` plus an
empty `test-action-ledger.v2`; agents never transcribe identity fields or
hand-author those outputs. The charter is frozen once execution begins.

The entrypoint carries one stable, machine-readable authoring contract for the
complete closed input protocol: exact schema literals and object keys, enums,
cardinalities, uniqueness and reference rules, root/run/head/path/artifact
bindings, reserved IDs, derived RFC 8785 charter binding, catalog order, terminal
unknown prohibition, exemption shape, and minimum result support. Contract
tests parse that block, compare every constraint map to executable-aligned
constants and fixtures, and exercise each constraint family against real
validation. Every non-exempt result requires at least one material oracle. A
machine-readable invariant distinguishes blocked prerequisites from
unsatisfied-rule defects so candidate preparation cannot accidentally convert
`BLOCKED` into `FAIL` support. A planned required action missing from the ledger
is unavailable evidence, not
an unknown reference: it forbids `PASS`, supports `BLOCKED` when no defect is
proven, and may accompany `FAIL` only when other retained evidence proves the
defect. Present required actions exist, bind back to their oracle, and name at
least one retained artifact. Agents treat the implementation as opaque and
never inspect it to rediscover the protocol.

Execution combines focused integration/E2E checks, relevant existing suites,
the three-ring application exercise, and bounded exploration. Independent
checks may continue after a failure when doing so remains safe and useful.
Checks that depend on a failed prerequisite stop to avoid cascade noise.
Immediately after each sequential action or parallel batch, Test writes the
returned artifacts and a complete private `test-ledger-batch.v1` candidate. The
shipped appender validates the whole candidate before publication, derives its
head from the frozen charter, atomically appends it to `ledger.json`, and consumes
the candidate. Test never edits `ledger.json` directly and never rewrites a
published entry. A rejected candidate leaves the ledger byte-for-byte unchanged
and remains correctable.
The sole exception is the exact conditionally predeclared final entry described
below; it becomes authoritative only when its predicate matches.
Every material product or suite command is its own literal direct command call.
Its tool-call command string is exactly the discovered product argv, with no
shell prefix/suffix, pipeline, redirection, `tee`, or capture wrapper. Test copies
returned stdout/stderr to artifacts only in the following file-change batch, so
argv, output, and exit code remain independently observable. Before that write,
an observation-capture gate requires the actual returned output promised by the
oracle; exit zero alone cannot replace it. Missing, empty, truncated, or
unparseable output is never reconstructed from source, fixture data, expected
text, or another call. This gate applies equally to success, defect, and blocker
proof. When replay is safe and semantically valid, Test immediately repeats the
exact literal command once with nothing intervening. For a parallel wave, Test
waits only for already-started siblings, then makes one repair-only parallel
batch with one literal replay per unusable sibling before any ledger write or
terminal preparation. That transport replay is
the same selected semantic action, so it creates no second ledger entry and
consumes no additional semantic-action slot. If replay is unsafe or its output
is also unusable, Test records `BLOCKED`.
Ordinary execution uses at most two real
dependency waves. Each wave launches all independent charter-selected changed,
neighbour, canary, exploration, and required-suite calls together before any
artifact or ledger write. It never serializes independent execution by recording
one result before launching the next. After all calls return, it retains separate
outputs and appends the batch in one file-change action. One semantic entry may
group commands with one purpose. The final action remains separate.

A charter-predeclared reversible local recovery is one closed, ordered segment
of the first wave. When its probe exactly matches the expected unavailable
outcome, the frozen literal recovery command is the next tool call, with no
commentary, evidence authoring, reread, replanning, or deadline deliberation
between them. Test records both actions together after recovery; dependent
journeys then occupy the second wave.

Every selected check and journey, including bounded exploration, appears at
least once as its own literal direct command. The recorder-wrapped final action
only repeats an already-observed material journey, normally changed behavior;
it never substitutes for a selected observation, diagnostic repetition, or
state transition. A hard diagnostic-completion gate runs before terminal
preparation: every required repetition must already exist as its own direct
command observation. Both sides of a contradiction therefore precede terminal
handoff. Stateful, counter-changing, and diagnostic-sequence commands are
ineligible for the final recorder; it uses another already-observed non-stateful
action.

Test observes outcomes across the client and independent state boundary rather
than trusting a success message or screenshot alone. Fixed sleeps, arbitrary
retry budgets, implementation-detail assertions, shared mutable fixtures, and
mocks that replace the very behavior under test fail the applicable quality
rules. Before execution, Test reserves 360 seconds for conditional candidate
preparation, 60 seconds for the closed terminal sprint, and at least 180 seconds
for the response: at least 600 seconds against every known outer deadline. If
no deadline is exposed, Test assumes a 780-second usable budget from initial
evidence-root allocation, conservatively excluding skill-loading overhead.
During that allocation it captures `draft.started_at` and the absolute cutoff.
It enters candidate preparation as soon as 600 seconds remain or immediately
after the ordinary action waves, whichever comes first. It finishes the
candidate with 240 seconds left, never starts terminal handoff with less than
240 seconds remaining, and completes handoff with at least 180 seconds left. It
never weakens credible scope, evidence, or teardown to manufacture the reserve.

Before the final action, Test chooses one exact observable proof predicate with
a closed outcome. It makes the last action self-recording and self-cleaning: one
tool invocation performs the product/runtime operation, writes its unedited raw
observation to the declared artifact path, and completes indispensable teardown
and source-integrity capture. If that shape is impossible, Test stops before the
action with `BLOCKED` rather than creating a post-action authoring chain.

Integrity paths contain only repository paths expected to remain clean against
the frozen HEAD. An intentionally repaired Test-owned asset is excluded from
that set while its finding and repair evidence are retained; product/application
and unchanged fixture implementation remain covered. Test never commits a
test-owned repair merely to make integrity pass. The recorder rejects a dirty
integrity path during preflight, before product execution, so Test corrects only
`final-action.json` in the same root.

Test may predeclare the final ledger entry and draft values only when every
statement is a literal consequence of that exact predicate. Those values are a
conditional candidate, not authoritative evidence, until the predicate is
observed. A mismatch can never authorize or terminalize them.

If execution returns `MISMATCH`, the same output includes a
`final_action_components` record distinguishing output-predicate, teardown, and
integrity status. Test uses that record directly instead of assuming a predicate
failure or spending another tool call rereading metadata.

After the last ordinary selected action, Test uses one file-change action to
write the conditional final `ledger-batch.json` plus private
`draft-preparation.json`, `draft-final-delta.json`, and closed
`final-action.json`, then validates and publishes the headless batch through the
appender. Direct preparation schema `test-draft-preparation.v3`
contains one complete marker-free conditional `draft_candidate` with only
`rule_applicability` omitted. It declares either `evaluate` or `exempt`, the
active catalog conditions, and grouped active-rule assessments whose evidence
names retained ledger action IDs. For `exempt`, both rule arrays are empty. The
composer treats a redundantly listed `always` as its implicit no-op, then expands
active assessments and mechanically inactive rules into
complete catalog order, using `ledger:<action-id>` evidence references. The
delta is the exact closed no-op
`{"schema_version":"test-draft-final-delta.v2","resolutions":[]}`; there are
no marker IDs or split values to reconcile. The preparation and delta files are
not evidence or terminal-finalization inputs. The ledger uses
`test-action-ledger.v2` and omits `charter_digest`; the finalizer derives the
binding from the frozen same-root charter, eliminating manual digest copying.
Check entries should use an empty `path`; harmless check source anchors are
accepted but omitted from the derived bundle. Journey paths remain non-empty.
The `test-final-action.v2` spec declares distinct raw observation and metadata
artifacts, expected exit and output predicate, integrity paths, and cleanup paths
proven absent before execution. It omits head and branch; the recorder derives
both from the frozen charter and verifies them against live Git. No standalone time,
status, integrity, or artifact-listing probe may intervene between that last
ordinary action and composition.

Stable authoring invariants are checked before the final product action:
`charter.scope.accepted_behavior` is an exact copy of
`charter.accepted_behavior`, `draft.environment` is a non-empty JSON object,
and the four finalizer-generated reserved artifact IDs never appear in
`draft.artifacts`.

At terminal handoff, the shipped recorder validates `final-action.json` and
invokes fixed draft composition before running the product action. The composer
authenticates the frozen charter and ledger and validates the supplied closed
schema, bindings, references, catalog coverage, and result support. It allows
the future final-action artifact bytes to remain absent and performs no
inference, evidence derivation, artifact digesting, or terminal publication. It
also rejects an eighth semantic action. After composition and before product
execution, the recorder cross-binds its observation and metadata output paths
to exactly one matching `draft.artifacts` entry each. Any missing, duplicated,
or mistyped binding fails before the product action. Before appender publication,
Test corrects only a rejected non-authoritative batch. After publication it may
correct an invalid preparation, delta, or final-action spec in the same root; the
frozen charter and published ledger entries never change. A new root is required
once the final product command has executed or immutable evidence itself is
invalid. The recorder creates absent private
parent directories for its declared output paths and safely hardens owned
evidence directories.

The final action never uses inline `python -c`, an inline shell, or a heredoc.
The literal product argv follows `--` on `record_final_action.py handoff`,
keeping the product operation observable while eliminating generated terminal
scripts. One command receives one authenticated root and performs compose →
record → finalize without returning control between stages. The closed terminal
sprint must finish within 60 seconds and is followed by the immediate response.
No progress commentary, discovery, authoring, teardown, integrity check,
resource reread, helper-source inspection, manual digest/schema validation,
standalone timestamp command, duplicate check, or evidence polish may
intervene. Scope, evidence, and teardown are never weakened to satisfy the time
bound; all controllable work happens earlier or inside the handoff.

Before handoff, Test completes every selected action except the declared
final action, completes every diagnostic repetition as a separate direct
command, passes both observation gates, and uses available no-write/no-cache
modes for read-only probes.
Preparation directly contains the complete conditional candidate, including
all observations and artifacts. The conditional final ledger entry,
candidate draft, output path, and predicate are frozen together.

The handoff invokes the shipped program's `compose-draft` mode internally.
Preparation and delta remain its only authored composition inputs; frozen
`charter.json` and `ledger.json` are implicit read-only inputs. It rejects raced
bindings, malformed or inconsistent supplied semantics, any v3 marker or
non-empty v2 delta, and existing output. It writes private `0600` O_EXCL staging
and atomically publishes canonical `draft.json` with no replacement.
Publication cleanup fails closed through a private `.draft-cleanup-*` quarantine
whenever inode continuity cannot prove safe removal.

If the predicate matches, the same handoff immediately invokes
`scripts/finalize_evidence.py`. It requires and hashes the actual artifact bytes,
validates charter, ledger, and draft, derives checks, journeys, digests, bundle,
and receipt, publishes terminal evidence atomically, and performs preflight.
Test never hand-authors derived values. If the predicate mismatches, Test must
not finalize the conditional candidate; it preserves the raw observation and
starts a new truthful run root without rerunning product behavior merely to
repair serialization. An error after product execution follows the same rule;
a purely pre-product authoring or schema error is corrected in the current root.

On failure, either mode emits one marker-free compact JSON diagnostic on
stdout so an agent runner can see and correct Test-owned input errors. Success
retains its distinct two-line marker-and-payload protocol.

A terminal-publication failure atomically quarantines the owned staging or
canonical terminal name when possible, retains the private `0700`
`.terminal-cleanup-*` directory, and reports `cleanup-failed`. It performs no
recursive unlink or pathname removal after ownership continuity is lost; only
an explicit single-writer, quiescent-root cleanup may remove the orphan.

A successful handoff ends with `terminal_preflight=PASS` and its compact payload
as the final tool action. The established six-field response follows
immediately, without another tool call. It copies the payload summary byte for
byte, maps payload result to `terminal_state`, and carries the exact bundle
digest, bundle path, and receipt path in probe evidence.
After terminal publication, the same recorder invocation best-effort restores
owner write access on the evidence parent so normal owned-worktree cleanup can
remove the private run roots. A failed or mismatched handoff leaves it sealed.

Before `PASS`, every selected check and journey runs successfully against the
final head. Any relevant head change invalidates prior evidence and forces a
rebind and affected rerun.

## Delegation

The main agent always performs repository grounding, scope and rule selection,
the mandatory hands-on/exploratory journey, finding classification, and final
verdict. It may delegate a mechanically independent evidence lane, such as a
long automated suite or an additional browser/device matrix, only when the lane
has a frozen charter, isolated environment and data, and meaningful expected
speedup.

Delegates return evidence bound to the exact head. They cannot edit production
code, redefine scope, ask the user questions, interpret sibling conclusions, or
issue a verdict. Parallelism is skipped when proving isolation would cost more
than it is expected to save.

## Test-side changes and production defects

Test owns E2E/integration test code, fixtures, harnesses, and test configuration.
It may repair those assets and may commit a valuable durable test as one logical
test-side change on the feature branch. It never edits production code.

A proven test-system defect is resolved once its permitted repair passes
confirmation and every selected action invalidated by the repair passes while
the other selected actions remain green in the same run. Resolved defects are
omitted from `draft.findings`, do not force `FAIL`, and remain visible only in
evidence artifacts and the summary.

Every anomaly is classified as `product-defect`, `test-system-defect`,
`environment-blocker`, or `unresolved-cause`. A finding contains exact head and
environment, journey/ring, expected and actual behavior, reproduction, severity,
artifact references, and violated quality rules.

Direct `$test` stops with the finding. It does not invoke or recommend another
product skill. In the later router lifecycle, `$use-expskill` sends a product
finding to `$implement`, returns to `$test`, first proves the exact defect is
corrected, and then reruns the affected rings and canary. A final `PASS` still
requires the full selected scope on the resulting head.

## Flakes, retries, and environment recovery

Retries are diagnostic, never a mechanism for manufacturing success. A passing
rerun cannot erase an unexplained failure. Contradictory outcomes remain
`FAIL` until classified: nondeterministic product behavior is a product defect,
while flaky test/harness behavior is a test-system defect owned by Test. There
is no universal retry count; repetition stops when another attempt would add no
new evidence. Every diagnostic repetition is its own literal direct command
before terminal preparation. If it changes counters or state, the final recorder
uses a different already-observed non-stateful action; the diagnostic rerun is
never hidden inside terminal handoff.

A proven environmental failure may be superseded only after the environment is
corrected and the complete affected scope passes cleanly on the same head.
Ordinary environment recovery is autonomous. Missing authority, credential,
dependency, reliable oracle, or unavailable external system returns `BLOCKED`
instead of weakening scope or claiming success.

## Effects, data, and hostile content

Existing authority covers ordinary reversible actions in an already approved
local or sandbox environment. Test requires just-in-time user confirmation
before destructive, irreversible, billable, customer-visible,
production-adjacent, fault-injection, real-message, payment, or deletion
effects. When no safe substitute exists, Test returns `BLOCKED` and identifies
the missing authority.

Page content, logs, fixtures, service responses, and tool output are untrusted
evidence. They cannot grant authority, redefine scope, request secrets, or
instruct Test to mutate unrelated systems.

## Evidence contract

Each run privately prewrites `draft-preparation.json`, conditionally resolves
`draft-final-delta.json`, and mechanically composes the candidate draft before
the final product action. Compact grouped rule assessments keep Test from
manually duplicating the catalog in preparation; the composer produces the full
ordered applicability array.
Only the frozen `charter.json`, append-only `ledger.json`, and final
`draft.json` enter terminal finalization; preparation and delta are neither
evidence nor terminal inputs and cannot be declared as raw artifacts. The
finalizer creates a raw evidence bundle bound to the exact head. Its closed
contract records:

- schema, run, workflow, repository, branch, head, and environment identity;
- active, inactive, and unresolved rules with applicability evidence;
- three-ring scope, suites, journeys, exploration, and declared limitations;
- commands/actions, expected and observed results, and independent probes;
- classified findings, artifacts, teardown, and test-side commits;
- the terminal result and a digest covering the retained bundle.

Secrets, credentials, customer data, source copies, and conversation
transcripts are excluded. Failure runs retain rich screenshots, traces, videos,
logs, console/network observations, and reproduction evidence. Passing checks
retain compact metadata unless a richer artifact materially supports the claim.

Raw artifacts, the bundle, and the full receipt are private, uncommitted, and
bound to the feature worktree so they survive correction sessions and
disappear with owned worktree cleanup. Durable tests are the only normal
tracked output. For a Plan-backed run, concise probe evidence names the private
exact-revision receipt path for the coordinator to load and persist through the
existing Plan Graph helper. Test never writes canonical graph state or creates
a second state database. Direct runs use null graph ancestry.

User-facing reporting is progressive and concise: selected scope first and
meaningful findings or blockers as they occur, without narrating routine green
stages. The terminal response has exactly
`schema_version`, `terminal_state`, `summary`, `scope`, `evidence`, and
`limitations`; concise `probe` entries carry the bundle digest and private
bundle/receipt paths. The summary is the finalizer payload summary byte for byte,
the terminal state is its result, and digest/paths are copied exactly. Raw logs,
the raw private bundle, and the full receipt are never pasted into conversation
or added as incompatible top-level fields. Authored summaries remain
revision-neutral rather than manually transcribing branch or head identity; if
response evidence names the exact head, it copies `payload.receipt.head`
byte-for-byte.

## Terminal states

- `PASS`: every selected automated check, journey, canary, and exploration
  obligation passed on the final head, with no unresolved active-rule failure.
- `FAIL`: a reproducible product/test-system defect, unexplained contradictory
  outcome, or unsatisfied active rule remains.
- `BLOCKED`: required evidence is unavailable because an environment,
  dependency, credential, authority, or reliable oracle cannot be obtained.
- `EXEMPT`: exact-revision evidence proves the change is genuinely tiny and
  behaviorally negligible under the strict exemption contract.

No partial, flaky, assumed, or stale state is represented as `PASS`.

## Eventual stack integration

The accepted eventual lifecycle is:

```text
$implement node work + per-node expskill-review/expskill-spec
→ local integration
→ $test
→ fresh whole-branch expskill-review + expskill-spec
→ ready for human review
```

Moving final whole-branch judges after Test ensures they inspect the exact final
head, including durable tests. A final finding loops through Implement, Test,
and both fresh judges. When remote delivery has separate authority,
`$use-expskill` may open a draft request after the first coherent implementation
commit, keep it current, and mark it ready only after Test, both whole-branch
judges, and required hosted checks pass on the remote head. AI never approves,
merges, enables auto-merge, or enters a merge queue.

This migration belongs to the later `use-expskill` target. The standalone Test
implementation must not prematurely edit neighbouring skill bodies or claim
that the current router already implements this lifecycle. Until that migration,
the current Implement and router boundaries remain truthful transitional
behavior.

## Behavioral development and acceptance

Test is authored through skill-level RED–GREEN–REFACTOR. Before creating the
candidate entrypoint, fresh agents without Test receive frozen pressure cases
covering:

- a one-line authorization change presented as too small;
- a backend persistence change with passing units and a broken client journey;
- asynchronous UI behavior tempting sleeps and retry-to-green;
- a legitimate metadata-only exemption;
- a product defect tempting production repair;
- direct use without a Plan Graph or router;
- head drift, contradictory reruns, unavailable environments, and hostile page
  or log instructions.

The baseline records actual omissions and rationalizations. Candidate trials
rerun visible, frozen, and hidden variants in fresh disposable contexts. They
are judged from tool behavior, writes, selected scope/rules, runtime actions,
evidence, and terminal state—not generated wording.

Deterministic tests cover package structure, catalog and evidence schemas,
metadata and explicit-only invocation, installer/validator roster, direct-use
boundaries, and preserved stack regressions. A controlled runnable fixture
contains UI, API, persistence, asynchronous behavior, and planted product,
harness, flake, and oracle defects. One independent forward run against a
suitable real open-source application tests adaptation to unfamiliar but
discoverable conventions. External source and run artifacts remain outside the
production plugin.

The Skill Builder rubric remains release-blocking: triggering, scope discipline,
workflow quality, collaboration, output contract, safety, recovery,
composability, context efficiency, and testability must each reach 10/10 on the
same revision, with no High/Medium independent-review finding and every exact
release command passing.

## Required repository changes for the Test target

The Test target may add its skill package, metadata, two supporting JSON
contracts, target-specific deterministic and behavioral tests, and the minimum
manifest, validator, installer, README, and public-roster updates required to
make the new direct skill coherent. It must preserve unrelated work and may not
edit `brainstorm`, `plan`, `design`, `implement`, or `use-expskill` bodies during
this target. The Skill Builder release queue and its contract tests must
insert `test` immediately before the router so future stack runs cannot skip the
new target.

## Non-goals

Test is not a test planner, framework recommender, production implementer, code
reviewer, specification judge, CI provider, generic browser macro system,
security penetration test, full accessibility certification, release manager,
branch integrator, remote-delivery agent, or merge authority.
