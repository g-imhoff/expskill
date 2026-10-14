# Research record

`scripts/research_record.py` owns every run. Each command prints one JSON
object. On `"ok": false`, the `errors` list names the op, record, field, and
the fix, and nothing was written. Correct the input and run the same command
again. `schema` prints every field, default, enum, and op shape.

Every command runs as `python3 <skill dir>/scripts/research_record.py
<command>`. In the table, `RUN` is the absolute `run_dir` that `init` printed.
Type the full command and the full run directory in every call. Shell
variables and functions may not persist between calls, and zsh does not split
a variable holding a whole command, so self-contained lines are the reliable
form.

## Commands

| Command | Purpose |
|---|---|
| `init [--root DIR]` | Create a run from the brief on stdin and publish checkpoint 0. |
| `apply RUN` | Apply `{"ops": [...]}` from stdin, all or nothing. |
| `worker-brief RUN --assignment ID --format text` | Print only the worker prompt. Send the whole output to the worker unchanged. |
| `capture RUN --assignment ID [--truncated]` | Store a worker reply from stdin verbatim. |
| `checkpoint RUN [--note TEXT]` | Publish the next immutable checkpoint. |
| `status RUN [--format markdown]` | Accounting, phase eligibility, streak, and saturation reasons. |
| `finish RUN --status S --reason TEXT` | Set `saturated`, `incomplete`, `paused`, or `cancelled`. |
| `verify RUN` | Read-only integrity check of the whole run. |
| `resume RUN --reason TEXT [--after-saturation]` | Reopen a paused, incomplete, or saturated run. |
| `schema` | Field and op reference. |

## Brief and initial branches

```bash
python3 <skill dir>/scripts/research_record.py init --root /host/assigned/dir <<'EOF'
{"question": "How do X and Y compare for Z?",
 "inclusions": ["peer-reviewed and standards documents"],
 "exclusions": ["marketing pages"],
 "relevance_rule": "Evidence must concern Z under field conditions.",
 "source_rules": ["prefer primary sources"],
 "date_rules": ["2018 or later unless foundational"],
 "output_constraints": ["report in chat"],
 "browsing_method": "",
 "limits": {"max_phases": 8},
 "initial_branches": [
  {"id": "B1", "title": "Mechanisms", "question": "Which mechanisms drive Z?", "rationale": "Core of the question."}
 ]}
EOF
```

Only `question` is required. `limits` accepts `max_phases`, `max_assignments`,
and `max_sources` (positive integers) and `other` (free text, for example a
requested pause point). Omit `--root` to use the private default under
`$XDG_STATE_HOME` (or `~/.local/state`). The helper refuses roots inside a
repository. Keep the printed `run_dir`.

When browsing is a host-supplied command instead of built-in search and fetch
tools, put its exact invocation in `browsing_method`. The brief carries it to
workers, so each worker already holds the command.

Workers see the question, inclusions, exclusions, relevance rule, source and
date rules, the browsing method, branch titles and questions, assignment
questions, queries, and accepted claims from earlier phases on their branches
and parent branches. Keep private supplied context out of all of those. Workers see
`assumptions` and `known_evidence` only when `share_context_with_workers` is
`true`.

## Ops

```bash
python3 <skill dir>/scripts/research_record.py apply <run_dir> <<'EOF' && python3 <skill dir>/scripts/research_record.py checkpoint <run_dir>
{"ops": [
 {"op": "start_phase", "id": "P1", "kind": "discovery", "worklist": ["B1"], "methods": ["broad web search"]},
 {"op": "add", "type": "assignment", "item": {"id": "A1", "branch_ids": ["B1"], "question": "Map the main mechanisms of Z.", "queries": ["Z mechanism review"], "budget": {"searches": 8, "opens": 6}}}
]}
EOF
```

To chain another command after a heredoc, put `&&` and the next command on
the heredoc's opening line, as above. Then a rejected `apply` stops the
`checkpoint`. A command on the line after the closing delimiter runs even
when `apply` fails.

- `add` takes `type` and `item`. `update` takes `type`, `id`, and `set`, and a
  list in `set` replaces the whole list. Omitted `id`s get B1, A1, SRC1, C1,
  N1, F1 style IDs, returned in `ids`.
- `start_phase` takes `kind` (`discovery`, `investigation`, or `challenge`),
  a frozen `worklist` of branch IDs, and optional `methods`. Queued worklist
  branches become `active`.
- `complete_phase` takes `id` and optional `duplicates` (one integer: the
  paraphrases and agreeing sources merged this phase, at least the duplicate
  claims recorded), `methods`, `limitations`, and `next_questions`.
- `change_scope` takes `reason`, an optional partial `brief`,
  `affected_branch_ids` to requeue, and `out_of_scope` as `[{"id", "reason"}]`.

Required fields when adding:

| Type | Required | Common optional fields |
|---|---|---|
| branch | `title`, `question`, `rationale` | `parent_ids`, `next_questions`, `status`, `out_of_scope_reason` |
| assignment | `branch_ids`, `question` | `worker` (`subagent` or `coordinator`), `queries`, `budget`, `retry_of`, `continuation_of` |
| source | `url`, `title`, `source_type` (`primary`, `secondary`, `other`), `found_by` | `access` (`opened`, `inaccessible`, `unverified`), `publisher`, `published`, `quality_note` |
| claim | `statement`, `epistemic`, `branch_ids`, `status` | `support`, `qualifiers`, `refines`, `contradicts`, `based_on`, `duplicate_of`, `rejection_reason`, `return_ids` |
| novelty | `kind`, `reason` | `claim_ids`, `branch_ids` |
| failure | `kind`, `detail` | `assignment_id`, `resolved`, `resolution`, `resolved_by` |

Add a newly found branch while the phase that found it runs. Once a phase
has completed, in-scope branches can be added only during a later phase, or
right after `change_scope` or a resume after saturation. Record an
out-of-scope proposal as a branch with status `out_of_scope` and an
`out_of_scope_reason`. `change_scope` is only for a user-approved scope
change.

`found_by` is an assignment ID or `"coordinator"`. A support item is
`{"source_id", "locator", "excerpt"}` with a short excerpt of at most 500
characters. Any record also accepts a free-text `notes` string.

## Rules the helper enforces

Claims:

- An accepted `fact` or `uncertain` claim needs support from a source with
  `access: "opened"`. An accepted `inference` needs `based_on` accepted claims
  or opened-source support.
- `duplicate` needs `duplicate_of` naming an accepted claim. `rejected` needs
  `rejection_reason`.
- A claim can be accepted only while its phase runs. Later, only its status,
  support, scope, and notes may change, plus its `branch_ids` in the same
  update that revalidates an old-scope claim into the current scope.
- Rejecting an accepted claim of an earlier phase in the current scope needs a
  running phase with an accepted claim whose `contradicts` names it.

Novelty events:

- `new_claim`, `new_mechanism`, `changed_qualifier`, and `changed_conclusion`
  name accepted claims of the running phase. For `changed_qualifier` and
  `changed_conclusion`, each named claim's `refines` or `contradicts` must name
  the other accepted claim it changes. `new_branch` names branches
  discovered in the running phase. `contradiction` names an accepted claim of
  the running phase whose `contradicts` list is not empty.
- A claim or branch counts in at most one event.

Assignments:

- Status moves forward only: `planned`, `running`, then `complete`, `failed`,
  `truncated`, or `interrupted`.
- Once running, an assignment's question, branches, worker, queries, and
  budget are fixed.
- A subagent assignment can be `complete` only after a capture not marked
  `--truncated`. A `failed` assignment needs a failure record with its
  `assignment_id`.
- `retry_of` names a failed assignment. Each lineage gets one corrective retry.
  `continuation_of` names a truncated or interrupted assignment.
- A failure can be `resolved` only with a `resolution` and a `resolved_by`
  naming a different complete assignment, added at or after the failure was
  recorded, from the same or a later phase, that shares a branch with the
  failed one.
- Only `change_scope` moves an existing branch into or out of scope.

A branch can be `covered` only after a complete assignment includes it, and only
with accepted evidence on it: an accepted claim naming the branch, or a resolved
failure on its assignments. A branch whose required source is inaccessible is
`blocked` with a reason. `covered` is reserved for branches with accepted
evidence.

`complete_phase` refuses while assignments of the phase are planned or
running, while an accepted claim of the phase has no novelty event, or while a
branch discovered in the phase has no `new_branch` event. Mark repeated
information as `duplicate` instead of accepting it again.

A completed phase is eligible only when every worklist branch has a complete
assignment in it, the worklist includes every in-scope branch that existed at
its start, no in-scope branch is queued, active, or blocked, every failed,
truncated, or interrupted assignment is settled by a complete retry,
continuation, or resolved failure, no failure of the phase is unresolved, and
a challenge phase lists its methods. `zero_novelty` means eligible with no
novelty events. Read the `blockers` in `status`.

`finish --status saturated` works only when the latest two completed phases
in the current scope since the last closure reset are both zero-novelty, the
latter is a challenge, nothing is planned or running, every in-scope branch is
covered with no open `next_questions`, no failure is unresolved, and no
old-scope claim on an in-scope branch awaits revalidation. Otherwise it
returns the reasons.

## Judging novelty

The helper checks bookkeeping. You judge meaning. Before accepting a claim,
compare it with the accepted claims of its branches:

- Same proposition, scope, mechanism, and qualifiers, with new wording or a
  new source: record it as `duplicate` and add the support to the original.
  A vaguer restatement, a near-synonym, or an unnamed subject is still a
  paraphrase.
- An explicit new or changed condition, range, population, date, version,
  magnitude, or mechanism, or a conflicting result: accept it, set `refines`
  or `contradicts` to the claim it changes, and record the matching novelty
  event.
- Conflicts stay as separate attributed claims linked with `contradicts`.

## Worker replies

Capture each reply before using it. Paste it exactly on stdin, inside a
quoted heredoc whose delimiter does not occur in the reply. Copy it character
for character: your own writing rules shape your prose, the pasted bytes stay
exact. Paste directly on stdin inside the quoted heredoc. The heredoc
adds one final newline, which is expected.

```bash
python3 <skill dir>/scripts/research_record.py capture <run_dir> --assignment A1 <<'WORKER_REPLY_A1'
...the worker's reply, unchanged...
WORKER_REPLY_A1
```

Add `--truncated` when the reply was cut off, set that assignment
`truncated`, and add a `continuation_of` assignment for the rest. Otherwise
set it `complete` or `failed`. List the capture in `return_ids` on the claims
drawn from it.

Worker prompts come from `worker-brief RUN --assignment ID --format text`.
Send its whole output as the subagent prompt, straight to the worker with no
file in between.

## Failures, retries, and continuations

- Record a failure (`source`, `tool`, `truncation`, `overflow`, `coverage`,
  `capture`, `storage`, or `limit`) for every failed question, inaccessible
  required source, and cut-off reply. An inaccessible required source gets a
  `source` failure naming the assignment, its branch goes `blocked` with the
  reason, and the run finishes `incomplete`. The helper refuses an `incomplete`
  or `saturated` finish while a `covered` branch has neither accepted claims
  nor a resolved failure.
- Retry a failed question once with `retry_of`, or cover it through a
  credible alternative assignment on the same branch in the same or a later
  phase. Continue truncated work with `continuation_of` until it completes.
- Resolve the failure with `resolved_by` the completed recovery assignment.
  When no recovery is possible, leave it unresolved and finish `incomplete`.

## Scope changes, pauses, and resumes

- `change_scope` needs no running phase. It archives the old brief, requeues
  affected branches, moves listed branches out of scope, and resets closure.
  Revalidate each old-scope accepted claim on an in-scope branch by setting
  its `scope_id` to the new scope, adding in-scope `branch_ids` in the same
  update when its old branches left scope, or reject it.
- `finish --status paused` or `cancelled` marks running assignments
  `interrupted`. Cancelled is terminal: start a new run instead of resuming.
- `resume` first verifies owner, checkpoint chain, digests, returns, modes,
  and state. Any problem blocks it. Report the problems and stop, leaving run
  files untouched. Resume marks running assignments `interrupted`. Continue them
  with `continuation_of`.

## Checkpoints and reporting

Checkpoint after init, after each completed phase, and before delivering a
result. `finish` and `resume` checkpoint on their own. For the report, use
`status --format markdown` for phase and novelty accounting. Run `verify`, and
when it passes give `RUN` plus the checkpoint `sequence` and `sha256`. If it
fails, report the problems instead of a digest.
