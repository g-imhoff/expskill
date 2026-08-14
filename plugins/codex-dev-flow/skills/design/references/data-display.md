# Data-display rules


### DATA-01 — Choose a display by the analytical relationship
- Authority/scope: conditional heuristic from official statistics guidance.
- Applies: charts and data summaries with a known question/audience.
- Rule: use the simplest familiar display that expresses correlation, deviation, distribution, geography, magnitude, parts-to-whole, ranking, or time; split competing messages when one view obscures them.
- Exceptions: exploratory products may expose additional views while keeping the initial view interpretable.
- Project override: yes, but never with a representation that materially distorts the data.
- Isolated check: deterministic fixtures and an explicit statement of the relationship the display communicates.
- Sources: [ONS choosing a chart](https://service-manual.ons.gov.uk/data-visualisation/chart-types/choosing-a-chart-type), [USWDS data visualizations](https://designsystem.digital.gov/components/data-visualizations/).

### DATA-02 — Use honest baselines
- Authority/scope: conditional data-integrity constraint.
- Applies: quantitative charts.
- Rule: start bar and filled-area axes at zero because length/area encodes magnitude; line/scatter axes may be cropped when clearly communicated and analytically justified.
- Exceptions: a different chart type or explicit representation whose encoding does not depend on length from zero.
- Project override: only with documented analytical rationale; never to exaggerate differences.
- Isolated check: assert axis domain from configuration/data and review cropped alternatives.
- Sources: [ONS axes and gridlines](https://service-manual.ons.gov.uk/data-visualisation/guidance/axes-and-gridlines), [ONS bar charts](https://service-manual.ons.gov.uk/data-visualisation/chart-types/bar-charts).

### DATA-03 — Keep comparisons visually comparable
- Authority/scope: conditional data-integrity constraint.
- Applies: small multiples, dashboard cards, or repeated charts comparing the same measure.
- Rule: use consistent scales and interval semantics; avoid dual axes by default.
- Exceptions: clearly independent units/panels or a documented analytical necessity with explicit labels and explanation.
- Project override: yes only when the comparison remains honest and the difference is conspicuous.
- Isolated check: compare axis domains/configuration across panels and render the no-dual-axis alternative.
- Sources: [ONS axes and gridlines](https://service-manual.ons.gov.uk/data-visualisation/guidance/axes-and-gridlines).

### DATA-04 — Order categories by meaning
- Authority/scope: named-system convention/heuristic.
- Applies: categorical axes, legends, stacks, and repeated panels.
- Rule: use a stable domain order, logical natural order, or task-relevant value order; keep corresponding legend/stack/panel order compatible.
- Exceptions: domain/user-task order takes precedence over rank.
- Project override: yes; the project/domain decides meaning.
- Isolated check: fixtures with ties, totals, other/unknown, and natural ordering; assert visual and accessible order.
- Sources: [ONS ordering in charts](https://service-manual.ons.gov.uk/data-visualisation/guidance/ordering-in-charts).

### DATA-05 — State scope, measure, time, and units clearly
- Authority/scope: named-system convention/heuristic.
- Applies: charts, tables, and dashboard metrics.
- Rule: make purpose, statistical measure, relevant geography/domain, period/as-of time, and units unambiguous without needless repetition.
- Exceptions: context may be supplied by an unambiguously associated host heading or panel.
- Project override: product voice/layout may vary; interpretation may not depend on guessing.
- Isolated check: read the component without surrounding prose and verify its scope; test narrow labels.
- Sources: [ONS chart text](https://service-manual.ons.gov.uk/data-visualisation/guidance/chart-text), [GOV.UK charts](https://brand.design-system.service.gov.uk/data/charts/).

### DATA-06 — Identify series and categories without forcing per-mark labels
- Authority/scope: named-system convention/conditional constraint.
- Applies: multi-series and categorical charts.
- Rule: provide an unambiguous mapping through direct labels, a legend, accessible description, text, or interaction; do not require a visible label on every bar, point, or sector when that harms clarity.
- Exceptions: dense displays may rely more heavily on accessible alternatives and focused detail.
- Project override: yes, provided visual and nonvisual mapping remains unambiguous.
- Isolated check: map every series/category to values visually and in the accessibility representation.
- Sources: [Government Analysis Function charts](https://analysisfunction.civilservice.gov.uk/policy-store/data-visualisation-charts/), [ONS chart elements](https://service-manual.ons.gov.uk/data-visualisation/build-specifications/chart-elements).

### DATA-07 — Provide equivalent information for charts
- Authority/scope: activated WCAG Level A outcome; WAI techniques are informative.
- Applies: charts, diagrams, maps, canvas/SVG visualizations, and other informative graphics.
- Rule: provide a text alternative serving the equivalent purpose and essential values/trends/relationships; a short-plus-long description or data table is an option, not a universal mechanism.
- Exceptions: purely decorative graphics are ignored by assistive technology.
- Project override: technique may vary; active equivalent information may not.
- Isolated check: compare known fixture values and trends with the accessible alternative.
- Sources: [WCAG Non-text Content](https://www.w3.org/TR/WCAG22/#non-text-content), [WAI Complex Images](https://www.w3.org/WAI/tutorials/images/complex/).

### DATA-08 — Match the palette to data semantics and add non-color cues
- Authority/scope: conditional heuristic plus activated use-of-color/contrast outcomes.
- Applies: categorical, sequential, and diverging encodings.
- Rule: use the project's data palette appropriate to the data relationship; pair color with labels, shape, pattern, line style, or position when color carries meaning.
- Exceptions: decorative color and single-series emphasis that does not encode distinct meaning.
- Project override: palette values and category counts follow the project; active accessibility outcomes remain.
- Isolated check: grayscale/color-vision simulation, contrast, theme matrix, and category mapping.
- Sources: [Carbon data palettes](https://v10.carbondesignsystem.com/data-visualization/color-palettes/), [ONS chart colors](https://service-manual.ons.gov.uk/data-visualisation/colours/using-colours-in-charts), [WCAG Use of Color](https://www.w3.org/TR/WCAG22/#use-of-color).

### DATA-09 — Keep provenance attached to the display
- Authority/scope: official-statistics convention/data-integrity heuristic.
- Applies: external, computed, or versioned data.
- Rule: make source, organisation/publication, relevant as-of/version, and essential methodology traceable and unambiguously associated with the display.
- Exceptions: a shared product-level provenance view may satisfy this when association remains clear.
- Project override: presentation may vary; traceability remains.
- Isolated check: visible source/as-of fixture, direct link or provenance action, and long-source-name layout.
- Sources: [ONS chart text](https://service-manual.ons.gov.uk/data-visualisation/guidance/chart-text), [GOV.UK charts](https://brand.design-system.service.gov.uk/data/charts/).

### DATA-10 — Do not hide the main message behind interaction
- Authority/scope: heuristic/named-system convention.
- Applies: filters, hover detail, tabs, zoom, and selectable dashboards.
- Rule: make the initial view communicate the primary message or clearly explain how to explore; use interaction only when it adds necessary value and keep it keyboard/input accessible.
- Exceptions: genuine high-dimensional exploration or personalization.
- Project override: yes with user-need evidence.
- Isolated check: inspect the untouched initial state, keyboard path, and no-hover alternative.
- Sources: [ONS interactive charts](https://service-manual.ons.gov.uk/data-visualisation/guidance/interactive-charts-and-animations), [GOV.UK charts](https://brand.design-system.service.gov.uk/data/charts/).

### DATA-11 — Preserve table relationships and controlled overflow
- Authority/scope: activated information/relationship and reflow outcomes; native table markup is a preferred technique.
- Applies: two-dimensional tabular data.
- Rule: expose header/data relationships programmatically; when 2D layout is necessary, provide deliberate keyboard-reachable overflow without clipping values.
- Exceptions: an equivalent responsive representation can replace the table if it preserves all meaning and function.
- Project override: implementation may vary; relationships/content may not be lost.
- Isolated check: accessibility-tree header associations, narrow viewport, scroll reachability, and complex-header fixture.
- Sources: [WCAG Info and Relationships](https://www.w3.org/TR/WCAG22/#info-and-relationships), [WAI Tables Tutorial](https://www.w3.org/WAI/tutorials/tables/), [WCAG Reflow](https://www.w3.org/TR/WCAG22/#reflow).

### DATA-12 — Model risk-relevant data states distinctly
- Authority/scope: conditional heuristic/design-system convention.
- Applies: loading, loaded, empty, no-results, error, permission, disabled/read-only, offline, stale, and partial data states that the component can actually reach.
- Rule: give materially different states distinct meaning, copy, semantics, and recovery; do not replace every condition with a blank panel or generic spinner.
- Exceptions: impossible states may be omitted with a grounded reason; there is no universal mandatory list.
- Project override: actual state model and visual language come from the project.
- Isolated check: deterministic state fixtures derived from actual contracts and user risk.
- Sources: [Carbon empty states](https://carbondesignsystem.com/patterns/empty-states-pattern/), [Carbon loading](https://carbondesignsystem.com/patterns/loading-pattern/), [GOV.UK error message](https://design-system.service.gov.uk/components/error-message/).

### DATA-13 — Keep independent failures local and stale data honest
- Authority/scope: heuristic.
- Applies: dashboards or composites with independent sources, offline/cached data, and partial failures.
- Rule: keep successful independent regions usable when one fails; identify permission/offline/stale conditions accurately and never present cached data as current without an as-of cue.
- Exceptions: genuinely dependent regions may share a failure state.
- Project override: yes with dependency/security rationale.
- Isolated check: mixed success/failure, offline, 403, stale cache, and recovery fixtures.
- Sources: [Carbon loading](https://carbondesignsystem.com/patterns/loading-pattern/), [Material offline states](https://m1.material.io/patterns/offline-states.html), [GOV.UK dashboards](https://brand.design-system.service.gov.uk/data/dashboards/).

### DATA-14 — Format and stress data for supported locales
- Authority/scope: conditional locale constraint.
- Applies: numbers, dates, currencies, units, labels, sources, and RTL for locales the product supports.
- Rule: use authoritative locale data rather than hard-coded punctuation/order; test large, small, negative, zero, null, long-label, and RTL cases.
- Exceptions: explicitly labeled interchange formats may remain fixed.
- Project override: the project selects supported locales and precision; formatting for those locales remains correct.
- Isolated check: realistic locale fixtures and boundary viewport screenshots plus accessible text.
- Sources: [Unicode CLDR](https://cldr.unicode.org/), [CLDR number symbols](https://cldr.unicode.org/translation/number-currency-formats/number-symbols), [CLDR date patterns](https://cldr.unicode.org/translation/date-time/date-time-patterns).

### DATA-15 — Scale isolated evidence by risk
- Authority/scope: evidence heuristic.
- Applies: data states, viewports, themes, locale, density, and interactions.
- Rule: create discrete isolated fixtures for risk-relevant combinations and combine visual, semantic, and value assertions; do not require a fixed story list or full Cartesian matrix.
- Exceptions: low-risk variants may share evidence; omissions remain explainable.
- Project override: use the project's isolation and test tooling.
- Isolated check: inventory covered/uncovered risks, deterministic mocks, values/labels assertions, accessibility structure, and reviewed visual diffs.
- Sources: [Storybook testing](https://storybook.js.org/docs/writing-tests), [Playwright fixtures](https://playwright.dev/docs/test-fixtures), [Playwright visual comparisons](https://playwright.dev/docs/test-snapshots), [Playwright ARIA snapshots](https://playwright.dev/docs/aria-snapshots).
