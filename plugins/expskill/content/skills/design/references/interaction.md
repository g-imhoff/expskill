# Interaction rules

### INT-01: Reuse project or native behavior before rebuilding it
- Scope: buttons, links, inputs, dialogs, menus, tabs, disclosure, selection, standard patterns. A genuine requirement the existing/native control cannot meet excepted. Custom choice may vary. Keep active name/role/state/value and operability.
- Rule: prefer an established project component or native host-language feature that supplies the required behavior. Custom UI must preserve the active semantic and interaction contract.
- Check: compare keyboard, pointer, accessibility tree, focus, state behavior with the established/native equivalent.
- Sources: [WAI-ARIA 1.2](https://www.w3.org/TR/wai-aria/).

### INT-02: Keep functionality keyboard-operable
- Scope: web functionality not inherently requiring a movement path, genuine path-dependent functions excepted.
- Rule: every function must be operable through a keyboard interface without timing-specific keystrokes.
- Check: complete open, select, edit, cancel, submit, recover, close using only the keyboard.
- Sources: [WCAG Keyboard](https://www.w3.org/TR/WCAG22/#keyboard).

### INT-03: Preserve focus escape and meaningful order
- Scope: dialogs, composites, editors, custom widgets, sequential navigation, responsive reordering. Nonstandard exits only when communicated, irrelevant ordering unconstrained. Verify within page navigation and overlays later.
- Rule: users can leave any entered component by keyboard. Where order affects meaning/operation, the sequential focus order preserves it.
- Check: record Tab, Shift+Tab, arrows, Escape, documented exits in every state.
- Sources: [No Keyboard Trap](https://www.w3.org/TR/WCAG22/#no-keyboard-trap).

### INT-04: Keep focus visible and unobscured
- Scope: keyboard-operable web UI, sticky regions, overlays, drawers, nested clipping, exact standard exceptions and reveal mechanisms apply. Tokens may define style, but visibility outcomes stay. Surrounding host overlays need later proof.
- Rule: provide a visible focus mode, and keep the focused component partly unobscured by author-created content.
- Check: keyboard traversal in light, dark, high-contrast, forced-colors, zoomed, clipped, overlay states.
- Sources: [Focus Visible](https://www.w3.org/TR/WCAG22/#focus-visible).

### INT-05: Expose name, role, state, and value
- Scope: custom web controls and visible labeled controls, exact criterion scope and host-language semantics excepted. Wording/implementation may vary, active semantic outcomes stay.
- Rule: make name and role programmatically determinable. Expose settable states/properties/values and their changes. Ensure a visible label is contained in the accessible name.
- Check: accessibility-tree snapshots, state changes, localized labels, voice activation using the visible phrase.
- Sources: [Name, Role, Value](https://www.w3.org/TR/WCAG22/#name-role-value).

### INT-06: Provide non-gesture and non-drag alternatives
- Scope: multipoint/path gestures, drag-and-drop, sort, resize, sliders, canvases. Genuinely essential or user-agent-determined behavior excepted as specified.
- Rule: provide a single-pointer, non-path alternative for path/multipoint gestures and a single-pointer non-drag alternative for dragging.
- Check: operate the same outcome with tap/click/buttons/keyboard without path movement or dragging.
- Sources: [Pointer Gestures](https://www.w3.org/TR/WCAG22/#pointer-gestures).

### INT-07: Allow pointer cancellation
- Scope: author-interpreted single-pointer actions, essential cases excepted.
- Rule: complete activation on up with abort/undo, reverse on up, or document a genuinely essential down-event.
- Check: press, move outside, release, cancel, undo for destructive and ordinary actions.
- Sources: [Pointer Cancellation](https://www.w3.org/TR/WCAG22/#pointer-cancellation).

### INT-08: Bind target size to the active standard and unit
- Scope: pointer/touch targets, only documented active criterion/platform exceptions apply. Larger project targets are fine, smaller ones need a valid exception. Keep CSS px, pt, dp distinct.
- Rule: use 24 by 24 CSS px plus its exceptions for WCAG 2.2 AA, 44 CSS px for WCAG AAA, Apple point guidance on Apple platforms, and Android dp guidance on Android. Each number stays with its own standard and unit.
- Check: measure actual hit bounds and adjacent-target spacing with each supported input.
- Sources: [WCAG Target Size Minimum](https://www.w3.org/TR/WCAG22/#target-size-minimum).

### INT-09: Make hover/focus disclosure controllable
- Scope: authored tooltips, submenus, nonmodal popups on hover/focus, input errors, non-obscuring content, user-agent presentation excepted as defined.
- Rule: additional content is dismissible, hoverable when pointer-triggered, and persistent until trigger removal, dismissal, or invalidation.
- Check: trigger by focus and hover, move into content, dismiss without moving focus/pointer, wait for persistence.
- Sources: [Content on Hover or Focus](https://www.w3.org/TR/WCAG22/#content-on-hover-or-focus).

### INT-10: Change context only when expected
- Scope: focus, select, input, routing, window opening, auto-submit. Explicit activation and disclosed behavior excepted within the standards' definitions. Actual routing needs later proof.
- Rule: focus alone leaves context unchanged. Change it only on explicit activation, or on a setting change the user was advised of beforehand.
- Check: focus and change each control while recording navigation, window changes, focus relocation, major context changes.
- Sources: [On Input](https://www.w3.org/TR/WCAG22/#on-input).

### INT-11: Expose qualifying status messages without stealing focus
- Scope: async success, progress, filtering, loading, error messages meeting the status-message definition, other visual changes excluded. Mechanism may vary, the outcome stays. Host assistive-technology behavior needs later proof.
- Rule: expose the status programmatically so assistive technology can present it without moving focus. Choose a role per status or another conforming mechanism.
- Check: delayed update with accessibility-tree and announcement inspection, focus remains expected.
- Sources: [WCAG Status Messages](https://www.w3.org/TR/WCAG22/#status-messages).

### INT-12: Distinguish native disabled, aria-disabled, and read-only
- Scope: unavailable or noneditable controls, host-language support and composite conventions apply. Focusability may vary with discoverability. Keep native semantics as the host defines them.
- Rule: native disabled behavior follows the host language. Aria-disabled only exposes state and requires authors to suppress activation. Custom disabled items may remain focusable for discoverability. Read-only data remains readable and reviewable.
- Check: tab order, pointer/keyboard suppression, accessible state, contrast, discoverability, readable value.
- Sources: [WAI-ARIA aria-disabled](https://www.w3.org/TR/wai-aria/#aria-disabled).
