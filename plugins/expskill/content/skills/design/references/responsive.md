# Responsive rules

### RESP-01: Use project breakpoints and content failure points
- Scope: layout, container, interaction changes across space. Named platform/product shells may impose canonical size classes. Breakpoint values may differ, active reflow/resize outcomes must still pass.
- Rule: start from existing project breakpoints. When a new boundary is necessary, place it where content or interaction fails, as the sweep shows.
- Check: sweep widths/heights continuously with long content, record the first meaningful failure.
- Sources: [Media Queries Level 5](https://www.w3.org/TR/mediaqueries-5/).

### RESP-02: Preserve reflow at the adopted web conformance level
- Scope: vertical web content at 320 CSS px, horizontal at 256 CSS px. Genuinely two-dimensional parts (qualifying maps, diagrams, media, games, presentations, data tables, manipulation toolbars) excepted. Keep the tested outcome.
- Rule: retain information and functionality without two-dimensional scrolling at criterion dimensions.
- Check: 320 CSS px and 400% zoom, long content, keyboard, both directions. Repeat in every full-page variation later.
- Sources: [WCAG Reflow](https://www.w3.org/TR/WCAG22/#reflow).

### RESP-03: Support both orientations unless one is essential
- Scope: content presentable in portrait or landscape, exact essential cases (meaning/function depends on orientation) excepted. Composition may change between orientations.
- Rule: allow both orientations for view and operation, and lock one only where that orientation is genuinely essential.
- Check: rotate/emulate both orientations, complete the component's operations. Actual page/device behavior needs later proof.
- Sources: [WCAG Orientation](https://www.w3.org/TR/WCAG22/#orientation).

### RESP-04: Keep meaningful visual and programmatic order compatible
- Scope: grid/flex reordering, responsive relocation, multi-column composition. Independent regions whose order carries no meaning excepted. Visual composition may vary. Keep meaningful order intact.
- Rule: when sequence affects meaning, expose a correct programmatic reading sequence. When sequential focus affects meaning or operation, preserve that order.
- Check: compare visual, DOM, accessibility-tree, and focus order at every responsive state, host-page navigation later.
- Sources: [Meaningful Sequence](https://www.w3.org/TR/WCAG22/#meaningful-sequence).

### RESP-05: Adapt instead of merely shrinking
- Scope: narrow/wide, high zoom, large text, density changes. Intrinsically spatial content needs an explicit alternative or controlled overflow. Use existing responsive component behavior.
- Rule: preserve task priority by wrapping, stacking, relocating, simplifying decoration, or changing interaction presentation. Adapt the composition rather than uniformly shrinking essential text and targets to fit it.
- Check: representative small/large sizes, 200% text, boundary locale strings, actual target regions.
- Sources: [WCAG Reflow](https://www.w3.org/TR/WCAG22/#reflow).

### RESP-06: Preserve controlled overflow and complete values
- Scope: tables, code, charts, diagrams, long identifiers, toolbars. An equivalent responsive representation preserving meaning/function may replace the 2D view. Treatment may vary. Keep all content complete.
- Rule: when two-dimensional content is genuinely necessary, contain and expose scrolling deliberately, keep headers/context usable, and show every essential value in full, wrapped or scrolled.
- Check: narrow viewport, keyboard-reachable scroll region, complete values, headers, equivalent representation.
- Sources: [WAI table tips](https://www.w3.org/WAI/tutorials/tables/tips/).

### RESP-07: Claim page conformance only with integration evidence
- Scope: every responsive variation and complete process covered by a conformance claim. The project scopes its claim honestly, and component evidence stays labeled as component evidence.
- Rule: use isolated results as component evidence and retain exact page/process checks for integration.
- Check: record passed local obligations and unresolved host dependencies, all responsive full pages and every page in complete processes later.
- Sources: [WCAG Conformance](https://www.w3.org/TR/WCAG22/#conformance).
