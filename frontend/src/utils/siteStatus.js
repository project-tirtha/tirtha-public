/**
 * What the map says about a site, and how it draws it.
 *
 * Shared rather than local to `MapView`, because a second surface that shows
 * the same three states must not invent its own palette for them. That has
 * already happened once here: the old `MapLegend` described the pins with
 * #27ae60, #f39c12 and #3498db -- a different green, amber and blue from the
 * pins it was a key to, which is the one thing a key must not get wrong.
 *
 * `map_status` is computed server-side from the site's runs (internal design notes):
 * `published` when a run has a published model, `in_progress` while a
 * reconstruction is under way, `needs_images` when there is nothing yet --
 * which is the state that asks the visitor to contribute.
 */

/*
  Measured against a light basemap: 4.37, 4.38 and 5.84:1, all clear of the 3:1
  a non-text element needs (WCAG 1.4.11).
*/
export const MARKER_COLOURS = {
  published: '#15803d',
  in_progress: '#b45309',
  /*
    Enough photographs, nothing reconstructed from them. 8.7:1 on white and
    clearly not the blue beside it.

    This state existed in the data all along and the map had no word for it:
    four sites were drawn as "needs images -- contribute here" while holding
    278 to 541 photographs each, eleven to twenty-one times the minimum. Asking
    those contributors for more was the one response that could not help.
  */
  awaiting_run: '#6b21a8',
  needs_images: '#1d4ed8',
};

/*
  A glyph per status, drawn inside the pin's white disc.

  The three fills are legible against the basemap but are 1.0, 1.34 and 1.33:1
  against *each other*. Green and amber at identical luminance are the same pin
  to a visitor with deuteranopia or protanopia, and telling those three states
  apart is this map's whole purpose (WCAG 1.4.1: colour must not be the only
  visual channel).

  A filled disc, a half disc and an outline ring: they differ in shape at 25px
  and they survive greyscale.
*/
const MARKER_GLYPHS = {
  published: (c) => `<circle cx="12.5" cy="11.5" r="2.6" fill="${c}"/>`,
  in_progress: (c) =>
    `<path d="M12.5 8.9a2.6 2.6 0 0 1 0 5.2z" fill="${c}"/>` +
    `<circle cx="12.5" cy="11.5" r="2.6" fill="none" stroke="${c}" stroke-width="1.1"/>`,
  /*
    A square. The other three are round -- a filled disc, a half disc, an
    outline ring -- so a corner is the clearest fourth thing to be at 25px, and
    it survives greyscale like the rest.
  */
  awaiting_run: (c) => `<rect x="10.1" y="9.1" width="4.8" height="4.8" fill="${c}"/>`,
  needs_images: (c) =>
    `<circle cx="12.5" cy="11.5" r="2.6" fill="none" stroke="${c}" stroke-width="1.4"/>`,
};

/** The status of a site, defaulting to the one that asks for help. */
export function completionStatus(temple) {
  return temple?.mapStatus || 'needs_images';
}

export function buildMarkerDataUrl(fillColor, status = null) {
  /*
    The colour is written into each shape rather than inherited from a `color`
    attribute via `currentColor`. Both work, but a data-URI SVG is a standalone
    document rendered by an image decoder rather than the page's CSS engine, and
    the failure mode of getting that wrong is an invisible glyph -- which is
    precisely the thing this exists to prevent.
  */
  const glyph = MARKER_GLYPHS[status]?.(fillColor) ?? '';
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="25" height="41" viewBox="0 0 25 41">` +
      `<path d="M12.5 0C6.148 0 1 5.148 1 11.5c0 8.176 10.338 20.523 11.025 21.337a.625.625 0 0 0 .95 0C13.662 32.023 24 19.676 24 11.5 24 5.148 18.852 0 12.5 0z" fill="${fillColor}" stroke="#ffffff" stroke-width="2"/>` +
      `<circle cx="12.5" cy="11.5" r="4.6" fill="#ffffff"/>${glyph}</svg>`,
  )}`;
}

/** The pin a site is drawn with, on the map and anywhere else it is listed. */
export function markerFor(temple) {
  const status = completionStatus(temple);
  return { status, src: buildMarkerDataUrl(MARKER_COLOURS[status], status) };
}
