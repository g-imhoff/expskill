# Typography rules

### TYPE-01: Use semantic project text roles
- Scope: headings, body, labels, captions, code, numeric, display text. Brand/display work, code, data numerals, or scripts needing a distinct style excepted. Project roles, fonts, hierarchy are authoritative.
- Rule: use the project type scale through text styles that bind family, size, weight, line height, and tracking by semantic role.
- Check: render beside existing hierarchy with long labels, fallback fonts, all supported themes.
- Sources: [Carbon typography](https://carbondesignsystem.com/elements/typography/type-sets/).

### TYPE-02: Preserve user text scaling
- Scope: text, labels, text-bearing controls. Exact WCAG exceptions (captions, images of text) and named platform exceptions excluded. Units may vary. Keep the active scaling outcomes.
- Rule: support the adopted scaling mechanism and preserve content/function through 200% on applicable web targets. Use platform systems such as Dynamic Type or scalable pixels where adopted.
- Check: browser text/zoom through 200%, largest native setting, wrapping, controls, localization. Repeat in host page/window later.
- Sources: [WCAG Resize Text](https://www.w3.org/TR/WCAG22/#resize-text).

### TYPE-03: Survive user text-spacing overrides
- Scope: supported languages/scripts using the relevant properties, unused properties excluded. Authored defaults need not equal these values.
- Rule: keep all content and functionality intact when line height is 1.5 times font size, paragraph spacing 2 times, letter spacing 0.12 times, and word spacing 0.16 times.
- Check: inject only the specified overrides, inspect wrapping, clipping, overlap, focus, controls.
- Sources: [WCAG Text Spacing](https://www.w3.org/TR/WCAG22/#text-spacing).

### TYPE-04: Treat line measure as scoped guidance
- Scope: long-form blocks, short labels, code, tables, script-specific presentation excluded. When AAA is not active the project decides, GOV.UK's ~75 characters applies only where the project adopts it.
- Rule: keep reading measure comfortable for the content and script. If WCAG AAA is active, provide the criterion's mechanism for no more than 80 characters or glyphs, 40 for CJK, plus its other presentation controls.
- Check: measure real localized content at zoom and supported widths, inspect reading flow across all lines.
- Sources: [WCAG Visual Presentation](https://www.w3.org/TR/WCAG22/#visual-presentation).

### TYPE-05: Keep essential meaning reachable under truncation
- Scope: narrow labels, table cells, navigation, cards, dynamic data. Fixed-format data with full value on focus/activation or detail view excepted. The project chooses presentation, and meaning stays reachable.
- Rule: wrap or adapt essential text where possible. If intentional truncation is necessary, make the full value discoverable through an accessible, input-compatible mechanism.
- Check: shortest, typical, longest, localized, unbroken strings, keyboard, touch, pointer, accessibility tree.
- Sources: [Apple typography](https://developer.apple.com/design/human-interface-guidelines/typography).

### TYPE-06: Scope minimum sizes and weights to the platform
- Scope: custom type styles on a named platform. Brand/display work may deviate when readability and scaling are proven. Each platform's guidance sets its minimum.
- Rule: follow the project's or platform's recommended sizes and weights. Small text needs adequate weight and rendering.
- Check: real devices/renderers, smallest size, light weights, low/high contrast, fallback fonts.
- Sources: [Apple typography](https://developer.apple.com/design/human-interface-guidelines/typography).

### TYPE-07: Validate with real language and data
- Scope: all text-bearing UI. Early structural probes may use marked synthetic content. Locales and domain data come from the project.
- Rule: use realistic domain vocabulary, lengths, numerals, plural forms, errors, and at least the relevant supported locale/direction boundaries. Approve typography only on realistic content.
- Check: deterministic fixtures covering long, empty, numeric, error, and RTL cases where supported.
- Sources: [Unicode CLDR](https://cldr.unicode.org/).
