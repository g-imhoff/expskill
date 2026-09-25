# Motion rules

### MOTION-01: Use motion for a named purpose
- Scope: transitions, state changes, feedback, navigation, loading, spatial continuity. Expressive product moments need user approval and must preserve access requirements. Project motion language is authoritative.
- Rule: every motion effect should clarify feedback, continuity, hierarchy, or state. Remove decorative motion that competes with the task and use project motion tokens.
- Check: compare with motion disabled, ask what information is lost. Inspect interruption and repeated use.
- Sources: [Apple Motion](https://developer.apple.com/design/human-interface-guidelines/motion).

### MOTION-02: Give users control over auto-running movement and updates
- Scope: moving, blinking, scrolling content starting automatically, lasting over five seconds beside other content, auto-updating parallel information. Essential activity and criterion preload/parallel conditions excepted. Control styling may vary, the outcome may not.
- Rule: provide pause/stop/hide. For auto-updating content also allow frequency control where applicable.
- Check: run beyond five seconds, pause/stop/hide, control update frequency, continue the task.
- Sources: [WCAG Pause, Stop, Hide](https://www.w3.org/TR/WCAG22/#pause-stop-hide).

### MOTION-03: Keep flashing below the active safety threshold
- Scope: flashing content and animations, only the exact threshold conditions excepted.
- Rule: do not exceed three flashes in any one-second period unless below the criterion's general/red-flash thresholds.
- Check: frame capture and frequency/luminance/red-transition/area analysis at representative sizes. Cumulative full-page area needs later proof.
- Sources: [WCAG Three Flashes or Below Threshold](https://www.w3.org/TR/WCAG22/#three-flashes-or-below-threshold).

### MOTION-04: Respect reduced-motion behavior when supported or required
- Scope: environments exposing reduced-motion preference and products promising support, genuinely essential animation excepted with the least harmful treatment. Exact replacement follows the project, ignoring a bound preference does not.
- Rule: remove or replace nonessential translation, scale, parallax, and repetitive motion while preserving information, feedback, and final state.
- Check: emulate reduced motion, inspect every transition, loader, parallax, state change.
- Sources: [Media Queries prefers-reduced-motion](https://www.w3.org/TR/mediaqueries-5/#prefers-reduced-motion).

### MOTION-05: Preserve control and final state under interruption
- Scope: interactive transitions, async progress, drag feedback, repeated state changes. Essential timed activities with explicit timing excepted.
- Rule: motion must not delay required input, trap the user, or leave an ambiguous state when interrupted or rapidly reversed.
- Check: rapid toggle, cancellation, repeated activation, navigation away/back, reduced-motion path.
- Sources: [Carbon Motion](https://carbondesignsystem.com/elements/motion/overview/).
