---
name: design
description: Explicitly invoked or router-selected design phase for grounded, production-intended UI components and approval-ready responsive work.
---

# Design

## Ground

Use this phase when the user explicitly invokes `$design` or when the ExpSkill
router assigns one `expskill-designer` with an exact baseline and isolated
helper-owned worktree. It applies to UI work of any size and stops at a
route-neutral completed bundle. It does not integrate a feature or select
another phase. Inspect the repository, branch/worktree, baseline, dirty state,
framework, target seam, adjacent active components, primitives/tokens, content
and localization patterns, tests, tool commands, and available isolated
specimen capability before writing. Preserve unrelated work. Bind the UI
contract, scope ledger, exclusions, baseline, and dirty fingerprint with
`scripts/design_state.py`. Confirm and record a concise Design brief before
writing. The brief is the canonical decision record for this phase. It names
the objective, requirements, compact, intermediate, and wide expectations,
non-goals, and its digest-bound Plan Graph or existing specification source.
Every consequential choice, including rejected directions and the reason they
lost, belongs in the brief or in a bound approval. A later phase works from
the brief digest plus approvals with no re-asking. Project convention and
confirmed intent outrank heuristics. Activated normative obligations outrank both, and genuine conflicts
are surfaced. Derive production components, temporary specimen files, allowed
dependencies, relevant rules, and actual pressure from content, state, and
responsive conditions in the project.

## Choose

When evidence implies one structure, build it directly. If consequential structural ambiguity remains, render two or three lightweight structural probes and let the user select or correct the direction. A component/template seed requires explicit permission for its exact source, version, adaptation, dependency, provenance, and license consequences. A changed seed requires renewed permission.

## Build

Create production-intended components, interfaces, variants, states, styles, component-owned accessibility behavior, and relevant component tests within the scope ledger. Reuse active primitives/tokens. Record justified local values. Shared token or scope change requires explicit confirmation. Keep `specimen/scenario -> production component -> project primitives`: production code never imports gallery, fixture, mock, or scenario modules. Use deterministic synthetic schema-shaped content. No external network, no customer data, credentials, external assets, silent installs, backend, application state, navigation, live side effects, or external side effects. A smallest temporary specimen adapter may be used when project capabilities permit. If no specimen can render, stop blocked.

In routed mode, use only the copied `.ui-harness/README.md` and
`.ui-harness/agent` support. Keep new specimens and evidence inside that
worktree. Return questions to the router and do not ask the user directly. The
router serializes Plan and Design questions. A stale baseline or invalid copied
setup blocks the session.

## Review

Maintain one stable isolated component surface. Render the same variant, state, representative synthetic content, theme, and data at actual compact, intermediate, and wide widths. Never use CSS zoom. Show applicable responsive, overflow, interaction, focus, loading, empty, error, partial, success, disabled, read-only, destructive, localization, long-content, input, motion, and permission pressure. Interactive panes remain live but only the selected pane is keyboard/accessibility-tree active. Announce width switches and reset the declared start state. Show directly dependent component families together.

Before any approval request, run blocking gates against the exact candidate: project format, lint, type, and build checks. Runtime and console health. Declared interaction, keyboard, and focus paths. Reduced-motion behavior. Zero serious or critical accessibility findings. Zero page-level overflow at compact, intermediate, and wide conditions. Record a tooling gate as not applicable only when the project truly has no such tool and bind the inspected reason. Automated checks remain partial evidence, so retain the relevant manual observations too.

Any failed gate is correction input. Do not present a technically failed candidate as a normal visual approval option: retain the failed evidence, repair the candidate, and rerun the affected gate plus dependent gates. If an unrelated project baseline or unavailable infrastructure prevents valid proof, stop blocked with the exact failure instead of weakening the gate. Once all gates pass, present every required state and viewport, the material change, trade-offs, unavailable later proof, and one decision. Approval binds the exact code/content digest, contract, rendered states, widths/themes, dependencies, and technical evidence. Material changes stale only affected components and dependents.

## Deliver

Deliver only when every retained component is current, eligible, technically passing, user-approved, dependency-coherent, and free of unresolved decisions. Bind three layers: candidate payload (components, exports, tests, assets), temporary preview and review evidence (specimens, fixtures, renders, results, approvals), and a route-neutral manifest (revisions/digests, classifications, contracts, assumptions, non-goals, rule decisions, exceptions, and integration obligations). Evidence, approval, and delivery bind the confirmed Design brief digest. Keep temporary evidence private and do not retain workflow documentation merely because Design ran. Direct invocation preserves unrelated work, returns the delivered summary and stable review entry point without creating a candidate commit, then stops. That summary names the confirmed brief digest, the approval digests, and the consequential decisions so the next phase inherits the complete context with no re-asking. In routed mode only, create one coherent local commit directly above the frozen baseline after approval and checkpoint it through `design_state.py`. Corrections amend that one commit and invalidate affected evidence and approval. Routed invocation returns the candidate-bearing delivery receipt and manifest to the router, then stops without integration.

## Rules and recovery

Read `references/rules-index.md` first. For a narrow component with a small, bounded concern set, selectively load the applicable category references. For a complex composite with interacting regions, state families, density, breakpoint composition, or several behavior modes, load the complete category catalog before shaping it. When classification is uncertain, choose the broader relevant load and revisit it after the first isolated render. This is an evidence-loading strategy, not a fixed ruleset, viewport system, or state matrix. Project convention and activated obligations still decide which loaded rules apply.

Resolve workflow state through the packaged `scripts/design_state.py`. Its normal root is `$XDG_STATE_HOME/expskill/design` (or the platform XDG default), while an explicit state home is test/API-only. It is the only state writer and uses expected-revision compare-and-swap. Initialize direct use with the default `direct` invocation mode. Initialize a router-created isolated session with `invocation_mode` set to `routed`. The helper refuses candidate checkpoints in direct mode and requires one before routed delivery. On missing direction return `not-ready`. On missing specimen capability return `blocked`. On unsafe workspace or contract conflict stop and present the smallest decision. Never infer approval, silently broaden scope, or route downstream.
