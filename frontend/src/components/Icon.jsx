import React from 'react';

import '../styles/Icon.css';

/**
 * The interface's icons, drawn inline.
 *
 * They were `<span class="material-icons">close</span>`, which needs Google's
 * icon font — and `shell.html` never linked it. The old jQuery page did; the
 * React shell carried over its meta tags and not its fonts. So all 33 icon
 * spans rendered their *ligature name* as text: "menu", "close", "search",
 * "open_in_new" scattered through the interface, and the viewer header
 * overlapping itself because a word is far wider than a 24px glyph.
 *
 * Inline rather than linking the font, for three reasons that outlast the bug:
 *
 * - **It cannot fail.** A font is a second network request; until it lands the
 *   ligature shows as text, and behind a firewall that blocks Google it shows
 *   as text forever. An icon that degrades to the word "close" is worse than no
 *   icon.
 * - **It removes a third party.** internal design notes self-hosts fonts precisely so a
 *   visitor's IP is not disclosed to Google on every page load, which matters
 *   for the DPDP obligations in 11.2. The `fonts.googleapis.com` allowance in
 *   the CSP was recorded there as a defect rather than a decision; this is what
 *   lets it go.
 * - **Fourteen icons is not a font.** The whole set is under 2 KB of path data,
 *   against ~50 KB of woff2 for glyphs we do not use.
 *
 * Sized in `em` and painted with `currentColor`, so the stylesheet rules that
 * already set `font-size` and `color` on `.material-icons` keep working
 * unchanged — the class name is kept for exactly that reason.
 *
 * These are drawn to match Material's shapes, not copied from them.
 */
const PATHS = {
  menu: 'M3 6h18M3 12h18M3 18h18',
  close: 'M6 6l12 12M18 6L6 18',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14M16.5 16.5L21 21',
  arrow_back: 'M20 12H4M10 6l-6 6 6 6',
  location_on: 'M12 22s7-6.2 7-12a7 7 0 1 0-14 0c0 5.8 7 12 7 12z M12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5',
  add_location_alt:
    'M12 22s7-6.2 7-12a7 7 0 1 0-14 0c0 5.8 7 12 7 12z M12 6.5v7 M8.5 10h7',
  open_in_new: 'M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5',
  dark_mode: 'M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z',
  light_mode:
    'M12 7.5a4.5 4.5 0 1 0 0 9 4.5 4.5 0 0 0 0-9 M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22'
    + 'M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8',
  fullscreen: 'M4 9V5a1 1 0 0 1 1-1h4M20 9V5a1 1 0 0 0-1-1h-4M4 15v4a1 1 0 0 0 1 1h4M20 15v4a1 1 0 0 1-1 1h-4',
  fullscreen_exit: 'M9 4v4a1 1 0 0 1-1 1H4M15 4v4a1 1 0 0 0 1 1h4M9 20v-4a1 1 0 0 0-1-1H4M15 20v-4a1 1 0 0 1 1-1h4',
  cloud_upload:
    'M7 18a4 4 0 0 1-.6-7.95A5.5 5.5 0 0 1 17.4 9.5 3.75 3.75 0 0 1 17 18h-2'
    + 'M12 20v-8M9 15l3-3 3 3',
  image: 'M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z M3 16l5-5 4 4 3-3 6 6 M9 9.5a1.2 1.2 0 1 1-2.4 0 1.2 1.2 0 0 1 2.4 0',
  info: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 11v6 M12 7.6v.9',
  logout: 'M15 4h3a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-3 M11 8l4 4-4 4 M15 12H4',
  check: 'M4 12.5l5 5L20 6.5',
  /*
    A cube in isometric projection: the outline, plus the three edges meeting at
    the front-top vertex. Drawn rather than borrowed from `fullscreen`, which
    means "make this bigger" -- a different promise from "this is a 3D model".
  */
  view_in_ar:
    'M12 2.5l8.5 4.9v9.2L12 21.5l-8.5-4.9V7.4z M12 12l8.5-4.6 M12 12l-8.5-4.6 M12 12v9.5',
  /* The location pin, struck through: this site has no coordinate yet. */
  location_off:
    'M12 22s7-6.2 7-12a7 7 0 0 0-11.9-4.95 M5.2 8.2A7.6 7.6 0 0 0 5 10c0 5.8 7 12 7 12 M4 4l16 16',
  layers: 'M12 3.5l8.5 4.5-8.5 4.5L3.5 8zM3.5 12.5L12 17l8.5-4.5M3.5 16.5L12 21l8.5-4.5',
  expand_more: 'M6 9.5l6 6 6-6',
  expand_less: 'M6 14.5l6-6 6 6',
  /*
    Restore the default view. A house, because the control is called Home and an
    icon that argues with its own label helps nobody -- a crosshair would read as
    "find me", which is a different promise.
  */
  home: 'M3.5 11L12 3.5l8.5 7.5 M5.5 9.5V20a.5.5 0 0 0 .5.5h12a.5.5 0 0 0 .5-.5V9.5',
  /* The paper: a document with lines of text on it. */
  article: 'M6 3h9l3.5 3.5V21a.5.5 0 0 1-.5.5H6a.5.5 0 0 1-.5-.5V3.5A.5.5 0 0 1 6 3z'
    + ' M14.5 3v4h4 M8.5 12h7 M8.5 15.5h7 M8.5 19h4',
  /* The source: angle brackets around a slash, the universal shorthand. */
  code: 'M8.5 8.5L4 12l4.5 3.5 M15.5 8.5L20 12l-4.5 3.5 M13.5 5.5l-3 13',
  /* The room: a speech bubble. */
  forum: 'M20 15.5a1 1 0 0 1-1 1H8l-4 3.5v-14a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1z',
  /* Contact: an envelope. */
  mail: 'M3.5 6h17a.5.5 0 0 1 .5.5v11a.5.5 0 0 1-.5.5h-17a.5.5 0 0 1-.5-.5v-11a.5.5 0 0 1 .5-.5z'
    + ' M3.2 6.8L12 13l8.8-6.2',
  /* Elapsed time: a clock face with hands at ten past ten. */
  schedule: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7.5V12l3.5 2',
  /* A contributor. Head and shoulders, drawn to the same stroke as the rest. */
  person: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M4.5 20.5c0-3.6 3.4-6 7.5-6s7.5 2.4 7.5 6',
  /* The key to the pins: a question mark, since the control answers one. */
  help:
    'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18'
    + ' M9.6 9.2a2.5 2.5 0 1 1 3.2 2.7c-.6.2-.8.7-.8 1.3v.6'
    + ' M12 16.9v.9',
  /*
    Language. A globe with a meridian rather than a flag: a flag claims a
    language belongs to a country, and Odia and English do not divide that way.
  */
  language:
    'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M3.2 9.5h17.6 M3.2 14.5h17.6'
    + ' M12 3a13 13 0 0 1 0 18 M12 3a13 13 0 0 0 0 18',
};

/**
 * `name` is the same string the `material-icons` span used, so replacing a call
 * site is a mechanical swap and an icon that is not in the set fails visibly
 * (nothing renders) rather than as a stray word.
 */
function Icon({ name, className = '', ...rest }) {
  const d = PATHS[name];
  if (!d) return null;

  return (
    <svg
      // Kept so the existing `font-size` and `color` rules still reach it.
      className={`material-icons ${className}`.trim()}
      viewBox="0 0 24 24"
      width="1em"
      height="1em"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      // Decoration: every one of these sits beside a text label or inside a
      // button that already carries an `aria-label`.
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      <path d={d} />
    </svg>
  );
}

export default Icon;
