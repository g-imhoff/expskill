# Geometry rules

### GEO-01: Use the project spacing system
- Scope: margin, padding, gap, density, layout. Dynamic safe-area insets, fluid proportions, content-derived sizing, or a documented missing token excepted. The project defines the scale, no external base is universal.
- Rule: use the project's spacing tokens and layout primitives before adding raw values or importing another system's scale.
- Check: inspect token use, render at supported densities, themes, boundary widths.
- Sources: [Carbon spacing](https://carbondesignsystem.com/elements/spacing/overview/).

### GEO-02: Let distance express relationship
- Scope: groups, sections, labels/controls, repeated items, hierarchy. Intentional density, diagrams, spatial data, user-controlled density excepted. Rhythm follows the project.
- Rule: keep strongly related items closer than separate groups. Use repeated spacing patterns to signal equal relationship and larger separation to signal hierarchy.
- Check: view without borders/decoration, grouping must stay understandable.
- Sources: [Carbon spacing relationships](https://carbondesignsystem.com/elements/spacing/overview/).

### GEO-03: Align meaningful structures
- Scope: top-level containers, repeated rows/columns, text and control alignment. Optical correction, fluid diagrams, asymmetric editorial layouts, independent elements excepted. Project grid and primitives are authoritative.
- Rule: align related content to project grid lines or shared edges. Preserve meaningful alignment as content and viewport change.
- Check: overlay alignment guides at narrow/wide widths and with long content.
- Sources: [Atlassian grid](https://atlassian.design/foundations/grid-beta/applying-grid).

### GEO-04: Use concentric radii only when geometry is actually concentric
- Scope: a rounded child deliberately inset from a rounded container with shared corner centers and curve family. Independent components, radius-token mappings, unequal insets, borders, elliptical/continuous curves, overlap reduction, pills, split controls, optical adjustment excepted. Project tokens win.
- Rule: when the actual aligned inset is p, use inner radius = outer radius - p, subject to clamping. Equivalently outer = inner + p.
- Check: render real dimensions at responsive states, inspect centers, curve family, clamping, borders, asymmetric insets.
- Sources: [SwiftUI concentric corner radii](https://developer.apple.com/documentation/swiftui/geometryproxy/concentriccornerradii).

### GEO-05: Respect system insets and safe areas
- Scope: edge-to-edge, full-screen, mobile, spatial, cutout, rounded-display, system-bar layouts. Full-bleed media may extend behind system regions when meaning and controls stay safe. Safety is never a guessed constant.
- Rule: obtain safe areas and system insets from platform APIs. Keep controls and essential content out of clipped or reserved regions.
- Check: simulate cutouts, rounded corners, system bars, orientation, smallest display. Verify in the actual host window/device later.
- Sources: [Apple layout](https://developer.apple.com/design/human-interface-guidelines/layout).

### GEO-06: Separate visible geometry from activation geometry
- Scope: icon buttons, compact controls, small glyphs, dense toolbars, noninteractive decoration and exact standard/platform exceptions excluded. Glyph size follows the project, target behavior follows the active standard/platform.
- Rule: a visible glyph may remain small while its wrapper supplies the applicable pointer/touch target. Measure the actual interactive region rather than the artwork.
- Check: display visible and hit-region bounds, test adjacent targets and every supported input mode.
- Sources: [WCAG target size](https://www.w3.org/TR/WCAG22/#target-size-minimum).

### GEO-07: Permit bounded optical correction
- Scope: icons beside text, asymmetric visual mass, custom shapes, baseline alignment. Never a cover for arbitrary drift, prefer an existing token or component fix when the issue repeats.
- Rule: after applying the project system, allow the smallest explainable optical adjustment needed for visual balance. Do not disguise arbitrary layout drift as optical correction.
- Check: compare before/after at multiple sizes, themes, directions, neighboring components.
- Sources: [Apple icon guidance](https://developer.apple.com/design/human-interface-guidelines/icons).
