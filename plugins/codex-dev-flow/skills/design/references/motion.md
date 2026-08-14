# Motion rules


### MOTION-01 — Use motion for a named purpose
- Authority/scope: heuristic/platform convention.
- Applies: transitions, state changes, feedback, navigation, loading, and spatial continuity.
- Rule: every motion effect should clarify feedback, continuity, hierarchy, or state; remove decorative motion that competes with the task and use project motion tokens.
- Exceptions: explicitly expressive product moments that the user approves and that preserve access requirements.
- Project override: yes; project motion language and product tone are authoritative.
- Isolated check: compare with motion disabled and ask what information or feedback is lost; inspect interruption and repeated use.
- Sources: [Apple Motion](https://developer.apple.com/design/human-interface-guidelines/motion), [Carbon Motion](https://carbondesignsystem.com/elements/motion/overview/).

### MOTION-02 — Give users control over auto-running movement and updates
- Authority/scope: activated WCAG Level A norm.
- Applies: moving, blinking, or scrolling content that starts automatically, lasts more than five seconds, and appears beside other content; auto-updating parallel information.
- Rule: provide pause/stop/hide; for auto-updating content also allow frequency control where applicable.
- Exceptions: essential activity and the criterion's preload/parallel-content conditions.
- Project override: control styling may vary; the active outcome may not.
- Isolated check: run beyond five seconds, pause/stop/hide, control update frequency, and continue the task.
- Sources: [WCAG Pause, Stop, Hide](https://www.w3.org/TR/WCAG22/#pause-stop-hide).

### MOTION-03 — Keep flashing below the active safety threshold
- Authority/scope: activated WCAG Level A norm.
- Applies: flashing content and animations.
- Rule: do not exceed three flashes in any one-second period unless below the criterion's general/red-flash thresholds.
- Exceptions: only the exact threshold conditions.
- Project override: no when active.
- Isolated check: frame capture and frequency/luminance/red-transition/area analysis at representative sizes.
- Later proof: cumulative full-page flashing area.
- Sources: [WCAG Three Flashes or Below Threshold](https://www.w3.org/TR/WCAG22/#three-flashes-or-below-threshold).

### MOTION-04 — Respect reduced-motion behavior when supported or required
- Authority/scope: conditional constraint/platform convention; WCAG interaction animation disablement is AAA.
- Applies: environments exposing reduced-motion preference and products promising that support.
- Rule: remove or replace nonessential translation, scale, parallax, and repetitive motion while preserving information, feedback, and final state.
- Exceptions: genuinely essential animation, with the least harmful supported treatment.
- Project override: exact replacement follows the project; ignoring a bound user preference does not.
- Isolated check: emulate reduced motion and inspect every transition, loader, parallax, and state change.
- Sources: [Media Queries prefers-reduced-motion](https://www.w3.org/TR/mediaqueries-5/#prefers-reduced-motion), [WCAG Animation from Interactions AAA](https://www.w3.org/TR/WCAG22/#animation-from-interactions), [Apple Motion](https://developer.apple.com/design/human-interface-guidelines/motion).

### MOTION-05 — Preserve control and final state under interruption
- Authority/scope: heuristic.
- Applies: interactive transitions, async progress, drag feedback, and repeated state changes.
- Rule: motion must not delay required input, trap the user, or leave an ambiguous state when interrupted or rapidly reversed.
- Exceptions: an essential timed activity with explicit timing requirements.
- Project override: yes within active interaction/timing obligations.
- Isolated check: rapid toggle, cancellation, repeated activation, navigation away/back, and reduced-motion path.
- Sources: [Apple Motion](https://developer.apple.com/design/human-interface-guidelines/motion), [Carbon Motion](https://carbondesignsystem.com/elements/motion/overview/).
