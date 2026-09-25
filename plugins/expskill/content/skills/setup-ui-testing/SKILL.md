---
name: setup-ui-testing
description: Establish, retrieve, repair, or update one project's local UI inspection setup when the user explicitly invokes $setup-ui-testing or the ExpSkill router selects it after the packaged inspector reports absent or invalid setup. Do not use for feature design, implementation, or routine testing.
---

# Setup UI Testing

## Boundary

`$setup-ui-testing` is standalone and also accepts one bounded router-selected activation after the packaged
inspector reports `absent` or `invalid`. It establishes one reusable, project-native way to render real
production UI in isolation, records that method locally, and retrieves the record later.

Outside an active workflow bound to this project and the inspector result, a request that does not explicitly
invoke `$setup-ui-testing` gets one response. State that `$setup-ui-testing` was not activated and that
production redesign and production UI work belong outside this skill, then stop before any project-facing
action. Routed activation is setup or repair intent only. It never authorizes production UI work and never
bypasses the proposal confirmation gate.

One-time setup and research only: no feature design, production UI changes, feature specimens, routine tests,
screenshot approval, or review-and-correct loops. Do not invoke or route through another skill. When the request
becomes feature work, stop at the current safe boundary. For routine execution, report the recorded project
command and leave execution to the project's normal workflow or `$test`.

Never commit, push, branch, merge, or open merge requests. Preserve dirty and untracked work.

## Classify before discovery

If this `SKILL.md` is not already loaded, load it fully in one standalone action before any project-facing
action, never combined with a project read or other tool operation.

The skill load and every action in the closed sequence below are fail-closed. An action partially executed if
any command, expected read, or output failed, timed out, or was interrupted, even with exit status zero. On any
error or partial execution, stop unchanged or discard every sequence result and restart at root resolution,
reloading the whole skill first if its load failed. Never retry or patch only the failed step, and never insert
a project-facing read before classification.

Repeat the full closed pre-classification sequence at the start of every explicit or routed invocation and every
resumed user turn in an active workflow. Complete it before the guide, discovery, proposals, or any other
project-facing action. No cached root, instruction set, classification, guide, or workflow state satisfies a
step, and user assertions waive none. No other project-facing action is permitted between these actions:

1. Resolve only the exact project root first. Honor a user-supplied project-root path directly, never
   substituting an enclosing Git root. Otherwise run only `git -C <current-directory> rev-parse
   --show-toplevel` and use its returned worktree root, or the current directory only when that command proves
   it is not a Git worktree. Stop on any other resolution failure.
2. At the exact root and each applicable direct ancestor, check only recognized instruction filenames such as
   `AGENTS.md` and `PROJECT.md` via nonrecursive exact-path existence and read checks, within the host's
   instruction-scope boundary. Build the list from the resolved root and only its applicable direct ancestors.
   Replace the entries and remove inapplicable lines, then run this literal zsh-safe template as the single
   instruction-check action:

   ```zsh
   set -e
   for instruction_file in \
     '[exact-root]/AGENTS.md' \
     '[exact-root]/PROJECT.md' \
     '[exact-applicable-direct-ancestor]/AGENTS.md' \
     '[exact-applicable-direct-ancestor]/PROJECT.md'
   do
     if [[ -e "$instruction_file" || -L "$instruction_file" ]]
     then
       if [[ ! -f "$instruction_file" || ! -r "$instruction_file" ]]
       then
         print -u2 -- "unsafe or unreadable instruction file: $instruction_file"
         exit 1
       fi
       command sed -n '1,$p' -- "$instruction_file" || exit 1
     fi
   done
   ```

   Never assign zsh's special `path` variable.
3. Run the bundled classifier as its own standalone action with:

   ```bash
   python3 <skill-root>/scripts/inspect_setup.py --project-root <project-root>
   ```

Never combine these four actions with any other project discovery (`rg --files`, `find`, listings, inventory,
search). Never search parent directories recursively, enumerate siblings, or traverse into another project. If
the root changes, restart this sequence.

Project enumeration stays forbidden until the classifier returns, then opens only for `absent` or explicit
reopening intent. Classification always precedes broad UI setup discovery.

The canonical root is `<project-root>/.ui-harness` and the canonical guide is `<project-root>/.ui-harness/README.md`.
Do not follow a symlink at either path. Use the returned classification with the direct or router-bound intent:

- **ready, ordinary invocation:** Read only the canonical guide. Report the guide path, established method,
  primary human command, recorded canary command (never execute it), optional agent command when recorded, and
  limitations, plus the exact reopening rule that only explicit update, repair, migrate, replace, or reconfigure
  intent reopens setup. Then stop successfully with no canary, test, drift or dependency inspection, alternative
  research, or writes.
- **invalid, no reopening intent:** Report every inspector issue and stop unchanged. Only a router receipt
  selecting this skill for the same invalid result supplies repair intent. Never reinterpret an invalid record
  as a first run.
- **absent:** Enter first-run discovery, still read-only.
- **explicit update, repair, migrate, replace, or reconfigure intent:** Read a regular readable current guide
  when present, scope discovery to the requested change, and prepare a diff-shaped proposal. A newer version,
  drift, or alternative is not reopening intent.

If the helper cannot establish path or Git safety, stop unchanged with its exact failure. Do not bypass or repair
the helper result manually.

## Discover and propose

For an absent guide or explicit reopening intent, read [the capability contract](references/capability-contract.md)
completely before discovery. Discovery is read-only: no dependency installs, file creation, canary runs, servers,
browsers, emulators, or Git configuration changes.

Inspect only evidence relevant to an isolated UI method: Git/worktree facts, manifests and lockfiles, installed
versions, source and application entrypoints, renderer context, existing previews/stories/examples/component
tests, development targets and literal commands, providers, themes, tokens, fonts, assets, fixtures, and
responsive conventions. Grade every required capability sufficient, partial, or absent, and reuse sufficient
project-native support.

Research only important unresolved gaps against authoritative documentation for the installed or pinned version,
comparing two or three coherent project-native options only when genuine alternatives exist. Never apply a canned
framework recipe or assume a browser-only or agent-only method.

Before proposing, establish that setup is safely possible within development/test support and `.ui-harness`
ownership. If a prerequisite needs production UI, production rendering, dependency restoration outside
development/test support, or other out-of-scope work, stop blocked with this complete checklist in every blocked
response, initial and later alike:

- **Evidence:** Exact evidence paths and facts that establish the blocker.
- **Production prerequisite:** Every exact production-owned path plus the production owner's obligation to change
  and independently validate it.
- **Pending setup:** A project-native development or example target, a real component, named realistic states,
  real context, actual compact, intermediate, and wide viewport or window bounds, one canary, local ownership,
  and honest proof limits.
- **Proof limit:** No setup readiness or isolated three-size proof can be claimed while the production
  prerequisite remains incomplete.
- **Resume condition:** State exactly, "Setup resumes only after the production prerequisite is independently
  completed and validated, followed by a new explicit invocation of `$setup-ui-testing`."

Keep setup choices and prerequisite work out of the handoff and confirmation question. Never seek setup
confirmation while blocked, since later approval cannot authorize production work. Each later turn repeats
pre-classification, the complete checklist, and the unchanged stop. Never request write access for the
prerequisite.

Only when an in-scope safe setup is possible, present one exact proposal satisfying the reference: every proposed
path, dependency, command, ownership decision, ignore action, proof step, limitation, and cleanup action, plus
every planned transient file or owned directory under `.ui-harness/evidence/`. Browser proof must use
`.ui-harness/evidence/browser-proof.json`, created by an independent browser observer. Native proof must use
`.ui-harness/evidence/native-window-proof.json`, created by an independent native-window observer. For every
applicable file, name its exact path, independent creator, expected observable facts from the reference schema,
consumer, and removal after successful setup. If either proof may be needed, never state that no evidence or
oracle file is planned. If proof appears at an unconfirmed path or needs a different path, stop and reconfirm
before accepting or consuming it. Create no unlisted evidence path or ad hoc pending-handoff file. Ask one clear
confirmation question, then stop unchanged. Approval must cover the exact proposal. A request to set up the
capability is not confirmation to mutate before this gate.

## Apply only the confirmed proposal

After explicit confirmation, choose exactly one branch. The guide-metadata-only branch overrides every general
apply, proof, and completion instruction below or in the reference.

**Guide-metadata-only repair:** Only canonical guide metadata such as the exact marker changes. The established
method, command, dependency, renderer, component, scenario, project context, support file, viewport or window
mechanism, canary definition, and proof never change in this branch. Recheck the root, inspector state, dirty
work, scope, and current confirmation, then apply only the metadata change. Rerun the inspector plus applicable
Git ignore and tracking or ownership checks. Execute no canary, test, server, browser, emulator, install, or
responsive proof. Report those checks and stop without entering the general branch.

**All other confirmed setup or update:** Follow the numbered steps:

1. Recheck the root, inspector state, dirty work, and proposed paths. If material facts changed, revise the
   proposal and reconfirm instead of broadening authority.
2. Create or modify only confirmed development/test support and local harness paths. Never modify production UI,
   and never replace a real component with a copy or stand-in. Create, accept, or consume only confirmed
   evidence paths. Stop and reconfirm before adding or using another.
3. For Git projects, resolve `info/exclude` with `git rev-parse --git-path info/exclude`. If existing coverage
   does not ignore the whole canonical root, append only `/.ui-harness/` there. Do not edit tracked `.gitignore`
   unless separately requested. Stop if anything under `.ui-harness` is tracked.
4. Apply the reference's durable-capability proof rule. A first setup runs the lowest-cost representative canary
   and full three-size proof. Any other change in this branch runs only the smallest canary and proof actually
   invalidated by the change: one real production component in one stable named realistic scenario through the
   actual renderer and required context.
5. Write the canonical guide only after the method is established, with the exact marker and headings from the
   reference, literal copyable commands, ownership, proof boundary, and limitations.
6. Remove abandoned prototypes and transient evidence. Re-run the inspector, verify the guide is locally ignored
   with no `.ui-harness` path tracked, check that production targets do not import preview/scenario/test/agent-only
   modules, and confirm unrelated files and customized agent profiles are unchanged.

For a non-Git project, keep the same `.ui-harness` path and document that Git ignore and tracking checks do not
apply. Do not introduce Git merely for this setup.

## Stops and report

If a canary touches production or writes unexpected files, stop, preserve user work, and report the exact diff. If
the real component cannot render with its required context, or actual viewport/window dimensions cannot be
controlled or observed, do not substitute synthetic UI or claim responsive capability. If version-specific
research still yields no safe project-native method, report the evidence, attempted options, concrete blockers,
and smallest unresolved decision.

The guide-metadata-only branch completes when its inspector and applicable Git or ownership checks pass, never
with canary or proof execution. Every other branch requires a valid locally ignored harness root for a Git
project, no tracked `.ui-harness` content, the canary and proof scope required by step 4, cleaned temporary
evidence, and verified ownership boundaries. Report the guide, primary human command, optional agent command,
tracked support paths, real three-size mechanism, applicable canary result, and limitations. State that any proof
covers isolated rendering only, not full-page, application, accessibility, feature, or visual-regression conformance.
