import { getApiBaseUrl } from './apiConfig';
import { csrfHeaders } from './csrf';
import { deploymentPrefix } from './deployment';

/**
 * Sign-in state, from the session the backend actually keeps (unit 6.8).
 *
 * The UI arrived with a Google Identity Services flow: load
 * `accounts.google.com/gsi/client`, render Google's button, take the returned
 * id_token and POST it to `/api/auth/google-login/`. This backend does not
 * authenticate that way -- `views.signin` redirects to Google through authlib
 * and `views.verifyToken` puts the result in the Django session -- so the
 * endpoint did not exist, `VITE_GOOGLE_CLIENT_ID` was never set, and the modal
 * said "Missing Google client ID" instead of ever showing a sign-in button.
 *
 * Rewriting it to the redirect flow rather than adding an id_token endpoint is
 * deliberate: two ways to become authenticated is two places for that to be
 * wrong, and the redirect flow is the one already carrying production traffic.
 * It also drops a third-party script from every page that opens this modal.
 */
export async function fetchSession(signal) {
  const response = await fetch(`${getApiBaseUrl()}/session/`, {
    headers: { Accept: 'application/json' },
    credentials: 'same-origin',
    signal,
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}


/**
 * Leave for Google, and come back to this page.
 *
 * A full-page navigation, not a popup: the session cookie is set by the
 * callback on this origin, and there is nothing for the SPA to hold onto in
 * between. `?next=` is a same-origin path only -- an absolute URL from here
 * would be an open redirect with Tirtha's name on it.
 */
export function startSignIn() {
  const next = `${window.location.pathname}${window.location.search}`;
  window.location.assign(`${deploymentPrefix()}/signin/?next=${encodeURIComponent(next)}`);
}

export async function signOut() {
  const response = await fetch(`${getApiBaseUrl()}/sign-out/`, {
    method: 'POST',
    headers: csrfHeaders({ Accept: 'application/json' }),
    credentials: 'same-origin',
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

/**
 * The one thing the rest of the UI asks: may this person upload?
 *
 * Three refusals, and they are not the same refusal. "Not signed in" is fixed
 * by signing in; "awaiting approval" is fixed by an administrator; "banned" is
 * not fixed by the person at all. Collapsing them into a disabled button --
 * which is what `contributor.status === 'approved'` did -- leaves someone
 * clicking a control that will never work with no idea why.
 */
export function uploadState(session) {
  if (!session?.authenticated) return 'anonymous';
  if (session.contributor?.banned) return 'banned';
  if (!session.contributor?.active) return 'awaiting_approval';
  return 'approved';
}
