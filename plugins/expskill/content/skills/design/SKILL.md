---
name: design
description: Build production-intended UI components and get user approval through Yodea previews. Only the user or another ExpSkill skill can load $design.
---

# Design

## Ground

Inspect the repository, baseline, worktree, target seam, adjacent components, primitives, content patterns, tests, and specimen capability before writing. Bind the scope ledger, baseline, and dirty fingerprint with `scripts/design_state.py` and confirm a concise Design brief with its digest before writing.

## Choose

When evidence implies one structure, build it directly. If consequential structural ambiguity remains, render two or three lightweight structural probes and let the user select or correct the direction. A component/template seed requires explicit permission for its exact source, version, adaptation, dependency, provenance, and license consequences. A changed seed requires renewed permission.

## Build

Create production-intended components, interfaces, variants, states, styles, component-owned accessibility behavior, and relevant component tests within the scope ledger. Reuse active primitives/tokens. Record justified local values. Shared token or scope change requires explicit confirmation. Keep `specimen/scenario -> production component -> project primitives`, with gallery, fixture, mock, and scenario modules outside production imports. Use deterministic synthetic schema-shaped content. The specimen's only inputs are bundled synthetic content and local component state, and its only effect is its own rendering. Announce every install. The required Yodea workflow below is the only external side effect. A smallest temporary specimen adapter is allowed when project capabilities permit. If no specimen can render, stop blocked.

In routed mode, work only from the isolated worktree and the project's
tracked files. A ready `.expskill/setup-design.md` record may be consulted
for the established sketch method but is optional. Keep new specimens and evidence inside that
worktree. Return questions to the router, which serializes Plan and Design
questions and alone asks the user. A stale baseline blocks the session.

## Review

Maintain one stable isolated component surface. Render the same variant, state, representative synthetic content, theme, and data at actual compact, intermediate, and wide widths. Leave CSS `zoom` unset across the surface. Show applicable responsive, overflow, interaction, focus, loading, empty, error, partial, success, disabled, read-only, destructive, localization, long-content, input, motion, and permission pressure. Interactive panes remain live but only the selected pane is keyboard/accessibility-tree active. Announce width switches and reset the declared start state. Show directly dependent component families together.

Before any approval request, run blocking gates against the exact candidate: project format, lint, type, and build checks. Runtime and console health. Declared interaction, keyboard, and focus paths. Reduced-motion behavior. Zero serious or critical accessibility findings. Zero page-level overflow at compact, intermediate, and wide conditions. Record a tooling gate as not applicable only when the project truly has no such tool and bind the inspected reason. Automated checks remain partial evidence, so retain relevant manual observations too. Technical evidence stays bound to the candidate.

Any failed gate is correction input: retain failed evidence, repair the candidate, and rerun the affected gate plus dependent gates. If an unrelated project baseline or unavailable infrastructure prevents valid proof, stop blocked, report the exact failure, and keep each gate's scope and threshold as written. Once all gates pass, present every required state and viewport, the material change, trade-offs, unavailable later proof, and one decision. Approval binds the exact code/content digest, contract, rendered states, widths/themes, dependencies, and technical evidence. Material changes stale only affected components and dependents.

## Publish a Yodea preview

Always publish the isolated specimen through Yodea once the candidate is ready for visual review and all local gates pass, before requesting approval, in direct and routed mode, even without a link request. Read [the Yodea preview reference](references/yodea-preview.md) on every Design run for commands, authentication, upload limits, and recovery.

Build the specimen and publish its static `dist/` bundle with `yodea push --dir <dir>`. Reuse a valid session or log in with `yodea login --email <addr>`. Upload only synthetic fixture content. Record the returned URL, bundle digest, and project label in a hosted note alongside the delivery manifest's brief and approval digests. A re-push stales affected evidence and approval like any other material change. Check that the hosted page loads and shows the candidate at one width, as convenience evidence. Local gates still decide technical eligibility. If publishing or checking the preview fails, report the exact blocker and keep preview delivery incomplete. Share only Yodea-reported preview URLs.

Include a clickable Yodea link in each visual approval request, preview progress update, Design delivery summary, and downstream handoff. Carry it into the description of any PR or merge request created or updated for the work, with the represented candidate revision or digest and a short account of what changed. In routed mode, relay the link through the router. Keep the preview available through handoff and PR review, past Design approval and local worktree cleanup. Remove a hosted copy only under the reference's retention rules.

## Deliver

Deliver only when every retained component is current, eligible, technically passing, user-approved, dependency-coherent, and free of unresolved decisions, and its Yodea preview is available. Bind three layers: candidate payload (components, exports, tests, assets), temporary preview and review evidence (specimens, fixtures, renders, results, approvals), and a route-neutral manifest (revisions/digests, classifications, contracts, assumptions, non-goals, rule decisions, exceptions, and integration obligations). Evidence, approval, and delivery bind the confirmed Design brief digest. Keep temporary evidence private except for the published synthetic specimen and its shared link, and deliver Design session notes only when project convention or the user asks for them. Direct invocation preserves unrelated work, returns the delivered summary and stable review entry point without creating a candidate commit, then stops. When the workflow finishes, give the user the written Design Brief's path so they can find and reuse it. That summary names the confirmed brief digest, approval digests, and consequential decisions so the next phase inherits complete context with no re-asking. In routed mode, create one coherent local commit directly above the frozen baseline after approval and checkpoint it through `design_state.py`. Corrections amend that one commit and invalidate affected evidence and approval. Routed invocation returns the candidate-bearing delivery receipt and manifest to the router, then stops. Before routed delivery, run the `preflight` check in the packaged `scripts/design_state.py` against the Plan baseline and target branch. An active lifecycle is the only expected pre-delivery problem: `eligible` is false and the CLI exits with status `1`. Fix every other problem before delivery. Missing or malformed preflight output blocks delivery. Use the `deliver` CLI stdout as the receipt, unchanged. After delivery, run `preflight` again and require `eligible: true`, no problems, and CLI exit status `0` before returning the receipt to Plan. The shared Design to Plan expectations live in `docs/specs/design-plan-handoff-contract.md`.

## Rules and recovery

Retain the original delivery request until the router accepts the receipt. If delivery output is lost, recover the receipt only by retrying `deliver` with that identical request, including the original `expected_revision` and inventories. The helper revalidates the workspace and returns the same saved receipt without changing state. A changed request still fails.

Read `references/rules-index.md` first. For a narrow component with a small, bounded concern set, selectively load the applicable category references. For a complex composite with interacting regions, state families, density, breakpoint composition, or several behavior modes, load the complete category catalog before shaping it. When classification is uncertain, choose the broader relevant load and revisit it after the first isolated render. Project evidence sets the viewports and states. Project convention and activated obligations still decide which loaded rules apply. Activated normative rules bind only when their platform, level, trigger, and exceptions hold, surface any conflict.

Resolve workflow state through the packaged `scripts/design_state.py`. Its normal root is `~/.expskill/design`, while an explicit state home is test/API-only. It is the only state writer and uses expected-revision compare-and-swap. Initialize direct use with the default `direct` invocation mode. Initialize an isolated session the user opened with `invocation_mode` set to `routed`. The helper refuses candidate checkpoints in direct mode and requires one before routed delivery. On missing direction return `not-ready`. On missing specimen capability return `blocked`. On unsafe workspace or contract conflict stop and present the smallest decision. Only explicit user decisions approve or broaden scope.

## Shared rules

Follow shared rules in `../../rules/` when their triggers match. The router index in `../use-expskill/SKILL.md` lists pin scope, isolate writes, ask owned only, explicit confirmation, bounded fanout, and evidence report. Read a leaf only on trigger match.
