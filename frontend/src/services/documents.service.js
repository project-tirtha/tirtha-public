import { getApiBaseUrl, logApiCall } from '../utils/apiConfig';

/*
  The canonical documents, fetched so a dialog can show the real one.

  Not copied into the locale files. `shell.document`'s own note records why the
  standalone pages exist at all: the sidebar had grown paraphrases of the Terms
  and the Privacy Policy, and the request form asked people to consent to those.
  A rewritten legal document presented as the original is a second document
  nobody wrote -- so the dialog reads the same templates the pages render, and
  there is still one copy on disk.
*/
const DOCUMENTS_API_PATH = '/documents/';

/**
 * One document as `{ id, title, html }`.
 *
 * `html` is markup rendered from a template in this repository. It is not user
 * input and has never been: the only writers are whoever edits
 * `tirtha/templates/tirtha/*.html`.
 */
export async function fetchDocument(name, signal) {
  const url = `${getApiBaseUrl()}${DOCUMENTS_API_PATH}${encodeURIComponent(name)}/`;
  logApiCall(url, 'GET');
  const response = await fetch(url, { signal, credentials: 'same-origin' });
  if (!response.ok) {
    throw new Error(`Document ${name} could not be loaded (${response.status}).`);
  }
  return response.json();
}
