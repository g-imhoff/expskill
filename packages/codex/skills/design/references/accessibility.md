# Accessibility rules


### A11Y-00: Activate normative rules explicitly
- Authority/scope: catalog governance derived from standards' conformance scope.
- Applies: any rule sourced from a normative standard.
- Rule: mark it binding only when the project target, conformance level, trigger, supported technology path, and lack of an exception are established. Otherwise retain it as a conditional baseline.
- Exceptions: none. This is the activation check.
- Project override: project evidence defines applicability but cannot silently override an activated requirement.
- Isolated check: record target/level/scope and which part can be verified locally.
- Later proof: full page/process where the standard requires it.
- Sources: [WCAG Conformance](https://www.w3.org/TR/WCAG22/#conformance), [Conformance Levels](https://www.w3.org/TR/WCAG22/#conformance-level), [Accessibility-supported technologies](https://www.w3.org/TR/WCAG22/#only-accessibility-supported-ways-of-using-technologies).

### A11Y-01: Give non-text content an equivalent purpose
- Authority/scope: activated WCAG Level A norm.
- Applies: informative images, icons, charts, controls, sensory media, and decoration.
- Rule: provide the applicable equivalent text alternative. Name controls by purpose. Make decorative/formatting-only content ignorable.
- Exceptions: the criterion's distinct cases for controls, media, tests, sensory content, CAPTCHA, and decoration.
- Project override: exact wording and technique may vary. The active equivalent-purpose outcome may not.
- Isolated check: accessibility tree and screen reader for informative, control, and decorative variants.
- Sources: [WCAG Non-text Content](https://www.w3.org/TR/WCAG22/#non-text-content), [WAI Images Tutorial](https://www.w3.org/WAI/tutorials/images/).

### A11Y-02: Expose information and relationships programmatically
- Authority/scope: activated WCAG Level A norm.
- Applies: headings, lists, tables, labels, groups, states, and visually encoded relationships.
- Rule: make visual information, structure, and relationships programmatically determinable or available in text.
- Exceptions: the criterion permits an equivalent textual expression.
- Project override: markup may differ. Relationships may not disappear.
- Isolated check: remove styling, linearize, and inspect DOM/accessibility tree for headings, groups, labels, and tables.
- Later proof: page landmarks, generated content, and surrounding relationships.
- Sources: [WCAG Info and Relationships](https://www.w3.org/TR/WCAG22/#info-and-relationships).

### A11Y-03: Do not rely on a single sensory cue
- Authority/scope: activated WCAG Level A norms.
- Applies: status, selection, error, instructions, action prompts, and distinctions.
- Rule: do not use color alone or instructions based only on shape, size, location, orientation, or sound. Add another understandable cue.
- Exceptions: a sensory cue may reinforce another signal.
- Project override: palette and icon language may vary. Sole-cue dependence may not when active.
- Isolated check: grayscale/color-vision simulation, cue removal, and nonvisual interpretation.
- Sources: [Use of Color](https://www.w3.org/TR/WCAG22/#use-of-color), [Sensory Characteristics](https://www.w3.org/TR/WCAG22/#sensory-characteristics).

### A11Y-04: Meet active text and non-text contrast thresholds
- Authority/scope: activated WCAG 2.2 AA norms.
- Applies: ordinary/large text, authored control/state visuals, focus indicators, and meaningful graphics.
- Rule: use 4.5:1 for ordinary text, 3:1 for large text, and 3:1 for necessary UI/state and graphical information, with exact criterion definitions.
- Exceptions: inactive, decorative, logos, user-agent-controlled unmodified UI, essential presentation, and the standards' other exact exceptions.
- Project override: semantic tokens choose colors. Active thresholds may not be silently reduced.
- Isolated check: computed composited colors across themes, overlays, states, gradients, placeholders, and focus. Do not round failures upward.
- Sources: [Contrast Minimum](https://www.w3.org/TR/WCAG22/#contrast-minimum), [Non-text Contrast](https://www.w3.org/TR/WCAG22/#non-text-contrast).

### A11Y-05: Preserve language metadata for supported locales
- Authority/scope: activated WCAG Level A/AA language criteria for web content.
- Applies: localized pages/components and passages in another language.
- Rule: expose the default human language and language changes programmatically.
- Exceptions: proper names, technical terms, indeterminate language, and adopted criterion exceptions.
- Project override: supported locales come from the project. Metadata remains correct for those locales.
- Isolated check: locale stories and accessibility tree for default and changed language.
- Later proof: document-level language in the host page.
- Sources: [Language of Page](https://www.w3.org/TR/WCAG22/#language-of-page), [Language of Parts](https://www.w3.org/TR/WCAG22/#language-of-parts).

### A11Y-06: Treat automated checks as partial evidence
- Authority/scope: evidence heuristic.
- Applies: accessibility review of rendered components.
- Rule: combine automated findings with manual keyboard, focus, semantic, zoom, contrast, and assistive-technology inspection appropriate to the risk. Do not equate a clean scanner with accessibility.
- Exceptions: depth scales with component risk and project support, not a fixed checklist.
- Project override: use the project's approved tools and assistive-technology targets.
- Isolated check: record which claims were mechanically checked and which received contextual human review.
- Sources: [Storybook accessibility testing](https://storybook.js.org/docs/writing-tests/accessibility-testing), [WAI Easy Checks](https://www.w3.org/WAI/test-evaluate/preliminary/).

### A11Y-07: Keep platform numbers namespaced
- Authority/scope: catalog governance.
- Applies: target sizes, font sizes, spacing, contrast enhancements, and platform interaction dimensions.
- Rule: preserve units and scope: CSS px, Apple pt, Android dp, and design-system px values are not interchangeable.
- Exceptions: none without a documented conversion and applicable platform requirement.
- Project override: the project selects its actual platform and may exceed recommendations.
- Isolated check: report the actual platform/unit next to every numeric assertion.
- Sources: [WCAG Target Size](https://www.w3.org/TR/WCAG22/#target-size-minimum), [Apple accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility), [Android accessibility](https://developer.android.com/design/ui/mobile/guides/foundations/accessibility).
