/**
 * The CSRF token for POSTs back to Django (internal design notes).
 *
 * This existed twice, once inside `ContributeModal` and once inside
 * `RequestSiteModal`, each reading only `document.cookie` and each falling back
 * to `fetch('/api/auth/current-contributor/')` -- an endpoint that does not
 * exist on this backend, so the "fallback" was a guaranteed 404 followed by a
 * second read of the same empty cookie.
 *
 * The meta tag comes first because it is the one that keeps working: the shell
 * view calls `get_token`, which both sets the cookie and renders the value, and
 * a rendered value survives `CSRF_COOKIE_HTTPONLY` being turned on. The cookie
 * is the fallback for a page not served by the shell -- the Vite dev server,
 * mainly.
 */
export function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]')?.content;
  if (meta) return meta;

  const match = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

/**
 * Headers for a JSON POST, with the token attached when there is one.
 *
 * Deliberately not `credentials: 'include'` here -- that belongs to the caller
 * -- but every caller does need both of these together, and getting one without
 * the other is a 403 that reads like a server fault.
 */
export function csrfHeaders(extra = {}) {
  const token = getCsrfToken();
  return { ...extra, ...(token ? { 'X-CSRFToken': token } : {}) };
}
