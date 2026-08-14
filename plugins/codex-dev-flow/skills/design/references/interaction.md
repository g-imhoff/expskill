# Interaction rules


### INT-01 — Reuse project or native behavior before rebuilding it
- Authority/scope: project convention and conditional heuristic.
- Applies: buttons, links, inputs, dialogs, menus, tabs, disclosure, selection, and other standard patterns.
- Rule: prefer an established project component or native host-language feature that supplies the required behavior; custom UI must preserve the active semantic and interaction contract.
- Exceptions: a genuine requirement the existing/native control cannot meet.
- Project override: yes for choosing a custom control; no for active name/role/state/value and operability requirements.
- Isolated check: compare keyboard, pointer, accessibility tree, focus, and state behavior with the established/native equivalent.
- Sources: [WAI-ARIA 1.2](https://www.w3.org/TR/wai-aria/), [informative APG status](https://www.w3.org/WAI/ARIA/apg/about/introduction/), [APG Read Me First](https://www.w3.org/WAI/ARIA/apg/practices/read-me-first/).

### INT-02 — Keep functionality keyboard-operable
- Authority/scope: activated WCAG 2.2 Level A norm.
- Applies: web functionality that does not inherently require a movement path.
- Rule: every function must be operable through a keyboard interface without timing-specific keystrokes.
- Exceptions: the underlying function genuinely depends on the path of movement, not merely its implementation.
- Project override: no when active.
- Isolated check: complete open, select, edit, cancel, submit, recover, and close using only the keyboard.
- Sources: [WCAG Keyboard](https://www.w3.org/TR/WCAG22/#keyboard).

### INT-03 — Preserve focus escape and meaningful order
- Authority/scope: activated WCAG Level A norms.
- Applies: dialogs, composites, editors, custom widgets, sequential navigation, responsive reordering.
- Rule: users can leave any entered component by keyboard; where order affects meaning/operation, the sequential focus order preserves it.
- Exceptions: nonstandard exit methods may be used only when communicated; irrelevant ordering is not constrained.
- Project override: no when active.
- Isolated check: record Tab, Shift+Tab, arrows, Escape, and documented exits in every state.
- Later proof: verify within page navigation and overlays.
- Sources: [No Keyboard Trap](https://www.w3.org/TR/WCAG22/#no-keyboard-trap), [Focus Order](https://www.w3.org/TR/WCAG22/#focus-order).

### INT-04 — Keep focus visible and unobscured
- Authority/scope: activated WCAG 2.2 AA norms; stronger appearance metrics are AAA.
- Applies: keyboard-operable web UI, sticky regions, overlays, drawers, and nested clipping.
- Rule: provide a visible focus mode and do not let author-created content entirely hide the focused component.
- Exceptions: the standards' exact exceptions and reveal mechanisms.
- Project override: focus tokens may define style; active visibility outcomes may not be removed.
- Isolated check: keyboard traversal in light, dark, high-contrast, forced-colors, zoomed, clipped, and overlay states.
- Later proof: surrounding sticky content and host overlays.
- Sources: [Focus Visible](https://www.w3.org/TR/WCAG22/#focus-visible), [Focus Not Obscured](https://www.w3.org/TR/WCAG22/#focus-not-obscured-minimum), [Focus Appearance AAA](https://www.w3.org/TR/WCAG22/#focus-appearance).

### INT-05 — Expose name, role, state, and value
- Authority/scope: activated WCAG Level A norm and applicable ARIA requirements.
- Applies: custom web controls and visible labeled controls.
- Rule: make name and role programmatically determinable; expose settable states/properties/values and their changes; ensure a visible label is contained in the accessible name.
- Exceptions: only the exact criterion scope and host-language semantics.
- Project override: wording and implementation may vary; active semantic outcomes may not.
- Isolated check: accessibility-tree snapshots, state changes, localized visible labels, and voice activation using the visible phrase.
- Sources: [Name, Role, Value](https://www.w3.org/TR/WCAG22/#name-role-value), [Label in Name](https://www.w3.org/TR/WCAG22/#label-in-name), [WAI-ARIA 1.2](https://www.w3.org/TR/wai-aria/).

### INT-06 — Provide non-gesture and non-drag alternatives
- Authority/scope: activated WCAG 2.2 A/AA norms depending on the behavior.
- Applies: multipoint/path gestures, drag-and-drop, sort, resize, sliders, canvases.
- Rule: provide a single-pointer, non-path alternative for path/multipoint gestures and a single-pointer non-drag alternative for dragging.
- Exceptions: behavior that is genuinely essential or user-agent determined as specified by the criterion.
- Project override: no when active.
- Isolated check: operate the same outcome with tap/click/buttons/keyboard without path movement or dragging.
- Sources: [Pointer Gestures](https://www.w3.org/TR/WCAG22/#pointer-gestures), [Dragging Movements](https://www.w3.org/TR/WCAG22/#dragging-movements).

### INT-07 — Allow pointer cancellation
- Authority/scope: activated WCAG Level A norm.
- Applies: author-interpreted single-pointer actions.
- Rule: avoid irreversible down-event activation; complete on up with abort/undo, reverse on up, or document a genuinely essential down-event.
- Exceptions: the criterion's essential cases.
- Project override: no when active.
- Isolated check: press, move outside, release, cancel, and undo for destructive and ordinary actions.
- Sources: [Pointer Cancellation](https://www.w3.org/TR/WCAG22/#pointer-cancellation).

### INT-08 — Bind target size to the active standard and unit
- Authority/scope: activated WCAG or named platform convention.
- Applies: pointer/touch targets.
- Rule: use 24 by 24 CSS px plus its exceptions for WCAG 2.2 AA, 44 CSS px for WCAG AAA, Apple point guidance on Apple platforms, and Android dp guidance on Android; never translate these as interchangeable universal numbers.
- Exceptions: only those documented by the active criterion/platform.
- Project override: larger project targets are fine; smaller targets need a valid active exception or platform rationale.
- Isolated check: measure actual hit bounds and adjacent-target spacing with each supported input.
- Sources: [WCAG Target Size Minimum](https://www.w3.org/TR/WCAG22/#target-size-minimum), [WCAG Target Size Enhanced](https://www.w3.org/TR/WCAG22/#target-size-enhanced), [Apple accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility), [Android accessibility](https://developer.android.com/design/ui/mobile/guides/foundations/accessibility).

### INT-09 — Make hover/focus disclosure controllable
- Authority/scope: activated WCAG 2.2 AA norm.
- Applies: authored tooltips, submenus, and nonmodal popups appearing on hover or focus.
- Rule: additional content is dismissible, hoverable when pointer-triggered, and persistent until trigger removal, dismissal, or invalidation.
- Exceptions: input errors, content that obscures nothing meaningful, and user-agent-controlled presentation as defined by the criterion.
- Project override: no when active.
- Isolated check: trigger by focus and hover, move into content, dismiss without moving focus/pointer, and wait for persistence.
- Sources: [Content on Hover or Focus](https://www.w3.org/TR/WCAG22/#content-on-hover-or-focus).

### INT-10 — Do not change context unexpectedly
- Authority/scope: activated WCAG Level A norms.
- Applies: focus, select, input, routing, window opening, and auto-submit.
- Rule: focus alone does not change context; changing a setting does not change context unless the user was advised beforehand.
- Exceptions: explicit activation and disclosed behavior within the standards' definitions.
- Project override: no when active.
- Isolated check: focus and change each control while recording navigation, window changes, focus relocation, and major context changes.
- Later proof: actual routing and page context.
- Sources: [On Focus](https://www.w3.org/TR/WCAG22/#on-focus), [On Input](https://www.w3.org/TR/WCAG22/#on-input).

### INT-11 — Expose qualifying status messages without stealing focus
- Authority/scope: activated WCAG 2.2 AA norm.
- Applies: async success, progress, filtering, loading, and error messages that meet the status-message definition.
- Rule: expose the status programmatically so assistive technology can present it without moving focus; no single ARIA role is universally mandated.
- Exceptions: visual changes that are not status messages under the criterion.
- Project override: exact semantic mechanism may vary; the active outcome may not.
- Isolated check: delayed update with accessibility-tree and announcement inspection; focus remains expected.
- Later proof: browser/assistive-technology behavior in host context.
- Sources: [WCAG Status Messages](https://www.w3.org/TR/WCAG22/#status-messages), [WAI-ARIA status](https://www.w3.org/TR/wai-aria/#status).

### INT-12 — Distinguish native disabled, aria-disabled, and read-only
- Authority/scope: host-language/ARIA constraint plus project convention.
- Applies: unavailable or noneditable controls.
- Rule: native disabled behavior follows the host language; aria-disabled only exposes state and requires authors to suppress activation; custom disabled items may remain focusable for discoverability; read-only data remains readable and reviewable.
- Exceptions: host-language-specific support and composite-widget conventions.
- Project override: custom focusability may vary with discoverability; native semantics may not be redefined.
- Isolated check: tab order, pointer/keyboard suppression, accessible state, contrast, discoverability, and readable value.
- Sources: [HTML disabled elements](https://html.spec.whatwg.org/multipage/semantics-other.html#disabled-elements), [WAI-ARIA aria-disabled](https://www.w3.org/TR/wai-aria/#aria-disabled), [APG disabled focusability](https://www.w3.org/WAI/ARIA/apg/practices/keyboard-interface/#focusability-of-disabled-controls).
