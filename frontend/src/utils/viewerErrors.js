/**
 * A viewer failure, as a sentence the visitor can read in their own language.
 *
 * The overlay used to print the raw loader message -- "TypeError: Failed to
 * fetch", "Stalled at 3/112 files ..." -- in English on Hindi and Odia pages.
 * That text is still shown, folded inside a `<details>`, because it is the
 * only thing a visitor can quote back to us; this picks the headline.
 */
const RULES = [
  ['viewer.error.contextLost', /context lost|out of graphics memory/i],
  ['viewer.error.memory', /out of memory|allocation failed|RangeError|array buffer/i],
  ['viewer.error.noStart', /no data arrived|never began/i],
  ['viewer.error.stalled', /stalled at/i],
  ['viewer.error.texture', /transcoder|ktx2|texture decoder|compressed-texture/i],
  ['viewer.error.notFound', /\b404\b|not found/i],
  ['viewer.error.network', /failed to fetch|networkerror|network error|load failed|HTTP \d{3}|\b5\d\d\b|aborted/i],
  ['viewer.error.format', /parse|unexpected token|invalid|unsupported|SyntaxError|magic/i],
];

export function viewerErrorKey(message) {
  const text = String(message ?? '');
  for (const [key, pattern] of RULES) {
    if (pattern.test(text)) return key;
  }
  return 'viewer.error.generic';
}
