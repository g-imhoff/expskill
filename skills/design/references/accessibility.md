# Accessibility rules

### A11Y-00: Activate normative rules explicitly
- Scope: any rule sourced from a normative standard. Binding only when target, level, trigger, supported technology, and no exception are established, otherwise a conditional baseline. Project evidence defines applicability but cannot silently override an activated requirement.
- Rule: mark it binding only when the project target, conformance level, trigger, supported technology path, and lack of an exception are established. Otherwise retain it as a conditional baseline.
- Check: record target/level/scope and which part is verifiable locally, full page/process proof later where the standard requires it.
- Sources: [WCAG Conformance](https://www.w3.org/TR/WCAG22/#conformance).

### A11Y-01: Give non-text content an equivalent purpose
- Scope: informative images, icons, charts, controls, sensory media, decoration, criterion's cases for controls, media, tests, sensory, CAPTCHA, decoration excepted. Wording/technique may vary, equivalent-purpose outcome may not.
- Rule: provide the applicable equivalent text alternative. Name controls by purpose. Make decorative/formatting-only content ignorable.
- Check: accessibility tree and screen reader for informative, control, and decorative variants.
- Sources: [WCAG Non-text Content](https://www.w3.org/TR/WCAG22/#non-text-content).

### A11Y-02: Expose information and relationships programmatically
- Scope: headings, lists, tables, labels, groups, states, visually encoded relationships, an equivalent textual expression is permitted. Markup may differ, relationships may not disappear.
- Rule: make visual information, structure, and relationships programmatically determinable or available in text.
- Check: remove styling, linearize, inspect DOM/accessibility tree. Page landmarks and surrounding relationships need later proof.
- Sources: [WCAG Info and Relationships](https://www.w3.org/TR/WCAG22/#info-and-relationships).

### A11Y-03: Do not rely on a single sensory cue
- Scope: status, selection, error, instructions, prompts, distinctions. A sensory cue may reinforce another signal. Palette/icons may vary, sole-cue dependence may not when active.
- Rule: do not use color alone or instructions based only on shape, size, location, orientation, or sound. Add another understandable cue.
- Check: grayscale/color-vision simulation, cue removal, nonvisual interpretation.
- Sources: [Use of Color](https://www.w3.org/TR/WCAG22/#use-of-color).

### A11Y-04: Meet active text and non-text contrast thresholds
- Scope: ordinary/large text, authored control/state visuals, focus indicators, meaningful graphics, inactive, decorative, logos, unmodified user-agent UI, essential presentation, and exact standard exceptions excluded. Tokens choose colors, active thresholds may not be silently reduced.
- Rule: use 4.5:1 for ordinary text, 3:1 for large text, and 3:1 for necessary UI/state and graphical information, with exact criterion definitions.
- Check: computed composited colors across themes, overlays, states, gradients, placeholders, focus. Do not round failures upward.
- Sources: [Contrast Minimum](https://www.w3.org/TR/WCAG22/#contrast-minimum).

### A11Y-05: Preserve language metadata for supported locales
- Scope: localized pages/components and passages in another language, proper names, technical terms, indeterminate language, and criterion exceptions excluded. Supported locales come from the project.
- Rule: expose the default human language and language changes programmatically.
- Check: locale stories and accessibility tree for default and changed language, document-level language needs host-page proof.
- Sources: [Language of Page](https://www.w3.org/TR/WCAG22/#language-of-page).

### A11Y-06: Treat automated checks as partial evidence
- Scope: accessibility review of rendered components, depth scales with risk, not a fixed checklist. Use the project's approved tools and assistive-technology targets.
- Rule: combine automated findings with manual keyboard, focus, semantic, zoom, contrast, and assistive-technology inspection appropriate to the risk. Do not equate a clean scanner with accessibility.
- Check: record which claims were mechanically checked and which received contextual human review.
- Sources: [WAI Easy Checks](https://www.w3.org/WAI/test-evaluate/preliminary/).

### A11Y-07: Keep platform numbers namespaced
- Scope: target sizes, font sizes, spacing, contrast enhancements, platform dimensions. No conversion without documentation and an applicable requirement. The project selects its platform and may exceed recommendations.
- Rule: preserve units and scope: CSS px, Apple pt, Android dp, and design-system px values are not interchangeable.
- Check: report the actual platform/unit next to every numeric assertion.
- Sources: [WCAG Target Size](https://www.w3.org/TR/WCAG22/#target-size-minimum).
