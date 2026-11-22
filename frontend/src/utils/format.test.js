/**
 * The formatters that used to exist two and three times over (internal design notes).
 *
 * The point of these is less "does it round correctly" than "is there one of
 * it". Each case below is a place where the duplicate copies had already
 * diverged and produced different output for the same input.
 */
import { describe, expect, it } from 'vitest';

import { formatBytes, placeLabel } from './format';

describe('formatBytes', () => {
  it('does not round a small file up to the next unit', () => {
    /*
      The divergence that mattered. `getFileSizeLabel` did
      `Math.round(size / 1024) + ' KB'`, so a 900-byte file read "1 KB" and a
      1,536-byte one read "2 KB" -- and it rendered in the same dialog as the
      `Intl` total, which said 1.5 kB for the second. Two numbers for one file.
    */
    expect(formatBytes(900, 'en')).toMatch(/900/);
    expect(formatBytes(1536, 'en')).toMatch(/1\.5/);
  });

  it('names the unit in the reading language', () => {
    // Odia has its own word for "byte"; the old formatter concatenated a
    // hardcoded English abbreviation regardless of language.
    expect(formatBytes(900, 'or')).not.toBe(formatBytes(900, 'en'));
  });

  it('picks the unit from the magnitude', () => {
    expect(formatBytes(900, 'en')).toMatch(/900/);
    expect(formatBytes(5_242_880, 'en')).toMatch(/5/);
    expect(formatBytes(5_242_880, 'en')).not.toMatch(/5242880/);
  });

  it('does not report a fraction of a byte', () => {
    expect(formatBytes(900, 'en')).not.toMatch(/900\./);
  });

  it('survives the values a file input actually produces', () => {
    // An empty file is 0 bytes, and `Math.log(0)` is -Infinity.
    for (const value of [0, -1, null, undefined, NaN, '']) {
      expect(() => formatBytes(value, 'en')).not.toThrow();
      expect(formatBytes(value, 'en')).toMatch(/0/);
    }
  });
});

describe('placeLabel', () => {
  it('joins the administrative fields the API publishes', () => {
    expect(placeLabel({ district: 'Puri', state: 'Odisha', country: 'India' })).toBe(
      'Puri, Odisha, India',
    );
  });

  it('skips the fields a site has not had filled in', () => {
    // 29 of 59 sites are incompletely located; the label must not read
    // "Puri, , India".
    expect(placeLabel({ district: 'Puri', country: 'India' })).toBe('Puri, India');
  });

  it('falls back rather than rendering an empty line', () => {
    expect(placeLabel({})).toBe('India');
    expect(placeLabel()).toBe('India');
  });
});
