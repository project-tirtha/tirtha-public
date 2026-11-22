/**
 * The formatters more than one place needs, in one place.
 *
 * Each of these existed two or three times over, and had begun to disagree:
 *
 *   - `ContributeModal` formatted the same quantity two ways in the same
 *     dialog. `getFileSizeLabel` did `Math.round(size / 1024) + ' KB'`, so a
 *     1,536-byte file read "2 KB"; `formatBytes` used `Intl` and said 1.5 kB.
 *     Both were on screen at once -- a per-file size beside a total that had
 *     rounded differently, and neither translated the unit.
 *
 * (`deploymentPrefix` had the same problem -- exported from two modules and
 * implemented a third time privately inside `apiConfig`, with only one of the
 * three guarding against `document` being undefined. It now has one definition
 * too, in `deployment.js`, which is where "where is this mounted" belongs.)
 *
 * Duplication is not the problem by itself; duplication that has already
 * drifted is, because the next edit lands on one copy.
 */

/**
 * A byte count in the reading language.
 *
 * `Intl` knows each language's unit name and decimal separator; concatenating
 * ' KB' onto a JavaScript-default number knew neither, so Odia readers got an
 * English abbreviation and the rounding lost small files entirely.
 */
export function formatBytes(bytes, language = 'en') {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) {
    return new Intl.NumberFormat(language, {
      style: 'unit',
      unit: 'byte',
      maximumFractionDigits: 0,
    }).format(0);
  }
  const units = ['byte', 'kilobyte', 'megabyte', 'gigabyte'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
  return new Intl.NumberFormat(language, {
    style: 'unit',
    unit: units[index],
    maximumFractionDigits: index === 0 ? 0 : 1,
  }).format(value / 1024 ** index);
}

/**
 * The one-line place label under a site's name.
 *
 * Built from the administrative fields rather than a free-text location string:
 * the API publishes `district`, `state` and `country` separately, and any of
 * them may be missing. `'India'` is the fallback because every site in the
 * archive is in India -- if that stops being true, this is the line to change.
 */
export function placeLabel({ district, state, country } = {}) {
  return [district, state, country].filter(Boolean).join(', ') || 'India';
}
