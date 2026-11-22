/**
 * Storage that cannot take the page down with it (internal design notes).
 *
 * `window.localStorage` does not return null when it is unavailable -- it
 * throws, and it throws on the property access as well as on the call. Safari
 * in Private Browsing is the case that matters here, because it is one of the
 * browsers the site was reported broken in.
 *
 * Every read in the app was already wrapped. The writes were not, and one of
 * them sat in `SearchBar`'s suggestion handler *before* the call that opens the
 * site -- so in a private window, clicking a search result threw, the site
 * never opened, and nothing appeared in the interface to say why.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  readStored,
  readStoredJSON,
  removeStored,
  writeStored,
  writeStoredJSON,
} from './storage';

/** Replaces `window.localStorage` with one that throws, as Safari's does. */
function withHostileStorage() {
  const throwing = {
    getItem() { throw new DOMException('The operation is insecure.', 'SecurityError'); },
    setItem() { throw new DOMException('The operation is insecure.', 'SecurityError'); },
    removeItem() { throw new DOMException('The operation is insecure.', 'SecurityError'); },
  };
  vi.spyOn(window, 'localStorage', 'get').mockReturnValue(throwing);
}

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('when storage works', () => {
  it('round-trips a string', () => {
    writeStored('k', 'v');
    expect(readStored('k')).toBe('v');
  });

  it('round-trips JSON', () => {
    writeStoredJSON('k', [{ name: 'Konark' }]);
    expect(readStoredJSON('k', [])).toEqual([{ name: 'Konark' }]);
  });

  it('returns the fallback for a key that was never set', () => {
    expect(readStored('missing', 'default')).toBe('default');
    expect(readStoredJSON('missing', [])).toEqual([]);
  });

  it('removes', () => {
    writeStored('k', 'v');
    removeStored('k');
    expect(readStored('k', null)).toBeNull();
  });

  it('treats corrupt JSON as absent rather than throwing on every render', () => {
    window.localStorage.setItem('k', '{"half-writ');
    expect(readStoredJSON('k', [])).toEqual([]);
  });
});

describe('when storage throws, as it does in a private window', () => {
  it('reads the fallback instead of propagating', () => {
    withHostileStorage();
    expect(readStored('k', 'fallback')).toBe('fallback');
    expect(readStoredJSON('k', [])).toEqual([]);
  });

  it('drops a write silently, so the caller carries on', () => {
    // This is the assertion that matters: the line after the write in
    // `SearchBar` is the one that actually opens the site.
    withHostileStorage();
    expect(() => writeStored('k', 'v')).not.toThrow();
    expect(() => writeStoredJSON('k', { a: 1 })).not.toThrow();
    expect(() => removeStored('k')).not.toThrow();
  });
});
