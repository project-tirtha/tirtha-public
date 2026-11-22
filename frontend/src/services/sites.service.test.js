/**
 * The adapter between the newer UI and the audited API (internal design notes).
 *
 * This is the seam the whole port turns on: the UI was written against a
 * different backend, and `normalizeSite` is the one function that reconciles
 * the two shapes. If it is wrong the map is empty and nothing says why, so it
 * is the first thing worth pinning.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  fetchSite,
  fetchSites,
  firstViewableRun,
  isViewableRun,
  resolveRun,
  viewableRuns,
} from './sites.service';

const site = (overrides = {}) => ({
  id: 'abc123',
  slug: 'India__Odisha__Puri__Konark',
  name: 'Konark Sun Temple',
  description: 'A 13th-century temple.',
  country: 'India',
  state: 'Odisha',
  district: 'Puri',
  status: 'Live',
  map_status: 'published',
  model_count: 2,
  location: { latitude: 19.8876, longitude: 86.0945, source: 'osm', zoom: 16 },
  categories: [],
  ...overrides,
});

function respondWith(payload) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => payload,
  });
}

describe('fetchSites', () => {
  beforeEach(() => {
    document.head.innerHTML =
      '<meta name="tirtha-prefix" content="/project/tirtha/">';
  });

  afterEach(() => {
    vi.restoreAllMocks();
    document.head.innerHTML = '';
  });

  it('reads the coordinate from the nested location object', async () => {
    global.fetch = respondWith({ results: [site()] });
    const [first] = await fetchSites();
    expect(first.lat).toBe(19.8876);
    expect(first.lng).toBe(86.0945);
  });

  it('keeps a site with no coordinate, marked as unplaced', async () => {
    /*
      29 of 59 production sites have no coordinate. That is not an error --
      nobody has located them yet.

      This used to drop the record entirely, and those sites then existed in no
      surface at all: not the map, not the panel list, not the search. Worse,
      the route effect treated their URLs as dead links and redirected to `/`,
      so 19 minted ARKs -- the one persistence promise this project has written
      down -- resolved correctly on the server and were thrown away by the
      client.

      The record is kept whole; `placed` is what the map filters on.
    */
    global.fetch = respondWith({ results: [site({ location: null }), site()] });
    const results = await fetchSites();

    expect(results).toHaveLength(2);
    const unplaced = results.find((r) => r.placed === false);
    expect(unplaced, 'the unlocated site was dropped').toBeTruthy();
    expect(unplaced.name).toBe('Konark Sun Temple');
    // Null rather than NaN: `Number(undefined)` is NaN, and a NaN reaching
    // MapLibre is a marker in the Gulf of Guinea.
    expect(unplaced.lat).toBeNull();
    expect(unplaced.lng).toBeNull();
  });

  it('carries the turntable through when the API offers one', async () => {
    /*
      A two-second orbit rendered from the published model. The API omits the
      key entirely for a site with no video on disk -- which is most of them --
      so the client has to treat absence as normal rather than as an error.
    */
    global.fetch = respondWith({
      results: [
        site({
          thumbnail_preview: '/m/a__preview.webp',
          thumbnail_turntable: '/m/a__turntable.mp4',
          thumbnail_turntable_kind: 'video',
        }),
        site(),
      ],
    });
    const [withVideo, without] = await fetchSites();
    expect(withVideo.thumbnailTurntable).toBe('/m/a__turntable.mp4');
    expect(withVideo.thumbnailTurntableKind).toBe('video');
    expect(without.thumbnailTurntable).toBeUndefined();
    // The still remains, because it is the video's poster.
    expect(withVideo.thumbnailPreview).toBe('/m/a__preview.webp');
  });

  it('marks a located site as placed, so the map can tell them apart', async () => {
    global.fetch = respondWith({ results: [site()] });
    const [first] = await fetchSites();
    expect(first.placed).toBe(true);
  });

  it('carries the server-computed status through to the map', async () => {
    /*
      Marker colour used to come from matching a free-text location string
      against 27 hardcoded Indian place names. It comes from the API now.
    */
    global.fetch = respondWith({
      results: [site({ map_status: 'needs_images', model_count: 0 })],
    });
    const [first] = await fetchSites();
    expect(first.mapStatus).toBe('needs_images');
    expect(first.modelCount).toBe(0);
  });

  it('builds the place label from the administrative fields', async () => {
    global.fetch = respondWith({ results: [site()] });
    const [first] = await fetchSites();
    expect(first.location).toBe('Puri, Odisha, India');
  });

  it('calls the versioned API under the deployment prefix', async () => {
    const spy = respondWith({ results: [] });
    global.fetch = spy;
    await fetchSites();
    expect(spy.mock.calls[0][0]).toMatch(/^\/project\/tirtha\/api\/v1\/sites\/\?/);
  });

  it('follows a different prefix, so one build serves beta and the demo', async () => {
    document.head.innerHTML =
      '<meta name="tirtha-prefix" content="/project/tirthademo/">';
    const spy = respondWith({ results: [] });
    global.fetch = spy;
    await fetchSites();
    expect(spy.mock.calls[0][0]).toMatch(/^\/project\/tirthademo\/api\/v1\/sites\/\?/);
  });

  it('accepts a bare array as well as a paged envelope', async () => {
    global.fetch = respondWith([site()]);
    expect(await fetchSites()).toHaveLength(1);
  });

  it('keeps the original error as the cause, not just its message', async () => {
    const boom = new TypeError('Failed to fetch');
    global.fetch = vi.fn().mockRejectedValue(boom);
    await expect(fetchSites()).rejects.toMatchObject({ cause: boom });
  });

  it('lets an abort through untouched, so a cancelled render is not an error', async () => {
    const abort = Object.assign(new Error('aborted'), { name: 'AbortError' });
    global.fetch = vi.fn().mockRejectedValue(abort);
    await expect(fetchSites()).rejects.toBe(abort);
  });
});

/**
 * Picking the run whose artifact this viewer can actually draw (internal design notes).
 *
 * The obvious implementation -- take `runs[0]` -- is wrong in a way that only
 * shows up on the archive's real data: a site's newest run may be a `GS` run
 * still in progress (no ARK, so `file_url` is null), or an older format this
 * build cannot read. Every one of those cases renders an empty canvas with no
 * explanation, which is indistinguishable from a broken viewer.
 */
describe('firstViewableRun', () => {
  const run = (overrides) => ({
    id: 'r1',
    kind: 'GS',
    file_url: 'https://example.test/models/a.splat',
    ...overrides,
  });

  it('returns the newest run the viewer can read', () => {
    const chosen = firstViewableRun([run({ id: 'new' }), run({ id: 'old' })]);
    expect(chosen.id).toBe('new');
  });

  it('skips a run that has no published file rather than passing null on', () => {
    // A run without an ARK has `file_url: null` -- it exists, it is visible in
    // the run list, but nothing has been published for it yet.
    const chosen = firstViewableRun([run({ id: 'unpublished', file_url: null }), run({ id: 'ok' })]);
    expect(chosen.id).toBe('ok');
  });

  it('skips a format this build cannot draw, instead of opening an empty canvas', () => {
    /*
      `.sog` is the newer artifact `_artifact_url` serves when ARTIFACT_ROOT is
      configured; this viewer's gaussian-splats-3d reads `.splat` and drei reads
      `.glb`. Offering it a .sog produces a silent blank.
    */
    const chosen = firstViewableRun([
      run({ id: 'sog', file_url: 'https://example.test/a.sog' }),
      run({ id: 'glb', file_url: 'https://example.test/a.glb' }),
    ]);
    expect(chosen.id).toBe('glb');
  });

  it('ignores a query string when reading the extension', () => {
    // Signed media URLs carry one, and `endsWith('.splat')` alone said no.
    expect(firstViewableRun([run({ file_url: 'https://x.test/a.splat?sig=abc' })])).toBeTruthy();
  });

  it('returns null when nothing is viewable, so the caller can say why', () => {
    expect(firstViewableRun([run({ file_url: null })])).toBeNull();
    expect(firstViewableRun([])).toBeNull();
    expect(firstViewableRun(undefined)).toBeNull();
  });
});

/**
 * Ordering the runs on the client rather than trusting the server's.
 *
 * The API sorts on `ended_at DESC`, and Postgres puts NULL first on a descending
 * sort. So a published run that was never stamped with an end time -- a manual
 * run, or one backfilled from the old archive -- arrives at the head of the
 * list and becomes "the latest model" for everyone who opens the site, ahead of
 * the reconstruction that actually finished last night. Nothing errors; the
 * page simply shows the wrong model, and keeps doing so until someone notices.
 */
describe('viewableRuns', () => {
  const run = (overrides) => ({
    id: 'r1',
    kind: 'GS',
    file_url: 'https://example.test/models/a.splat',
    ended_at: '2026-09-01T00:00:00Z',
    ...overrides,
  });

  it('puts a run with no end time after every dated one, whatever the server said', () => {
    // Server order, NULLS FIRST: the undated run is what `runs[0]` would give.
    const ordered = viewableRuns([
      run({ id: 'undated', ended_at: null }),
      run({ id: 'older', ended_at: '2026-08-01T00:00:00Z' }),
      run({ id: 'newer', ended_at: '2026-09-01T00:00:00Z' }),
    ]);
    expect(ordered.map((r) => r.id)).toEqual(['newer', 'older', 'undated']);
  });

  it('treats an unparseable date the same as a missing one', () => {
    // `Date.parse` of garbage is NaN, and NaN compared with anything is false,
    // which would leave the comparator returning nonsense and the order
    // depending on the engine's sort algorithm.
    const ordered = viewableRuns([
      run({ id: 'garbage', ended_at: 'not a date' }),
      run({ id: 'dated' }),
    ]);
    expect(ordered.map((r) => r.id)).toEqual(['dated', 'garbage']);
  });

  it('keeps the server order between runs that tie', () => {
    // Two undated runs have nothing to sort on; the sort is stable, so the
    // server's newest-first is what survives rather than an arbitrary shuffle.
    const ordered = viewableRuns([
      run({ id: 'first', ended_at: null }),
      run({ id: 'second', ended_at: null }),
    ]);
    expect(ordered.map((r) => r.id)).toEqual(['first', 'second']);
  });

  it('leaves out a Processing run, which has no file to draw', () => {
    const ordered = viewableRuns([run({ id: 'processing', file_url: null }), run({ id: 'ok' })]);
    expect(ordered.map((r) => r.id)).toEqual(['ok']);
  });

  it('does not throw on an empty or absent list', () => {
    expect(viewableRuns([])).toEqual([]);
    expect(viewableRuns()).toEqual([]);
    expect(viewableRuns(null)).toEqual([]);
  });

  it('is what firstViewableRun is defined through, so the two cannot disagree', () => {
    // The default model and the picker's first entry must be the same run, or
    // the page opens one model while the list highlights another.
    const runs = [run({ id: 'undated', ended_at: null }), run({ id: 'dated' })];
    expect(firstViewableRun(runs)).toBe(viewableRuns(runs)[0]);
    expect(firstViewableRun(runs).id).toBe('dated');
  });
});

describe('isViewableRun', () => {
  it('accepts the three formats this build reads and nothing else', () => {
    for (const ok of ['a.splat', 'a.ply', 'a.glb', 'A.SPLAT?sig=x']) {
      expect(isViewableRun({ file_url: `https://x.test/${ok}` }), ok).toBe(true);
    }
    // `.sog` and `.spz` are what the pipeline writes, and are refused on
    // purpose; see the note on VIEWABLE and `test_artifact_url.ViewerFormatCoupling`.
    for (const bad of ['a.sog', 'a.spz', 'a.obj']) {
      expect(isViewableRun({ file_url: `https://x.test/${bad}` }), bad).toBe(false);
    }
    expect(isViewableRun({ file_url: null })).toBe(false);
    expect(isViewableRun(undefined)).toBe(false);
  });
});

/**
 * A run id in the URL is a promise the server may no longer keep. The visitor
 * should still get a model, and the page should be able to say why it is not
 * the one they asked for.
 */
describe('resolveRun', () => {
  const run = (overrides) => ({
    id: 'r1',
    kind: 'GS',
    file_url: 'https://example.test/models/a.splat',
    ended_at: '2026-09-01T00:00:00Z',
    ...overrides,
  });
  const runs = [
    run({ id: 'newest' }),
    run({ id: 'older', ended_at: '2026-08-01T00:00:00Z' }),
    run({ id: 'processing', file_url: null, ended_at: null }),
  ];

  it('gives the newest viewable run and no reason when nothing was asked for', () => {
    expect(resolveRun(runs)).toEqual({ run: runs[0], reason: null });
    expect(resolveRun(runs, null)).toEqual({ run: runs[0], reason: null });
  });

  it('honours a viewable run that was asked for by id, even an older one', () => {
    // A shared link to a specific reconstruction must open that one, not be
    // silently upgraded to whatever finished since.
    expect(resolveRun(runs, 'older')).toEqual({ run: runs[1], reason: null });
  });

  it('says "missing" for an id not in the list, and still shows the newest', () => {
    // The run was hidden after the link was shared. Falling back keeps the
    // page useful; the reason lets it explain the substitution.
    expect(resolveRun(runs, 'gone')).toEqual({ run: runs[0], reason: 'missing' });
  });

  it('says "unviewable" for a run that exists but has nothing to draw', () => {
    // Processing: visible in the run list, `file_url` null. It is excluded from
    // `viewableRuns`, but a link to it is a different situation from a link to
    // a run that does not exist, and the message should differ.
    expect(viewableRuns(runs).map((r) => r.id)).not.toContain('processing');
    expect(resolveRun(runs, 'processing')).toEqual({ run: runs[0], reason: 'unviewable' });
  });

  it('says "unviewable" for a format this build cannot read', () => {
    const withSog = [...runs, run({ id: 'sog', file_url: 'https://x.test/a.sog' })];
    expect(resolveRun(withSog, 'sog')).toEqual({ run: runs[0], reason: 'unviewable' });
  });

  it('matches a numeric id against the string the URL carries', () => {
    const numeric = [run({ id: 7 })];
    expect(resolveRun(numeric, '7').run).toBe(numeric[0]);
  });

  it('returns a null run, with the reason intact, when nothing is viewable', () => {
    const nothing = [run({ id: 'processing', file_url: null })];
    expect(resolveRun(nothing, 'processing')).toEqual({ run: null, reason: 'unviewable' });
    expect(resolveRun(nothing, 'gone')).toEqual({ run: null, reason: 'missing' });
  });

  it('does not throw on an empty or absent list', () => {
    expect(resolveRun([])).toEqual({ run: null, reason: null });
    expect(resolveRun([], 'r1')).toEqual({ run: null, reason: 'missing' });
    expect(resolveRun()).toEqual({ run: null, reason: null });
    expect(resolveRun(null, 'r1')).toEqual({ run: null, reason: 'missing' });
  });
});

describe('fetchSite', () => {
  beforeEach(() => {
    document.head.innerHTML = '<meta name="tirtha-prefix" content="/project/tirtha/">';
  });
  afterEach(() => {
    vi.restoreAllMocks();
    document.head.innerHTML = '';
  });

  it('asks the detail endpoint, which is the only one carrying runs', async () => {
    const spy = respondWith({ id: 'abc123', runs: [] });
    global.fetch = spy;
    await fetchSite('abc123');
    expect(spy.mock.calls[0][0]).toBe('/project/tirtha/api/v1/sites/abc123/');
  });

  it('encodes the id, so a hostile one cannot reach another path', async () => {
    const spy = respondWith({ runs: [] });
    global.fetch = spy;
    await fetchSite('../../admin');
    expect(spy.mock.calls[0][0]).not.toContain('../');
  });

  it('raises on a 404 rather than resolving with an error page', async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 404, statusText: 'Not Found' });
    await expect(fetchSite('nope')).rejects.toThrow('404');
  });
});

/**
 * The map draws every site, not the first page of them.
 *
 * The endpoint answers `{count, page, page_size, results}` with a default page
 * size of 100. There are 59 sites, so one request happens to return all of them
 * -- and on the day the 101st is added, the map would quietly stop showing it.
 * Nothing reports that: the request succeeds, the payload is well-formed, and
 * exactly one temple is missing from a map of sixty.
 */
describe('fetchSites paging', () => {
  beforeEach(() => {
    document.head.innerHTML =
      '<meta name="tirtha-prefix" content="/project/tirtha/">';
  });
  afterEach(() => {
    vi.restoreAllMocks();
    document.head.innerHTML = '';
  });

  const page = (results, count) => ({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => ({ count, page: 1, page_size: results.length, results }),
  });

  it('follows the pages until it has every site', async () => {
    const first = Array.from({ length: 2 }, (_, i) => site({ id: `a${i}` }));
    const second = [site({ id: 'b0' })];
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(page(first, 3))
      .mockResolvedValueOnce(page(second, 3));

    expect(await fetchSites()).toHaveLength(3);
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });

  it('stops after one request when the first page is everything', async () => {
    global.fetch = vi.fn().mockResolvedValue(page([site()], 1));
    await fetchSites();
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it('asks for each page in turn', async () => {
    const spy = vi
      .fn()
      .mockResolvedValueOnce(page([site({ id: 'a' })], 2))
      .mockResolvedValueOnce(page([site({ id: 'b' })], 2));
    global.fetch = spy;
    await fetchSites();
    expect(spy.mock.calls[0][0]).toContain('page=1');
    expect(spy.mock.calls[1][0]).toContain('page=2');
  });

  it('does not spin forever when the server always claims more', async () => {
    // A `count` that never agrees with what is returned would otherwise be an
    // infinite loop in the browser, with no error and no page.
    global.fetch = vi.fn().mockResolvedValue(page([site()], 10_000));
    await fetchSites();
    expect(global.fetch.mock.calls.length).toBeLessThan(25);
  });

  it('still accepts a bare array, which cannot be paged', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => [site()],
    });
    expect(await fetchSites()).toHaveLength(1);
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });
});
