// TODO : let's rethink about possibly making folder like web_guidelines/{accessibility.md, data-display.md} and more

---
name: design
description: Build production-intended UI components following project guidelines and share them. Only the user or another ExpSkill skill can load $design.
---

## Precondition

- This skill requires to have `cat {working_folder}/.expskill/design/expskill/design/setup.md` with done == true.
- You should have an completed Concept Brief coming from `$brainstorm` skill or another sufficiently concrete direction. If not redirect the user to `$brainstorm`. 
- Create sketchs using `{working_folder}/.expskill/design/references/sketch-preview.md` and follow `{working_folder}/.expskill/design/references/design-guidelines.md`.

## Behavior

When evidence implies one structure, build it directly using one single frontend subagent. If consequential structural ambiguity remains, render two or three different proposition using frontend subagent following `{expskill_folder}/rules/subagent-usage.md` and let the user select or correct the direction, ask the user confirmation using `{expskill_folder}/rules/explicit-confirmation.md`.

- The steps Build, Animate, Break and Review should be done sequencially and delegated to a frontend subagent. You only orchestrate and verify the work made by the subagents.

## Build

Delegate `{expskill_folder}/skills/design/references/build_ui.md` to make production grade ui.

## Animate

Delegate `{expskill_folder}/skills/design/references/animate_ui.md` to make production grade animation.

## Break

Delegate `{expskill_folder}/skills/design/references/break_ui.md` to find stress test your ui.

## Review

Delegate `{expskill_folder}/skills/design/references/review_ui.md` to make sure the previous steps were followed carefully.

== NOT REVIEWED ==

Maintain one stable isolated component surface. Render the same variant, state, representative synthetic content, theme, and data at actual compact, intermediate, and wide widths. Show applicable responsive, overflow, interaction, focus, loading, empty, error, partial, success, disabled, read-only, destructive, localization, long-content, input, motion, and permission pressure. Interactive panes remain live but only the selected pane is keyboard/accessibility-tree active. Announce width switches and reset the declared start state. Show directly dependent component families together.

Before any approval request, run blocking gates against the exact candidate: project format, lint, type, and build checks. Runtime and console health. Declared interaction, keyboard, and focus paths. Reduced-motion behavior. Zero serious or critical accessibility findings. Zero page-level overflow at compact, intermediate, and wide conditions. Automated checks remain partial evidence, so retain relevant manual observations too.

Any failed gate is correction input: retain failed evidence, repair the candidate, and rerun the affected gate plus dependent gates.

## Delivery and confirmation

Deliver the sketch only when every retained component is current, eligible, technically passing and user-approved following `./expskill/design/references/sketch-preview.md`.

When the workflow finishes, give the user the written Design Brief's path so they can find and reuse it. That summary names the confirmed brief digest, approval digests, and consequential decisions so the next phase inherits complete context with no re-asking. In routed mode, create one coherent local commit directly above the frozen baseline after approval and checkpoint it through `design_state.py`. Commit only above the frozen baseline per `{expskill_folder}/rules/pin-scope.md`. Corrections amend that one commit and invalidate affected evidence and approval. Invalidate evidence/approval on correction per `{expskill_folder}/rules/explicit-confirmation.md`. Routed invocation returns the candidate-bearing delivery receipt and manifest to the router, then stops. Before routed delivery, run the `preflight` check in the packaged `scripts/design_state.py` against the Plan baseline and target branch. An active lifecycle is the only expected pre-delivery problem: `eligible` is false and the CLI exits with status `1`. Fix every other problem before delivery. Missing or malformed preflight output blocks delivery. Use the `deliver` CLI stdout as the receipt, unchanged. After delivery, run `preflight` again and require `eligible: true`, no problems, and CLI exit status `0` before returning the receipt to Plan. The shared Design to Plan expectations live in `docs/specs/design-plan-handoff-contract.md`.

## Rules and recovery

Retain the original delivery request until the router accepts the receipt. If delivery output is lost, recover the receipt only by retrying `deliver` with that identical request, including the original `expected_revision` and inventories. Recover receipts only on the identical expected revision per `{expskill_folder}/rules/pin-scope.md`. The helper revalidates the workspace and returns the same saved receipt without changing state. A changed request still fails. Fail changed delivery requests per `{expskill_folder}/rules/explicit-confirmation.md`.

Read `references/rules-index.md` first. For a narrow component with a small, bounded concern set, selectively load the applicable category references. For a complex composite with interacting regions, state families, density, breakpoint composition, or several behavior modes, load the complete category catalog before shaping it. When classification is uncertain, choose the broader relevant load and revisit it after the first isolated render. Project evidence sets the viewports and states. Project convention and activated obligations still decide which loaded rules apply. Activated normative rules bind only when their platform, level, trigger, and exceptions hold, surface any conflict.

Resolve workflow state through the packaged `scripts/design_state.py`. Its normal root is `~/.expskill/design`, while an explicit state home is test/API-only. Root state at home state per `{expskill_folder}/rules/evidence-report.md`. It is the only state writer and uses expected-revision compare-and-swap. Initialize direct use with the default `direct` invocation mode. Initialize an isolated session the user opened with `invocation_mode` set to `routed`. The helper refuses candidate checkpoints in direct mode and requires one before routed delivery. On missing direction return `not-ready`. On missing specimen capability return `blocked`. On unsafe workspace or contract conflict stop and present the smallest decision. Only explicit user decisions approve or broaden scope.
