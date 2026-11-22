/**
 * Whether the visitor has asked the system for less animation (WCAG 2.3.3).
 *
 * Read at call time rather than cached: the setting is a live media query, and
 * someone who turns it on mid-session means it now.
 *
 * `index.css` already collapses CSS animations and transitions under the same
 * media query, but a scripted camera move is invisible to that rule. The map's
 * `flyTo` was a 900ms swoop across the country regardless of the preference --
 * the single largest piece of motion on the site, and the one most likely to
 * cause trouble for the people who set it.
 */
export function prefersReducedMotion() {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
  );
}

/** A `flyTo`/`easeTo` duration that honours the preference. */
export function motionDuration(milliseconds) {
  return prefersReducedMotion() ? 0 : milliseconds;
}
