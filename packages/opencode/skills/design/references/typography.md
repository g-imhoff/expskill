# Typography rules


### TYPE-01: Use semantic project text roles
- Authority/scope: project convention. Conditional constraint in a tokenized project.
- Applies: headings, body, labels, captions, code, numeric data, and display text.
- Rule: use project text styles that bind family, size, weight, line height, and tracking by semantic role. Do not assemble a parallel ad hoc scale.
- Exceptions: justified brand/display work, code, data numerals, or scripts needing a distinct supported style.
- Project override: the project's roles, fonts, and hierarchy are authoritative.
- Isolated check: render the component beside existing hierarchy with long labels, fallback fonts, and all supported themes.
- Sources: [Apple typography](https://developer.apple.com/design/human-interface-guidelines/typography), [Atlassian typography](https://atlassian.design/foundations/typography), [Carbon typography](https://carbondesignsystem.com/elements/typography/type-sets/), [Fluent typography](https://fluent2.microsoft.design/typography).

### TYPE-02: Preserve user text scaling
- Authority/scope: activated norm for WCAG 2.2 AA web content. Platform convention for native scaling systems.
- Applies: text, labels, and text-bearing controls.
- Rule: support the adopted scaling mechanism and preserve content/function through 200% on applicable web targets. Use platform systems such as Dynamic Type or scalable pixels where adopted.
- Exceptions: exact WCAG exceptions such as captions and images of text. Named platform exceptions.
- Project override: implementation units may vary. Active scaling outcomes may not.
- Isolated check: browser text/zoom through 200%, largest supported native text setting, wrapping, controls, and localization.
- Later proof: repeat in the host page/window.
- Sources: [WCAG Resize Text](https://www.w3.org/TR/WCAG22/#resize-text), [Apple accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility), [Android Material theming](https://developer.android.com/codelabs/m3-design-theming).

### TYPE-03: Survive user text-spacing overrides
- Authority/scope: activated WCAG 2.2 AA norm for applicable markup-based content.
- Applies: supported languages/scripts using the relevant properties.
- Rule: no content or functionality may be lost when line height is 1.5 times font size, paragraph spacing 2 times, letter spacing 0.12 times, and word spacing 0.16 times.
- Exceptions: properties not used by the language/script.
- Project override: no when the criterion is active. Authored defaults need not equal these values.
- Isolated check: inject only the specified overrides and inspect wrapping, clipping, overlap, focus, and controls.
- Sources: [WCAG Text Spacing](https://www.w3.org/TR/WCAG22/#text-spacing).

### TYPE-04: Treat line measure as scoped guidance
- Authority/scope: activated WCAG AAA mechanism requirement. Otherwise heuristic or named-system convention.
- Applies: long-form blocks of text.
- Rule: keep reading measure comfortable for the content and script. If WCAG AAA is active, provide the criterion's mechanism for no more than 80 characters or glyphs, 40 for CJK, plus its other presentation controls.
- Exceptions: short labels, code, tables, and script-specific presentation.
- Project override: yes when AAA is not active. GOV.UK's roughly 75-character guidance is not universal.
- Isolated check: measure real localized content at zoom and supported widths. Inspect reading flow rather than a single average.
- Sources: [WCAG Visual Presentation](https://www.w3.org/TR/WCAG22/#visual-presentation), [GOV.UK layout](https://design-system.service.gov.uk/styles/layout/).

### TYPE-05: Do not let truncation hide essential meaning
- Authority/scope: conditional constraint supported by accessibility and platform guidance.
- Applies: narrow labels, table cells, navigation, cards, and dynamic data.
- Rule: wrap or adapt essential text where possible. If intentional truncation is necessary, make the full value discoverable through an accessible, input-compatible mechanism.
- Exceptions: fixed-format data whose complete value is available on focus/activation or in an equivalent detail view.
- Project override: the project chooses the presentation, not the loss of meaning.
- Isolated check: use shortest, typical, longest, localized, and unbroken strings. Test keyboard, touch, pointer, and accessibility tree.
- Sources: [WCAG Resize Text understanding](https://www.w3.org/WAI/WCAG22/Understanding/resize-text.html), [Apple typography](https://developer.apple.com/design/human-interface-guidelines/typography), [ONS chart text](https://service-manual.ons.gov.uk/data-visualisation/guidance/chart-text).

### TYPE-06: Scope minimum sizes and weights to the platform
- Authority/scope: platform convention/heuristic.
- Applies: custom type styles on a named platform.
- Rule: follow the project's or platform's recommended sizes and weights. Small text needs adequate weight and rendering, but no single numeric minimum transfers across web, iOS, Android, macOS, watch, or spatial UI.
- Exceptions: brand/display work may deviate when readability and scaling are proven.
- Project override: yes within active accessibility outcomes.
- Isolated check: real devices/renderers, smallest supported size, light weights, low contrast, high contrast, and fallback fonts.
- Sources: [Apple accessibility type guidance](https://developer.apple.com/design/human-interface-guidelines/accessibility), [Apple typography](https://developer.apple.com/design/human-interface-guidelines/typography).

### TYPE-07: Validate with real language and data
- Authority/scope: heuristic.
- Applies: all text-bearing UI.
- Rule: use realistic domain vocabulary, lengths, numerals, plural forms, errors, and at least the relevant supported locale/direction boundaries. Do not approve typography with placeholder-only content.
- Exceptions: an early structural probe may use marked synthetic content, but not final approval evidence.
- Project override: supported locales and domain data come from the project.
- Isolated check: deterministic content fixtures including long, empty, numeric, error, and RTL cases where supported.
- Sources: [Unicode CLDR](https://cldr.unicode.org/), [Storybook testing](https://storybook.js.org/docs/writing-tests), [GOV.UK component research guidance](https://design-system.service.gov.uk/community/develop-a-component-or-pattern/).
