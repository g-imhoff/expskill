# Responsive rules


### RESP-01: Use project breakpoints and content failure points
- Authority/scope: project convention plus heuristic.
- Applies: layout, container, and interaction changes across available space.
- Rule: start from existing project breakpoints. When a new boundary is necessary, place it where content or interaction actually fails rather than copying a device list.
- Exceptions: a named platform or product shell may impose canonical size classes.
- Project override: yes, provided active reflow/resize outcomes still pass.
- Isolated check: sweep widths/heights continuously with long content and record the first meaningful failure.
- Sources: [Media Queries Level 5](https://www.w3.org/TR/mediaqueries-5/), [Atlassian grid](https://atlassian.design/foundations/grid-beta/applying-grid), [Apple layout](https://developer.apple.com/design/human-interface-guidelines/layout).

### RESP-02: Preserve reflow at the adopted web conformance level
- Authority/scope: activated WCAG 2.2 AA norm.
- Applies: vertical web content at 320 CSS px width and horizontal content at 256 CSS px height.
- Rule: retain information and functionality without two-dimensional scrolling at the criterion dimensions.
- Exceptions: parts whose use or meaning genuinely requires a two-dimensional layout, such as qualifying maps, diagrams, media, games, presentations, data tables, or manipulation toolbars.
- Project override: breakpoint values may differ. The active tested outcome may not.
- Isolated check: 320 CSS px and 400% zoom, long content, keyboard, and both supported directions.
- Later proof: repeat in every full-page responsive variation.
- Sources: [WCAG Reflow](https://www.w3.org/TR/WCAG22/#reflow), [WCAG full pages](https://www.w3.org/TR/WCAG22/#full-pages).

### RESP-03: Support both orientations unless one is essential
- Authority/scope: activated WCAG 2.2 AA norm and platform guidance.
- Applies: content that can be presented in portrait or landscape.
- Rule: do not lock view/operation to one orientation unless that orientation is genuinely essential.
- Exceptions: exact essential cases such as a use whose meaning/function depends on orientation.
- Project override: no when active. Changed composition between orientations is allowed.
- Isolated check: rotate/emulate both orientations and complete the component's operations.
- Later proof: actual page/device orientation behavior.
- Sources: [WCAG Orientation](https://www.w3.org/TR/WCAG22/#orientation).

### RESP-04: Keep meaningful visual and programmatic order compatible
- Authority/scope: activated WCAG Level A norm.
- Applies: grid/flex reordering, responsive relocation, multi-column composition.
- Rule: when sequence affects meaning, expose a correct programmatic reading sequence. When sequential focus affects meaning or operation, preserve that order.
- Exceptions: independent regions whose relative order does not change meaning.
- Project override: visual composition may vary. Meaningful order may not be corrupted.
- Isolated check: compare visual, DOM, accessibility-tree, and focus order at every responsive state.
- Later proof: host-page navigation and surrounding regions.
- Sources: [Meaningful Sequence](https://www.w3.org/TR/WCAG22/#meaningful-sequence), [Focus Order](https://www.w3.org/TR/WCAG22/#focus-order).

### RESP-05: Adapt instead of merely shrinking
- Authority/scope: heuristic.
- Applies: narrow/wide, high zoom, large text, content-density changes.
- Rule: preserve task priority by wrapping, stacking, relocating, simplifying decoration, or changing interaction presentation. Do not uniformly shrink essential text and targets to force a desktop composition into less space.
- Exceptions: intrinsically spatial content handled by an explicit alternative or controlled overflow.
- Project override: yes. Use existing responsive component behavior.
- Isolated check: representative small/large sizes, 200% text, boundary locale strings, and actual target regions.
- Sources: [Apple layout](https://developer.apple.com/design/human-interface-guidelines/layout), [WCAG Resize Text](https://www.w3.org/TR/WCAG22/#resize-text), [WCAG Reflow](https://www.w3.org/TR/WCAG22/#reflow).

### RESP-06: Preserve controlled overflow and complete values
- Authority/scope: conditional constraint.
- Applies: tables, code, charts, diagrams, long identifiers, toolbars.
- Rule: when two-dimensional content is genuinely necessary, contain and expose scrolling deliberately, keep headers/context usable, and never clip essential values with hidden overflow.
- Exceptions: an alternate responsive representation may replace the 2D view if it preserves meaning/function.
- Project override: yes for the chosen treatment, not for content loss.
- Isolated check: narrow viewport, keyboard-reachable scroll region, complete values, headers, and equivalent representation.
- Sources: [WCAG Reflow](https://www.w3.org/TR/WCAG22/#reflow), [WAI table tips](https://www.w3.org/WAI/tutorials/tables/tips/).

### RESP-07: Do not claim page conformance from isolation
- Authority/scope: normative WCAG conformance scope when a claim is made.
- Applies: every responsive variation and complete process covered by the claim.
- Rule: use isolated results as component evidence and retain exact page/process checks for integration.
- Exceptions: none for a conformance claim.
- Project override: the project may honestly scope its claim. It may not mislabel component evidence as full conformance.
- Isolated check: record passed local obligations and unresolved host dependencies.
- Later proof: all responsive full pages and every page in complete processes.
- Sources: [WCAG Conformance](https://www.w3.org/TR/WCAG22/#conformance), [Full Pages](https://www.w3.org/TR/WCAG22/#full-pages), [Complete Processes](https://www.w3.org/TR/WCAG22/#complete-processes).
