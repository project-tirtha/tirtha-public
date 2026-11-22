/**
 * One live region, mounted for the life of the page.
 *
 * A `role="status"` element that is *created together with its text* announces
 * nothing: assistive technology watches a live region for changes, and a region
 * that did not exist a moment ago has not changed, it has appeared. Several
 * places in this app render a status element conditionally and expect it to be
 * read -- the map's cluster expansion, the upload's outcome and the reason it
 * is blocked, a site request's result.
 *
 * So the region is always there and only its text changes. Callers push a
 * sentence; nobody has to remember the markup.
 *
 * This shipped with exactly one caller while claiming to have fixed all of
 * them, which was infrastructure described as a repair. The callers listed
 * above are wired now; `useAnnouncement` below is what they use.
 *
 * `polite`, not `assertive`: none of what this carries is urgent enough to
 * interrupt someone mid-sentence, and `assertive` on a map that announces every
 * pan would be unusable.
 */
import { useEffect } from 'react';

const REGION_ID = 'tirtha-announcer';

export function announce(message) {
  if (!message || typeof document === 'undefined') return;
  const region = document.getElementById(REGION_ID);
  if (!region) return;

  /*
    Cleared first, then set on the next frame. Setting the same text twice in a
    row is not a change, so a repeated announcement -- "12 sites" after another
    "12 sites" -- would be silently dropped, which is exactly the case a map
    produces.
  */
  region.textContent = '';
  window.requestAnimationFrame(() => {
    const live = document.getElementById(REGION_ID);
    if (live) live.textContent = message;
  });
}

/**
 * Announce a message whenever it appears or changes.
 *
 * The region above only helps if something pushes to it, and for a while only
 * the map did -- every other conditional `role="status"` in the app went on
 * rendering itself into existence and saying nothing. This is the shape those
 * sites need: keep the visible element exactly where it is, and let the
 * persistent region carry the same sentence to a screen reader.
 *
 * A falsy message announces nothing, so the usual `condition && <p role=
 * "status">` pattern can pass its text straight through.
 */
export function useAnnouncement(message) {
  useEffect(() => {
    announce(message);
  }, [message]);
}

export { REGION_ID };
