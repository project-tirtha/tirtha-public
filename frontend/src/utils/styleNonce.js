/**
 * Let one third-party library inject its own `<style>` under CSP.
 *
 * `@mkkellogg/gaussian-splats-3d` builds three stylesheets at construction --
 * `.progressBarOuterContainer`, `.spinnerOuterContainer0`, `.infoPanel` -- for
 * a loading UI this app disables (`showLoadingUI: false`). `style-src` is
 * `'self'` plus a per-response nonce, so all three are refused, and every model
 * load prints six CSP errors. Nothing breaks: the styles belong to widgets that
 * are never shown. The cost is noise in the one channel the cutover runbook
 * tells an operator to watch for *real* violations.
 *
 * The alternatives are worse. `'unsafe-inline'` in `style-src` would admit any
 * injected stylesheet, and an injected stylesheet is a genuine exfiltration
 * channel -- attribute selectors with `background-image` URLs read form values
 * out one character at a time. Hashes would pin us to a library version.
 *
 * So: stamp the nonce the server already issued onto style elements, at
 * creation, for the duration of the call that makes them. A browser reads the
 * `nonce` attribute when the element is *inserted*, so setting it afterwards
 * (a MutationObserver, say) is too late -- it has to be at `createElement`.
 *
 * Scoped deliberately. The patch is installed around one constructor and
 * removed in a `finally`, so it cannot silently bless a stylesheet injected by
 * anything else, later.
 */
function nonce() {
  return document.querySelector('meta[name="csp-nonce"]')?.content ?? '';
}

export function withStyleNonce(fn) {
  const value = nonce();
  if (!value || typeof document === 'undefined') return fn();

  const create = document.createElement.bind(document);
  document.createElement = (tag, options) => {
    const el = create(tag, options);
    if (String(tag).toLowerCase() === 'style') el.setAttribute('nonce', value);
    return el;
  };

  try {
    return fn();
  } finally {
    document.createElement = create;
  }
}
