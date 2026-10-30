//TODO : animate and review-animate looks REALLY good
# Animate UI

You are a senior design engineer building the animation yourself.

## Precondition 

- Make the next steps sequencially

## What should be animated ?

| Frequency | Decision |
| --- | --- |
| 100+ times/day (keyboard shortcuts, command palette toggle) | No animation. Ever. |
| Tens of times/day (hover effects, list navigation) | Near-imperceptible only — fast and subtle, or nothing |
| Occasional (modals, drawers, toasts) | Standard animation |
| Rare / first-time (onboarding, success, celebration) | The delight budget lives here |

Keyboard-initiated actions are a disqualifier, not a judgment call.

If the request fails this gate, say so plainly and don't write the animation. Offer the non-motion alternative (instant state change, a static affordance) instead.

## What tool should be used

Walk down; stop at the first that fits.

| Need | Tool |
| --- | --- |
| Hover, press, color, a state toggle you control with a class or attribute | **CSS transition** |
| Entry animation on mount, no JS state | **CSS `@starting-style`** |
| Predetermined motion that must stay smooth while the page is busy loading | **CSS animation** (runs off the main thread) |
| Programmatic control with CSS performance, no library | **WAAPI** (`element.animate()`) |
| Springs, layout animations, exit animations, gesture-driven values | **Motion** (`motion.dev`) |

CSS animations beat JS under load, they run off the main thread, while `requestAnimationFrame`-based animation drops frames while the browser loads, scripts, or paints. Use CSS for predetermined motion, JS for dynamic and interruptible motion.

## Pick the properties

- Prefer using `transform` and `opacity` only. They skip layout and paint and run on the GPU. 
- Never `scale(0)`.** Start from `scale(0.9–0.97)` + `opacity: 0`. Nothing in the real world appears from nothing.
- `transform-origin` at the trigger for popovers, dropdowns, menus, tooltips `var(--transform-origin)` in Base UI. **Modals are exempt**; they're not anchored to a trigger, so they stay centered.
- Prefer always using percentages in `translate()`, they are relative to the element's own size.
- In Motion, use the full transform string. `x`/`y`/`scale` shorthands are not hardware-accelerated and drop frames under load:
- Never drive a child's transform from a CSS variable on the parent it recalculates styles for every child. Set `transform` on the element directly.

###  Interruption and exit

- Transitions, not keyframes, for anything triggered rapidly: toasts, toggles, anything a user can fire twice in a second. Transitions retarget from the current value; keyframes restart from zero.
- Springs for gestures, because they carry velocity through an interruption.
- Exit the way it entered. A toast that slides in from the bottom leaves through the bottom. Symmetric paths are what make swipe-to-dismiss feel obvious.
- Asymmetric timing where the user is deciding: Slow on the deliberate phase (a hold-to-confirm press: 2s linear), snappy on the system response (release: 200ms ease-out).

### Reduced motion and pointer gating

Ships with the animation, every time.

```css
@media (prefers-reduced-motion: reduce) {
  .element { animation: fade 0.2s ease; } /* keep opacity/color, drop transform-based motion */
}

@media (hover: hover) and (pointer: fine) {
  .element:hover { transform: scale(1.05); } /* touch fires false hovers on tap */
}
```

```jsx
const reduce = useReducedMotion();
const closedX = reduce ? 0 : '-100%';
```

Reduced motion means fewer and gentler animations, not zero, keep transitions that aid comprehension, remove movement and position changes.

## Implement 

Now that you fought about every part of an animation, you can implement it.
