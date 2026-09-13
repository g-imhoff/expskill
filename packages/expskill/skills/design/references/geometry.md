# Geometry rules


### GEO-01: Use the project spacing system
- Authority/scope: project convention. Conditional constraint in a tokenized project.
- Applies: margin, padding, gap, density, and component layout.
- Rule: use the project's spacing tokens and layout primitives before adding raw values or importing another system's scale.
- Exceptions: dynamic safe-area insets, fluid proportions, content-derived sizing, or a documented missing token.
- Project override: the project defines the scale. No external 4px, 5px, or 8px base is universal.
- Isolated check: inspect token use and render at supported densities, themes, and boundary widths.
- Sources: [Atlassian spacing](https://atlassian.design/foundations/grid-beta/applying-grid), [Carbon spacing](https://carbondesignsystem.com/elements/spacing/overview/), [Fluent layout](https://fluent2.microsoft.design/layout), [GOV.UK spacing](https://design-system.service.gov.uk/styles/spacing/).

### GEO-02: Let distance express relationship
- Authority/scope: heuristic supported by multiple official systems.
- Applies: groups, sections, labels and controls, repeated items, hierarchy.
- Rule: keep strongly related items closer than separate groups. Use repeated spacing patterns to signal equal relationship and larger separation to signal hierarchy.
- Exceptions: intentional high density, diagrams, spatial data, or user-controlled density.
- Project override: yes. Use the project's rhythm and content needs.
- Isolated check: view without borders or background decoration and verify grouping remains understandable.
- Sources: [Carbon spacing relationships](https://carbondesignsystem.com/elements/spacing/overview/), [Fluent spacing and proximity](https://fluent2.microsoft.design/layout), [Atlassian spacing](https://atlassian.design/foundations/grid-beta/applying-grid).

### GEO-03: Align meaningful structures
- Authority/scope: heuristic/platform convention.
- Applies: top-level containers, repeated rows, columns, text and control alignment.
- Rule: align related content to project grid lines or shared edges. Preserve meaningful alignment as content and viewport change.
- Exceptions: optical correction, fluid diagrams, asymmetric editorial layouts, and deliberately independent elements.
- Project override: yes. The project grid and component primitives are authoritative.
- Isolated check: overlay alignment guides at narrow/wide widths and with long content.
- Sources: [Apple layout](https://developer.apple.com/design/human-interface-guidelines/layout), [Atlassian grid](https://atlassian.design/foundations/grid-beta/applying-grid), [Carbon 2x Grid](https://carbondesignsystem.com/elements/2x-grid/overview/).

### GEO-04: Use concentric radii only when geometry is actually concentric
- Authority/scope: conditional heuristic. Platform convention when using SwiftUI concentric shapes or an adopted system such as GitLab Pajamas.
- Applies: a rounded child deliberately inset from a rounded container with shared corner centers and curve family.
- Rule: when the actual aligned inset is p, use inner radius = outer radius - p, subject to clamping. Equivalently outer = inner + p.
- Exceptions: independent components, project radius-token mappings, unequal insets, borders, elliptical or continuous/superellipse curves, overlap reduction, pills, split controls, and optical adjustment.
- Project override: yes. Project components and radius tokens win.
- Isolated check: render real dimensions at responsive states. Inspect shared centers, curve family, clamping, borders, and asymmetric insets.
- Sources: [SwiftUI concentric corner radii](https://developer.apple.com/documentation/swiftui/geometryproxy/concentriccornerradii), [SwiftUI ConcentricRectangle](https://developer.apple.com/documentation/swiftui/concentricrectangle), [CSS box-edge corner shaping](https://www.w3.org/TR/css-backgrounds-3/#corner-shaping), [GitLab Pajamas border guidance](https://design.gitlab.com/product-foundations/border/).

### GEO-05: Respect system insets and safe areas
- Authority/scope: conditional platform constraint.
- Applies: edge-to-edge, full-screen, mobile, spatial, cutout, rounded-display, and system-bar layouts.
- Rule: obtain safe areas and system insets from platform APIs. Keep controls and essential content out of clipped or reserved regions.
- Exceptions: full-bleed media may extend behind system regions when meaning and controls remain safe.
- Project override: ordinary margins may vary. System-feature safety does not become a guessed constant.
- Isolated check: simulate cutouts, rounded corners, system bars, orientation, and the smallest supported display.
- Later proof: verify in the actual host window/device because isolation may not reproduce all insets.
- Sources: [Apple layout](https://developer.apple.com/design/human-interface-guidelines/layout), [Android rounded-corner insets](https://developer.android.com/develop/ui/views/layout/insets/rounded-corners).

### GEO-06: Separate visible geometry from activation geometry
- Authority/scope: platform convention plus activated target-size requirements.
- Applies: icon buttons, compact controls, small glyphs, dense toolbars.
- Rule: a visible glyph may remain small while its wrapper supplies the applicable pointer/touch target. Measure the actual interactive region rather than the artwork.
- Exceptions: noninteractive decoration and the exact exceptions of the adopted standard/platform.
- Project override: glyph size follows the project. Target behavior follows the active standard/platform.
- Isolated check: display both visible and hit-region bounds. Test adjacent targets and every supported input mode.
- Sources: [WCAG target size](https://www.w3.org/TR/WCAG22/#target-size-minimum), [Apple buttons](https://developer.apple.com/design/human-interface-guidelines/buttons), [Android accessibility](https://developer.android.com/design/ui/mobile/guides/foundations/accessibility).

### GEO-07: Permit bounded optical correction
- Authority/scope: heuristic.
- Applies: icons beside text, asymmetric visual mass, custom shapes, baseline alignment.
- Rule: after applying the project system, allow the smallest explainable optical adjustment needed for visual balance. Do not disguise arbitrary layout drift as optical correction.
- Exceptions: none beyond preserving requirements and responsive behavior.
- Project override: yes. Prefer an existing project token or component fix when the issue repeats.
- Isolated check: compare before/after at multiple sizes, themes, directions, and neighboring components.
- Sources: [Atlassian optical spacing](https://atlassian.design/foundations/grid-beta/applying-grid), [Apple icon guidance](https://developer.apple.com/design/human-interface-guidelines/icons).
