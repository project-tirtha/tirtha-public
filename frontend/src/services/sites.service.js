import { getApiBaseUrl, logApiCall } from '../utils/apiConfig';
import { placeLabel } from '../utils/format';

// The audited API (internal design notes). `getApiBaseUrl` already carries the
// deployment prefix and the `/api/v1` segment, so these are relative to it.
const SITES_API_PATH = '/sites/';

function normalizeSite(site) {
  /*
    The coordinate is nested and carries its own provenance (internal design notes).
    It is null for a site nobody has located yet -- 12 of 34 on the demo, 29 of
    59 in production -- which is not an error.

    This used to `return null` there, and `fetchSites` ended in `.filter(Boolean)`,
    so those sites existed in **no surface at all**: not the map, not the panel
    list, not the search. The comment above this claimed "the panel still lists
    them"; it did not. And because they were absent from `siteList`, the route
    effect treated their URLs as dead links and redirected to `/` -- so 19
    minted ARKs, the one persistence promise this project has written down,
    resolved correctly through `resolveARK` and were then thrown away by the
    client.

    The record is kept whole. `placed` says whether it can be drawn, and the map
    filters on that; everything else -- the list, the search, a deep link, an
    ARK -- has the site.
  */
  const lat = Number(site.location?.latitude);
  const lng = Number(site.location?.longitude);
  const placed = Number.isFinite(lat) && Number.isFinite(lng);

  // `location` is an object on this API (internal design notes) -- a coordinate with
  // its provenance -- not the free-text place string the previous backend
  // returned. The label is built from the administrative fields instead, which
  // is where it should have come from anyway.
  const place =
    placeLabel(site);

  return {
    id: site.id,
    // `verbose_id` -- the upload endpoints' `mesh_vid` (internal design notes). Not
    // cosmetic: without it the contribute flow has no way to name the site it
    // is uploading to.
    slug: site.slug,
    name: site.name || `Site ${site.id}`,
    /*
      Other names the site is known by, for search. The API returns none today;
      read defensively under the names a server is likely to use, so adding the
      field there is enough for search to find them.
    */
    alternateNames: [
      ...(Array.isArray(site.alternate_names) ? site.alternate_names : []),
      ...(Array.isArray(site.local_names) ? site.local_names : []),
    ].filter((value) => typeof value === 'string' && value),
    /*
      The 160px WebP copy, when it has been generated. The full thumbnail is
      ~420 KB, so a hover over a cluster of fourteen would pull 4.9 MB of them;
      this is the size the map can afford to show on a pointer moving across it.
      Undefined where the derivative does not exist yet, and the hover simply
      shows no picture rather than a broken one.
    */
    thumbnailPreview: site.thumbnail_preview,
    thumbnailCard: site.thumbnail_card,
    /*
      A two-second orbit rendered from the published model, where one exists.
      Offered by the API only when the file is on disk, so this is undefined
      for the sites that have no reconstruction to turn.
    */
    thumbnailTurntable: site.thumbnail_turntable,
    /*
      'video' or 'image'. A hand-uploaded preview may be a GIF or an animated
      WebP, and a GIF inside a `<video>` renders nothing at all -- silently. The
      server knows the extension, so it says which element to build.
    */
    thumbnailTurntableKind: site.thumbnail_turntable_kind,
    placed,
    lat: placed ? lat : null,
    lng: placed ? lng : null,
    location: place,
    state: site.state,
    country: site.country,
    status: site.status,
    // What the map should say about this site, derived server-side from its
    // runs (internal design notes). Replaces `getCompletionStatus`, which decided marker
    // colour by matching a hardcoded list of Indian place names against a
    // free-text string.
    mapStatus: site.map_status,
    modelCount: site.model_count,
    completed: Boolean(site.completed),
    thumbnail: site.thumbnail,
    modelPath: site.modelPath,
    description: site.description,
    // Sanitised server-side (`api._description_html`). 27 of the 59
    // descriptions carry paragraphs, lists and outbound links.
    descriptionHtml: site.description_html,
    details: site.details,
  };
}

/**
 * Every site the map should draw.
 *
 * **Paged, and it has to follow the pages.** The endpoint returns
 * `{count, page, page_size, results}` with a default page size of 100. There are
 * 59 sites, so a single request happens to return all of them today -- and on
 * the day the 101st is added, the map would quietly stop showing it. Nothing
 * would report that: the request succeeds, the payload is well-formed, and
 * exactly one temple is missing from a map of sixty. Following `count` costs one
 * comparison and removes the whole class.
 *
 * `page_size` is asked for explicitly rather than left to the server's default,
 * so the number of round trips is a decision here rather than a coincidence.
 * The server caps it at `MAX_PAGE_SIZE`.
 */
const PAGE_SIZE = 500;
const MAX_PAGES = 20;

async function fetchPage(page, signal) {
  const endpoint = `${getApiBaseUrl()}${SITES_API_PATH}?page=${page}&page_size=${PAGE_SIZE}`;
  logApiCall(endpoint, 'GET');

  const response = await fetch(endpoint, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    signal,
    mode: 'cors',
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }
  return response.json();
}

export async function fetchSites(signal) {
  try {
    const rows = [];
    let page = 1;
    let total = null;

    // Bounded: a server that always reports more than it returns would
    // otherwise spin here forever.
    while (page <= MAX_PAGES) {
      const payload = await fetchPage(page, signal);
      const batch = Array.isArray(payload) ? payload : payload?.results;

      if (!Array.isArray(batch)) {
        throw new Error('Unexpected API response format for sites');
      }

      rows.push(...batch);

      // A bare array is the whole answer; there is nothing to page.
      if (Array.isArray(payload)) break;

      total = Number.isFinite(Number(payload.count)) ? Number(payload.count) : rows.length;
      if (batch.length === 0 || rows.length >= total) break;
      page += 1;
    }

    logApiCall(`${getApiBaseUrl()}${SITES_API_PATH}`, 'GET', {
      success: true,
      count: rows.length,
      pages: page,
    });

    /*
      No `.filter(Boolean)`. It was there to drop the nulls `normalizeSite`
      returned for a site with no coordinate -- which is what made 29 of 59
      sites exist in no surface at all, and 19 ARKs resolve to the bare map.
      `normalizeSite` returns a record for every row now and marks it `placed`,
      so a filter here could only hide a real bug.
    */
    return rows.map(normalizeSite);
  } catch (error) {
    if (error?.name === 'AbortError') {
      throw error;
    }
    logApiCall(`${getApiBaseUrl()}${SITES_API_PATH}`, 'GET', { error: error.message });
    // `cause` keeps the original: the message alone loses the stack and the
    // network error's own type, which is what tells a developer whether this
    // was a 404, CORS or an abort.
    throw new Error(`Failed to load sites: ${error.message}`, { cause: error });
  }
}

export async function fetchSiteStats(siteId, signal) {
  const endpoint = `${getApiBaseUrl()}${SITES_API_PATH}${encodeURIComponent(siteId)}/stats/`;
  logApiCall(endpoint, 'GET');

  try {
    const response = await fetch(endpoint, {
      method: 'GET',
      headers: {
        Accept: 'application/json',
      },
      signal,
      mode: 'cors',
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const payload = await response.json();
    if (!payload || typeof payload !== 'object') {
      throw new Error('Unexpected API response format for site stats');
    }

    logApiCall(endpoint, 'GET', { success: true, siteId });
    return payload;
  } catch (error) {
    if (error?.name === 'AbortError') {
      throw error;
    }
    logApiCall(endpoint, 'GET', { error: error.message, siteId });
    // `cause` keeps the original: the message alone loses the stack
    // and the network error's own type, which is what tells a
    // developer whether this was a 404, CORS or an abort.
    throw new Error(`Failed to load site stats: ${error.message}`, { cause: error });
  }
}

/**
 * One site, with its runs (internal design notes).
 *
 * The list endpoint deliberately does not carry runs -- it is fetched for every
 * site at once and the payload is what the browser pays for -- so the model URL
 * only exists here. Without this call `modelPath` is `undefined` for every site
 * and the viewer renders its debug cube, which is what it was doing.
 */
export async function fetchSite(siteId, signal) {
  const endpoint = `${getApiBaseUrl()}${SITES_API_PATH}${encodeURIComponent(siteId)}/`;
  logApiCall(endpoint, 'GET');

  const response = await fetch(endpoint, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    signal,
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }
  return response.json();
}

/**
 * One run, by id (`/api/v1/runs/<id>/`).
 *
 * The embed needs it: the template hands over the ARK's URL as stored, which
 * may name a format this viewer cannot read, and no camera. The run detail
 * carries the viewer-readable `file_url` (`api._artifact_url`), the published
 * camera, and -- where the server provides them -- `model_bytes` and
 * `model_format`.
 */
export async function fetchRun(runId, signal) {
  const endpoint = `${getApiBaseUrl()}/runs/${encodeURIComponent(runId)}/`;
  logApiCall(endpoint, 'GET');
  const response = await fetch(endpoint, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    signal,
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Formats this viewer can draw, newest run first.
 *
 * `@mkkellogg/gaussian-splats-3d` reads `.splat` -- the legacy format the
 * archive is full of -- and `.ply`; drei's `useGLTF` reads `.glb`. It does
 * **not** read `.sog`, which is what the pipeline has published since
 * internal design notes, so `run_postprocess` writes a `.splat` beside every `.sog`
 * and the API offers whichever of these it finds (`api._artifact_url`).
 *
 * `.spz` is absent deliberately. The library has an SPZ loader, but it accepts
 * only versions 1-2, and splat-transform writes 4 and refuses below 3 -- so
 * every `.spz` this pipeline can produce is one this viewer rejects.
 *
 * This list and `VIEWER_ARTIFACT_FORMATS` on the server describe the same fact
 * from two sides, so they are kept in step by a test
 * (`test_artifact_url.ViewerFormatCoupling`). A run whose artifact is none of
 * these -- or which has no published file at all -- cannot be shown, and the
 * page should say so rather than open an empty canvas.
 */
const VIEWABLE = ['.splat', '.ply', '.glb'];

export function isViewableRun(run) {
  // The query string is dropped before the extension test: signed media URLs
  // carry one, and `endsWith('.splat')` on the raw URL said no to every one.
  const url = (run?.file_url || '').toLowerCase().split('?')[0];
  return VIEWABLE.some((extension) => url.endsWith(extension));
}

// `ended_at` as milliseconds, or null when it is absent or not a date. Both
// `Date.parse(null)` and `Date.parse(undefined)` are NaN, but the null is made
// explicit so `viewableRuns` has one value to test for rather than two.
function endedAtMillis(run) {
  const millis = Date.parse(run?.ended_at ?? '');
  return Number.isFinite(millis) ? millis : null;
}

/**
 * The runs this viewer can draw, newest first, with the undated ones last.
 *
 * The API already sends runs newest-first, so re-sorting looks redundant. It is
 * not: the server orders on `ended_at DESC`, and Postgres puts NULL **first** on
 * a descending sort. A published run whose `ended_at` was never set -- a manual
 * `create_manual_run`, or a row backfilled from the old archive -- therefore
 * arrives at the head of the list and becomes "the latest model" for every
 * visitor to that site, ahead of the reconstruction finished last night. Sorting
 * here, with a null date ranked below any real one, is the client declining to
 * trust that order.
 *
 * Ties -- two undated runs, or two with the same timestamp -- keep the server's
 * relative order, because `Array.prototype.sort` has been stable since ES2019.
 */
export function viewableRuns(runs = []) {
  return (Array.isArray(runs) ? runs : []).filter(isViewableRun).sort((a, b) => {
    const endedA = endedAtMillis(a);
    const endedB = endedAtMillis(b);
    if (endedA === null && endedB === null) return 0;
    if (endedA === null) return 1;
    if (endedB === null) return -1;
    return endedB - endedA;
  });
}

// What the page opens when the URL names no run. Defined through `viewableRuns`
// so this and the run picker's list can never disagree about which run is first.
export function firstViewableRun(runs = []) {
  return viewableRuns(runs)[0] ?? null;
}

/**
 * The run a page should show, given what the URL asked for.
 *
 * A run id in the URL is a promise the server may no longer be able to keep:
 * the run may have been hidden since the link was shared (`missing`), or it may
 * be there but still Processing with no published file, or in a format this
 * build cannot read (`unviewable`). In every one of those cases the visitor
 * still gets a model -- the newest viewable one -- and `reason` is what lets
 * the page say "that run is not available; showing the latest instead" rather
 * than opening a blank canvas, which is indistinguishable from a broken viewer.
 *
 * `run` is null only when the site has nothing viewable at all; `reason` still
 * says whether a specific run was asked for, because the two messages differ.
 *
 * Ids are compared as strings: the one in the URL is always a string, and a
 * strict `===` against a numeric id would report every run as `missing`.
 */
export function resolveRun(runs = [], requestedId = null) {
  const newest = firstViewableRun(runs);
  if (requestedId === null || requestedId === undefined) {
    return { run: newest, reason: null };
  }

  const wanted = String(requestedId);
  const requested =
    (Array.isArray(runs) ? runs : []).find((run) => String(run?.id) === wanted) ?? null;

  if (!requested) return { run: newest, reason: 'missing' };
  if (!isViewableRun(requested)) return { run: newest, reason: 'unviewable' };
  return { run: requested, reason: null };
}
