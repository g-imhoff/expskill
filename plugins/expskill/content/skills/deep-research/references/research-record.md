# Research record

Type the full command and run directory in every call, as
self-contained lines. Shell variables and functions may not persist between
calls, and zsh does not split a variable holding a whole command. `RUN` below
is the absolute `run_dir` that `init` prints. Pass it verbatim to every later
call and to the final report.

Each command prints one JSON object, except `worker-brief --format text`,
which prints only the worker prompt. Run commands one at a time, or chain
them with `&&` so a rejected step stops the rest. On `"ok": false`, nothing
was written and the `errors` list names the op, record, field, and fix. Correct
the input from the errors and rerun the same command. If the helper
keeps rejecting a step you cannot correct, stop the run as `incomplete` and
say why.

## Commands

- `init [--root DIR]`: create a run from the brief on stdin and publish
  checkpoint 0.
- `apply RUN`: apply `{"ops": [...]}` from stdin, all or nothing.
- `worker-brief RUN --assignment ID --format text`: print the worker prompt.
- `capture RUN --assignment ID [--truncated]`: store a worker reply from
  stdin verbatim.
- `checkpoint RUN [--note TEXT]`: publish the next immutable checkpoint. Also
  run it before delivering a result. `finish` and `resume` checkpoint on
  their own.
- `status RUN [--format markdown]`: accounting, phase eligibility, streak,
  and saturation reasons.
- `finish RUN --status S --reason TEXT`: set `saturated`, `incomplete`,
  `paused`, or `cancelled`.
- `verify RUN`: read-only integrity check of the whole run.
- `resume RUN --reason TEXT [--after-saturation]`: reopen a paused,
  incomplete, or saturated run.
- `schema`: print every field, default, enum, and op shape.

## Brief

Only `question` is required.

```bash
python3 <skill dir>/scripts/research_record.py init --root /host/assigned/dir <<'EOF'
{"question": "How do X and Y compare for Z?",
 "inclusions": ["peer-reviewed studies"],
 "exclusions": ["marketing pages"],
 "relevance_rule": "Evidence must concern Z.",
 "source_rules": ["prefer primary sources"],
 "date_rules": ["2018 or later"],
 "output_constraints": ["report in chat"],
 "browsing_method": "",
 "limits": {"max_phases": 8},
 "initial_branches": [
  {"id": "B1", "title": "Mechanisms", "question": "Which mechanisms drive Z?", "rationale": "Core of the question."}
 ]}
EOF
```

`limits` accepts `max_phases`, `max_assignments`, and `max_sources` (positive
integers) and `other` (free text, for example a requested pause point). Omit
`--root` for the private default under `$XDG_STATE_HOME` (or
`~/.local/state`). The helper refuses roots inside a repository.

## Ops

```bash
python3 <skill dir>/scripts/research_record.py apply RUN <<'EOF' && python3 <skill dir>/scripts/research_record.py checkpoint RUN
{"ops": [
 {"op": "start_phase", "id": "P1", "kind": "discovery", "worklist": ["B1"], "methods": ["broad web search"]},
 {"op": "add", "type": "assignment", "item": {"id": "A1", "branch_ids": ["B1"], "question": "Map the main mechanisms of Z.", "queries": ["Z mechanism review"], "budget": {"searches": 8, "opens": 6}}}
]}
EOF
```

To chain a command after a heredoc, put `&&` and the next command on the
heredoc's opening line, as above, so a rejected `apply` stops the
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

`found_by` is an assignment ID or `"coordinator"`. A support item is
`{"source_id", "locator", "excerpt"}` with a short excerpt of at most 500
characters. Any record also accepts a free-text `notes` string.

## Rules the helper enforces

- An accepted `fact` or `uncertain` claim needs support from a source with
  `access: "opened"`. An accepted `inference` needs `based_on` accepted claims
  or opened-source support.
- `duplicate` needs `duplicate_of` naming an accepted claim. `rejected` needs
  `rejection_reason`.
- A claim can be accepted only while its phase runs. Later, only its status,
  support, scope, and notes may change, plus its `branch_ids` in the same
  update that revalidates an old-scope claim into the current scope.
- `new_claim`, `new_mechanism`, `changed_qualifier`, and `changed_conclusion`
  events name accepted claims of the running phase. For `changed_qualifier`
  and `changed_conclusion`, each named claim's `refines` or `contradicts`
  must name the other accepted claim it changes. `new_branch` names branches
  discovered in the running phase. `contradiction` names an accepted claim of
  the running phase whose `contradicts` list is not empty. A claim or branch
  counts in at most one event.
- Assignment status moves forward only: `planned`, `running`, then
  `complete`, `failed`, `truncated`, or `interrupted`. A subagent assignment
  can be `complete` only after a capture not marked `--truncated`. A
  `failed` assignment needs a failure record with its `assignment_id`.
- `complete_phase` refuses while assignments of the phase are planned or
  running, an accepted claim of the phase has no novelty event, or
  a branch discovered in the phase has no `new_branch` event.
- A completed phase is eligible only when every worklist branch has a
  complete assignment in it, the worklist includes every in-scope branch that
  existed at its start, no in-scope branch is queued, active, or blocked,
  every failed, truncated, or interrupted assignment is settled by a complete
  retry, continuation, or resolved failure, no failure of the phase is
  unresolved, and a challenge phase lists its methods. `zero_novelty` means
  eligible with no novelty events. Read the `blockers` in `status`.

## Worker replies

Paste each reply directly on stdin inside a quoted heredoc whose delimiter
appears nowhere in the reply. Copy it character for character. Writing-style
rules shape only your own prose.
The heredoc adds one final newline, which is expected.

```bash
python3 <skill dir>/scripts/research_record.py capture RUN --assignment A1 <<'WORKER_REPLY_A1'
...the worker's reply, unchanged...
WORKER_REPLY_A1
```

Add `--truncated` when the reply was cut off. An assignment whose captured
replies are all truncated is set `truncated` and gets a `continuation_of`
assignment for the rest before the phase closes. Otherwise set it `complete`
or `failed` after capture. List each capture's return ID in `return_ids` on
the claims drawn from it.

## Failures and recovery

Record a failure (`source`, `tool`, `truncation`, `overflow`, `coverage`,
`capture`, `storage`, or `limit`) for every failed question, inaccessible
required source, and cut-off reply. An inaccessible required source gets a
`source` failure naming the assignment, its branch goes `blocked` with the
reason, and the run finishes `incomplete`. The helper refuses an
`incomplete` or `saturated` finish while a `covered` branch has neither
accepted claims nor a resolved failure.

Retry a failed question at most once, with `retry_of` naming the failed
assignment (one corrective retry per lineage), or cover it through a credible
alternative assignment on the same branch in the same or a later phase.
`continuation_of` names a truncated or interrupted assignment. Continue
truncated work that way until it completes. Resolve a failure by setting
`resolved`, a `resolution`, and `resolved_by` naming the recovery
assignment, which must be a different complete assignment, added at or after
the failure was recorded, from the same or a later phase, that shares a
branch with the failed one. When no recovery is possible, leave the failure
unresolved and finish `incomplete`.

## Scope changes, pauses, and resumes

- Material scope change: stop affected work, then run `change_scope`. It
  needs no running phase, archives the old brief, requeues the affected
  branches, moves listed branches out of scope, and resets closure. Then
  revalidate each old-scope accepted claim on an in-scope branch: set its
  `scope_id` to the new scope (adding in-scope `branch_ids` in the same update
  when its old branches left scope) or reject it. Then reopen only the
  affected branches. Unrelated valid evidence keeps its original provenance and
  phase credit.
- Explicit pause: stop owned workers, then `finish --status paused`.
- Explicit cancel or stop: interrupt owned workers immediately, then
  `finish --status cancelled` and stop research. Cancelled is terminal: later
  research needs a new run.
- `finish --status paused` or `cancelled` marks running assignments
  `interrupted`.
- `resume` first validates the whole run: owner, checkpoint chain, digests,
  returns, modes, and state. Any problem, including corrupt, missing, or
  ambiguous state, blocks it. Report the problems and stop, leaving every run
  file untouched (Example 9). Resume marks running assignments
  `interrupted`. Continue them with `continuation_of`. Continuing after
  saturation needs the user's explicit request plus `--after-saturation`.
