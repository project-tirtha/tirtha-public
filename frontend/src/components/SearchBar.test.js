/**
 * The two things about the search box that outlive the click that made them:
 * what a row in the history is worth when it is clicked again, and whether the
 * highlighted option can be seen at all.
 *
 * Both are states nothing else pins. The recent list is written by one handler
 * and read by another, so a field dropped on the way in fails silently on the
 * way out; and the highlight is a CSS class with no DOM focus behind it, so no
 * focus test would notice it going invisible.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { fireEvent, render, screen } from '@testing-library/react';
import React, { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SearchBar from './SearchBar';
import '../i18n/config';

const KONARK = {
  id: 'abc123',
  name: 'Konark Sun Temple',
  location: 'Puri, Odisha',
  lat: 19.8876,
  lng: 86.0945,
};

const PASTED_LINK = 'https://maps.google.com/?q=18.497500,73.847500';

/**
 * `Home` owns the query, the focus flag and the highlight; the component takes
 * all three as props and has no fallback for them, so the test has to own them
 * too.
 */
function Harness({ onSearchSelect }) {
  const [searchQuery, setSearchQuery] = useState('');
  const [isFocused, setIsFocused] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  return React.createElement(SearchBar, {
    templeList: [KONARK],
    onSearchSelect,
    searchQuery,
    setSearchQuery,
    isFocused,
    setIsFocused,
    activeIndex,
    setActiveIndex,
  });
}

describe('the recent searches list', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('replays a pasted coordinate as a coordinate, not as a site', () => {
    /*
      The row is stored under the pasted text as its name, which matches no
      site, so clicking it hands `Home` the stored entry itself. If that entry
      has lost `isCustomLocation`, `Home` skips the request-site form and opens
      the 3D viewer on a site that does not exist.
    */
    const onSearchSelect = vi.fn();
    const first = render(React.createElement(Harness, { onSearchSelect }));

    fireEvent.change(screen.getByRole('combobox'), { target: { value: PASTED_LINK } });
    fireEvent.mouseDown(screen.getByRole('option'));
    expect(onSearchSelect.mock.calls[0][0].isCustomLocation).toBe(true);

    // Remounted, because the history goes through `localStorage`: the fields
    // are dropped on the way in, and the round trip is where that shows.
    first.unmount();
    render(React.createElement(Harness, { onSearchSelect }));
    fireEvent.focus(screen.getByRole('combobox'));
    fireEvent.click(screen.getByText(PASTED_LINK));

    expect(onSearchSelect).toHaveBeenCalledTimes(2);
    expect(onSearchSelect.mock.calls[1][0]).toMatchObject({
      isCustomLocation: true,
      lat: 18.4975,
      lng: 73.8475,
    });
  });

  it('keeps the id a site row needs to resolve its model', async () => {
    // The same hole reached from the other side: a stored site whose name has
    // since changed falls back to the entry, and an entry with no `id` opens a
    // viewer whose run lookup returns immediately.
    const onSearchSelect = vi.fn();
    const first = render(React.createElement(Harness, { onSearchSelect }));

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'Konark Sun Temple' } });
    // Awaited: a site suggestion arrives on the debounced query, not the typed
    // one. The coordinate above skips the debounce, which is why it does not.
    fireEvent.mouseDown(await screen.findByRole('option'));
    first.unmount();

    const [stored] = JSON.parse(window.localStorage.getItem('templeSearchHistory'));
    expect(stored.id).toBe('abc123');
  });
});

const HERE = dirname(fileURLToPath(import.meta.url));
const searchCss = readFileSync(join(HERE, '../styles/SearchBar.css'), 'utf8');
const themeCss = readFileSync(join(HERE, '../styles/theme-variables.css'), 'utf8');

/** The value of one custom property inside one theme's block. */
function token(selector, name) {
  const start = themeCss.indexOf(selector);
  const body = themeCss.slice(start, themeCss.indexOf('\n}', start));
  return body.match(new RegExp(`${name}:\\s*([^;]+);`))[1].trim();
}

function contrast(a, b) {
  const luminance = (hex) => {
    const [r, g, b] = [1, 3, 5]
      .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe('the highlighted suggestion', () => {
  /*
    The highlight moves with `aria-activedescendant` while DOM focus stays on
    the input, so the option never matches `:focus-visible` and never gets the
    outline `index.css` gives everything else. Whatever marks it here *is* the
    focus indicator, and WCAG 1.4.11 puts that at 3:1 -- which the background
    change alone cannot reach in either theme.
  */
  const at = searchCss.lastIndexOf('.suggestion-item.active {');
  const activeRule = searchCss.slice(at, searchCss.indexOf('}', at));

  it('is marked by more than a background change', () => {
    expect(activeRule, 'the active option declares no outline').toMatch(
      /outline:.*var\(--focus-ring/,
    );
  });

  it.each([
    ['light', ':root,\n[data-theme="light"] {'],
    ['dark', '[data-theme="dark"] {'],
  ])('reaches 3:1 against the row it sits on, %s theme', (themeName, selector) => {
    const ratio = contrast(token(selector, '--focus-ring'), token(selector, '--bg-hover'));
    expect(
      Number(ratio.toFixed(2)),
      `the ${themeName} indicator is only ${ratio.toFixed(2)}:1`,
    ).toBeGreaterThanOrEqual(3);
  });
});
