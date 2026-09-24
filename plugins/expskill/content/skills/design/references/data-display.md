# Data-display rules

### DATA-01: Choose a display by the analytical relationship
- Scope: charts and data summaries with a known question/audience. Exploratory products may add views while keeping the initial view interpretable. Never a representation that materially distorts the data.
- Rule: use the simplest familiar display that expresses correlation, deviation, distribution, geography, magnitude, parts-to-whole, ranking, or time. Split competing messages when one view obscures them.
- Check: deterministic fixtures plus an explicit statement of the communicated relationship.
- Sources: [ONS choosing a chart](https://service-manual.ons.gov.uk/data-visualisation/chart-types/choosing-a-chart-type).

### DATA-02: Use honest baselines
- Scope: quantitative charts. Encodings not depending on length from zero excepted. Deviation needs documented analytical rationale, never exaggeration.
- Rule: start bar and filled-area axes at zero because length/area encodes magnitude. Line/scatter axes may be cropped when clearly communicated and analytically justified.
- Check: assert axis domain from configuration/data, review cropped alternatives.
- Sources: [ONS axes and gridlines](https://service-manual.ons.gov.uk/data-visualisation/guidance/axes-and-gridlines).

### DATA-03: Keep comparisons visually comparable
- Scope: small multiples, dashboard cards, repeated charts comparing the same measure. Independent units/panels or documented necessity with explicit labels excepted, the comparison must stay honest and conspicuous.
- Rule: use consistent scales and interval semantics. Avoid dual axes by default.
- Check: compare axis domains/configuration across panels, render the no-dual-axis alternative.
- Sources: [ONS axes and gridlines](https://service-manual.ons.gov.uk/data-visualisation/guidance/axes-and-gridlines).

### DATA-04: Order categories by meaning
- Scope: categorical axes, legends, stacks, repeated panels. Domain/user-task order takes precedence over rank, the project/domain decides meaning.
- Rule: use a stable domain order, logical natural order, or task-relevant value order. Keep corresponding legend/stack/panel order compatible.
- Check: fixtures with ties, totals, other/unknown, natural ordering, assert visual and accessible order.
- Sources: [ONS ordering in charts](https://service-manual.ons.gov.uk/data-visualisation/guidance/ordering-in-charts).

### DATA-05: State scope, measure, time, and units clearly
- Scope: charts, tables, dashboard metrics. An unambiguously associated host heading/panel may supply context. Voice/layout may vary, interpretation may not depend on guessing.
- Rule: make purpose, statistical measure, relevant geography/domain, period/as-of time, and units unambiguous without needless repetition.
- Check: read the component without surrounding prose, verify scope. Test narrow labels.
- Sources: [ONS chart text](https://service-manual.ons.gov.uk/data-visualisation/guidance/chart-text).

### DATA-06: Identify series and categories without forcing per-mark labels
- Scope: multi-series and categorical charts. Dense displays may lean on accessible alternatives and focused detail, provided visual and nonvisual mapping stays unambiguous.
- Rule: provide an unambiguous mapping through direct labels, a legend, accessible description, text, or interaction. Do not require a visible label on every bar, point, or sector when that harms clarity.
- Check: map every series/category to values visually and in the accessibility representation.
- Sources: [ONS chart elements](https://service-manual.ons.gov.uk/data-visualisation/build-specifications/chart-elements).

### DATA-07: Provide equivalent information for charts
- Scope: charts, diagrams, maps, canvas/SVG visualizations, informative graphics, purely decorative graphics ignored by assistive technology excepted. Technique may vary, active equivalent information may not.
- Rule: provide a text alternative serving the equivalent purpose and essential values/trends/relationships. A short-plus-long description or data table is an option, not a universal mechanism.
- Check: compare known fixture values and trends with the accessible alternative.
- Sources: [WAI Complex Images](https://www.w3.org/WAI/tutorials/images/complex/).

### DATA-08: Match the palette to data semantics and add non-color cues
- Scope: categorical, sequential, diverging encodings. Decorative color and single-series emphasis not encoding distinct meaning excepted. Palette/counts follow the project, active accessibility outcomes remain.
- Rule: use the project's data palette appropriate to the data relationship. Pair color with labels, shape, pattern, line style, or position when color carries meaning.
- Check: grayscale/color-vision simulation, contrast, theme matrix, category mapping.
- Sources: [ONS chart colors](https://service-manual.ons.gov.uk/data-visualisation/colours/using-colours-in-charts).

### DATA-09: Keep provenance attached to the display
- Scope: external, computed, or versioned data. A shared product-level provenance view suffices when association stays clear. Presentation may vary, traceability remains.
- Rule: make source, organisation/publication, relevant as-of/version, and essential methodology traceable and unambiguously associated with the display.
- Check: visible source/as-of fixture, direct link or provenance action, long-source-name layout.
- Sources: [ONS chart text](https://service-manual.ons.gov.uk/data-visualisation/guidance/chart-text).

### DATA-10: Do not hide the main message behind interaction
- Scope: filters, hover detail, tabs, zoom, selectable dashboards. Genuine high-dimensional exploration or personalization excepted with user-need evidence.
- Rule: make the initial view communicate the primary message or clearly explain how to explore. Use interaction only when it adds necessary value and keep it keyboard/input accessible.
- Check: untouched initial state, keyboard path, no-hover alternative.
- Sources: [GOV.UK charts](https://brand.design-system.service.gov.uk/data/charts/).

### DATA-11: Preserve table relationships and controlled overflow
- Scope: two-dimensional tabular data. An equivalent responsive representation may replace the table if it preserves all meaning and function. Implementation may vary, relationships/content may not be lost.
- Rule: expose header/data relationships programmatically. When 2D layout is necessary, provide deliberate keyboard-reachable overflow without clipping values.
- Check: accessibility-tree header associations, narrow viewport, scroll reachability, complex-header fixture.
- Sources: [WAI Tables Tutorial](https://www.w3.org/WAI/tutorials/tables/).

### DATA-12: Model risk-relevant data states distinctly
- Scope: loading, loaded, empty, no-results, error, permission, disabled/read-only, offline, stale, partial states the component can actually reach. Impossible states may be omitted with a grounded reason, no universal mandatory list. The state model comes from the project.
- Rule: give materially different states distinct meaning, copy, semantics, and recovery. Do not replace every condition with a blank panel or generic spinner.
- Check: deterministic state fixtures derived from actual contracts and user risk.
- Sources: [Carbon empty states](https://carbondesignsystem.com/patterns/empty-states-pattern/).

### DATA-13: Keep independent failures local and stale data honest
- Scope: dashboards or composites with independent sources, offline/cached data, partial failures. Genuinely dependent regions may share a failure state.
- Rule: keep successful independent regions usable when one fails. Identify permission/offline/stale conditions accurately and never present cached data as current without an as-of cue.
- Check: mixed success/failure, offline, 403, stale cache, recovery fixtures.
- Sources: [Material offline states](https://m1.material.io/patterns/offline-states.html).

### DATA-14: Format and stress data for supported locales
- Scope: numbers, dates, currencies, units, labels, sources, RTL for supported locales. Explicitly labeled interchange formats may stay fixed. The project selects locales and precision, formatting for those locales stays correct.
- Rule: use authoritative locale data rather than hard-coded punctuation/order. Test large, small, negative, zero, null, long-label, and RTL cases.
- Check: realistic locale fixtures and boundary viewport screenshots plus accessible text.
- Sources: [Unicode CLDR](https://cldr.unicode.org/).

### DATA-15: Scale isolated evidence by risk
- Scope: data states, viewports, themes, locale, density, interactions. Low-risk variants may share evidence, omissions stay explainable. Use the project's isolation and test tooling.
- Rule: create discrete isolated fixtures for risk-relevant combinations and combine visual, semantic, and value assertions. Do not require a fixed story list or full Cartesian matrix.
- Check: inventory covered/uncovered risks, deterministic mocks, values/labels assertions, accessibility structure, reviewed visual diffs.
- Sources: [Playwright ARIA snapshots](https://playwright.dev/docs/aria-snapshots).
