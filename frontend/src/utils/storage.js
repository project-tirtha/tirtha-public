/**
 * `localStorage`, for the things it is actually safe to lose.
 *
 * Every accessor here is wrapped, because `window.localStorage` does not merely
 * return null when it is unavailable -- it *throws*, on the property access as
 * well as on the call. Safari in Private Browsing, Firefox with
 * `dom.storage.enabled` off, an embedded webview with site data blocked, and a
 * page in a sandboxed iframe all do this.
 *
 * The read sites in this app were already wrapped. The writes were not, and one
 * of them was in `SearchBar`'s suggestion handler -- so in a private window,
 * clicking a search result threw before it ever called `onSearchSelect`, and
 * the site simply did not open. No error surfaced; the click did nothing.
 *
 * Everything stored through here is a convenience -- a remembered panel state,
 * a search history, a chosen basemap. None of it is worth a broken page, so a
 * failed write is dropped rather than reported.
 */

export function readStored(key, fallback = null) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? fallback : raw;
  } catch {
    return fallback;
  }
}

/** Reads and parses JSON, returning `fallback` for missing *or* corrupt data. */
export function readStoredJSON(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return fallback;
    return JSON.parse(raw);
  } catch {
    // Corrupt JSON lands here too, which is the point: a half-written history
    // should not throw on every render thereafter.
    return fallback;
  }
}

export function writeStored(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Unavailable, or the quota is full. Neither is worth surfacing.
  }
}

export function writeStoredJSON(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* see writeStored */
  }
}

export function removeStored(key) {
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* see writeStored */
  }
}
