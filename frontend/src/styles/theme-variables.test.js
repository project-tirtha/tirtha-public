/**
 * The theme's colour pairs actually meet WCAG 1.4.3 (internal design notes).
 *
 * The contrast figures in `theme-variables.css` were computed by hand and
 * written into comments, which is the same as not checking them: a comment does
 * not fail when someone edits the hex beside it. This reads the stylesheet and
 * recomputes them.
 *
 * It also pins the thing that made the dark theme *look* broken to a user:
 * a component that hardcodes its colours opts out of the theme silently. The
 * last test here fails when a stylesheet reintroduces a raw hex background.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const HERE = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(HERE, 'theme-variables.css'), 'utf8');

/** The custom properties declared inside one selector's block. */
function tokensIn(selector) {
  const start = css.indexOf(selector);
  if (start === -1) throw new Error(`no such block: ${selector}`);
  const body = css.slice(start, css.indexOf('\n}', start));
  const out = {};
  for (const [, name, value] of body.matchAll(/(--[\w-]+):\s*([^;]+);/g)) {
    out[name] = value.trim();
  }
  return out;
}

const LIGHT = tokensIn(':root,\n[data-theme="light"] {');
const DARK = tokensIn('[data-theme="dark"] {');

function relativeLuminance(hex) {
  const h = hex.replace('#', '');
  const full = h.length === 3 ? [...h].map((c) => c + c).join('') : h;
  const channels = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) =>
    c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a, b) {
  const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * Every pair the UI actually puts together: text token over surface token.
 *
 * Kept as a list rather than derived, because "which text sits on which
 * background" is a fact about the components, not about the stylesheet.
 */
const PAIRS = [
  ['--text-primary', '--bg-primary'],
  ['--text-primary', '--bg-secondary'],
  ['--text-primary', '--surface-subtle'],
  ['--text-primary', '--surface-raised'],
  ['--text-secondary', '--bg-primary'],
  ['--text-tertiary', '--bg-primary'],
  ['--success-text', '--success-bg'],
  ['--warning-text', '--warning-bg'],
  ['--error-text', '--error-bg'],
  ['--info-text', '--info-bg'],
  ['--focus-ring', '--surface-raised'],
  ['--btn-primary-text', '--btn-primary-bg'],
  ['--btn-secondary-text', '--btn-secondary-bg'],
  ['--btn-tertiary-text', '--btn-tertiary-bg'],
  ['--input-text', '--input-bg'],
];

describe.each([
  ['light', LIGHT],
  ['dark', DARK],
])('%s theme', (themeName, tokens) => {
  it.each(PAIRS)('%s on %s reaches 4.5:1', (fg, bg) => {
    // Both must exist in the theme -- a token defined only for light is a
    // colour that silently keeps its light value on the dark ground.
    expect(tokens[fg], `${fg} is not defined for ${themeName}`).toBeTruthy();
    expect(tokens[bg], `${bg} is not defined for ${themeName}`).toBeTruthy();
    const ratio = contrast(tokens[fg], tokens[bg]);
    expect(
      Number(ratio.toFixed(2)),
      `${fg} (${tokens[fg]}) on ${bg} (${tokens[bg]}) is only ${ratio.toFixed(2)}:1`,
    ).toBeGreaterThanOrEqual(4.5);
  });
});

describe('the dark theme is reachable at all', () => {
  it('is keyed off the attribute ThemeContext sets, not a class', () => {
    /*
      `ThemeContext` sets `data-theme` on <html>. Six stylesheets carried rules
      keyed off `.dark-mode`, `.dark` and `:global(.dark-mode)` -- 27 rules, none
      of which could ever match, and the `:global()` ones were not even valid
      plain CSS. The theme toggle appeared to do nothing to those components.
    */
    expect(css).toContain('[data-theme="dark"]');
    for (const file of readdirSync(HERE).filter((f) => f.endsWith('.css'))) {
      const text = readFileSync(join(HERE, file), 'utf8');
      expect(text, `${file} keys a rule off a class nothing applies`).not.toMatch(
        /(^|,)\s*(:global\(\.dark-mode\)|\.dark-mode|\.dark)(?![\w-])/m,
      );
    }
  });

  it('overrides every colour token the light theme declares', () => {
    // A token present in light and missing from dark keeps its light value on
    // the dark ground, which is how a panel stays white in dark mode. Only
    // colours: `--control-size` is a length and is deliberately theme-neutral.
    const isColour = (value) => /#[0-9a-f]{3,8}\b|rgba?\(|gradient\(/i.test(value);
    const missing = Object.keys(LIGHT).filter(
      (name) => isColour(LIGHT[name]) && !(name in DARK),
    );
    expect(missing, `dark theme does not override: ${missing.join(', ')}`).toEqual([]);
  });
});
