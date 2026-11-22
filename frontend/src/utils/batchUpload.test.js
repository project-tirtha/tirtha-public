/**
 * The batched upload's client half: how files are split, how a failed batch is
 * retried, where a Retry resumes, and what Cancel leaves behind.
 */
import { describe, expect, it, vi } from 'vitest';

import {
  BatchUploadError,
  beaconDiscard,
  discardTarget,
  discardUpload,
  fileKey,
  isRetryable,
  MAX_BATCH_BYTES,
  refusalCode,
  REFUSAL_MESSAGES,
  runBatchedUpload,
  splitIntoBatches,
} from './batchUpload';
import en from '../i18n/locales/en.json';
import hi from '../i18n/locales/hi.json';
import or from '../i18n/locales/or.json';

const MB = 1024 * 1024;

/** A File of a given size without allocating it. */
function file(name, size, lastModified = 1) {
  const f = new File(['x'], name, { type: 'image/jpeg', lastModified });
  Object.defineProperty(f, 'size', { value: size });
  return f;
}

const entries = (n, size = MB) =>
  Array.from({ length: n }, (_, i) => ({ file: file(`IMG_${i}.jpg`, size), isVideo: false }));

describe('fileKey', () => {
  it('is stable for the same name, size and lastModified', () => {
    expect(fileKey(file('a.jpg', 10, 5))).toBe(fileKey(file('a.jpg', 10, 5)));
  });

  it('differs when any of the three differs', () => {
    const base = fileKey(file('a.jpg', 10, 5));
    expect(fileKey(file('b.jpg', 10, 5))).not.toBe(base);
    expect(fileKey(file('a.jpg', 11, 5))).not.toBe(base);
    expect(fileKey(file('a.jpg', 10, 6))).not.toBe(base);
  });

  it('fits what the server accepts', () => {
    expect(fileKey(file('ଫଟୋ (1).heic', 123456789, 1700000000000))).toMatch(/^[A-Za-z0-9_-]{1,64}$/);
  });
});

describe('splitIntoBatches', () => {
  it('closes a batch at 20 files', () => {
    const batches = splitIntoBatches(entries(45));
    expect(batches.map((b) => b.length)).toEqual([20, 20, 5]);
  });

  it('closes a batch before it would pass 150 MB', () => {
    const batches = splitIntoBatches(entries(10, 40 * MB));
    // 3 x 40 = 120 MB fits, a fourth would make 160.
    expect(batches.map((b) => b.length)).toEqual([3, 3, 3, 1]);
    for (const batch of batches) {
      expect(batch.reduce((acc, e) => acc + e.file.size, 0)).toBeLessThanOrEqual(MAX_BATCH_BYTES);
    }
  });

  it('sends a file over the byte limit as a batch of its own', () => {
    const big = { file: file('walk.mp4', 400 * MB), isVideo: true };
    const last = { file: file('last.jpg', MB), isVideo: false };
    const batches = splitIntoBatches([...entries(2), big, last]);
    expect(batches.map((b) => b.map((e) => e.file.name))).toEqual([
      ['IMG_0.jpg', 'IMG_1.jpg'],
      ['walk.mp4'],
      ['last.jpg'],
    ]);
  });

  it('keeps order and attaches a key to every entry', () => {
    const batches = splitIntoBatches(entries(3));
    expect(batches[0].map((e) => e.file.name)).toEqual(['IMG_0.jpg', 'IMG_1.jpg', 'IMG_2.jpg']);
    expect(batches[0].every((e) => e.key === fileKey(e.file))).toBe(true);
  });

  it('sends a file selected twice once', () => {
    const one = { file: file('a.jpg', 5), isVideo: false };
    expect(splitIntoBatches([one, { ...one }]).flat()).toHaveLength(1);
  });

  it('returns nothing for nothing', () => {
    expect(splitIntoBatches([])).toEqual([]);
  });
});

describe('isRetryable', () => {
  it.each([
    [undefined, true],
    [408, true],
    [429, true],
    [500, true],
    [503, true],
    [400, false],
    [403, false],
    [404, false],
    [409, false],
  ])('status %s -> %s', (status, expected) => {
    const error = status ? { response: { status } } : new Error('Network Error');
    expect(isRetryable(error)).toBe(expected);
  });

  it('never retries a cancel', () => {
    expect(isRetryable({ name: 'CanceledError' })).toBe(false);
  });
});

/** A fake server: records calls, answers batches with an id, can be told to fail. */
function fakeServer({ failures = {} } = {}) {
  const calls = [];
  let batchNo = 0;
  const post = vi.fn(async (url, body, config) => {
    if (url.endsWith('/upload/finish/')) {
      calls.push({ kind: 'finish', body });
      const fail = failures.finish?.shift();
      if (fail) throw fail;
      return { data: { status: 'Success', output: 'Thanks' } };
    }
    if (url.endsWith('/upload/discard/')) {
      calls.push({ kind: 'discard', body });
      return { data: { status: 'Success', discarded: true } };
    }
    const index = batchNo;
    calls.push({
      kind: 'batch',
      index,
      contributionId: body.get('contribution_id'),
      keys: body.getAll('image_keys'),
    });
    const fail = failures[index]?.shift();
    if (fail) throw fail;
    config.onUploadProgress?.({ loaded: 1, total: 1 });
    batchNo += 1;
    return {
      data: {
        status: 'Success',
        contribution_id: 'c-1',
        received: body.getAll('image_keys'),
        rejected: [],
        count: 0,
      },
    };
  });
  return { post, calls };
}

const network = () => Object.assign(new Error('Network Error'), { response: undefined });
const noSleep = vi.fn(async () => {});

describe('runBatchedUpload', () => {
  it('sends every batch into one contribution, then finishes', async () => {
    const server = fakeServer();
    const batches = splitIntoBatches(entries(45));
    const result = await runBatchedUpload({ batches, siteSlug: 'S', post: server.post, sleep: noSleep });
    const kinds = server.calls.map((c) => c.kind);
    expect(kinds).toEqual(['batch', 'batch', 'batch', 'finish']);
    expect(server.calls[0].contributionId).toBeNull();
    expect(server.calls[1].contributionId).toBe('c-1');
    expect(server.calls[2].contributionId).toBe('c-1');
    expect(server.calls[3].body).toEqual({ contribution_id: 'c-1' });
    expect(result.contributionId).toBe('c-1');
    expect(result.data.output).toBe('Thanks');
  });

  it('reports progress across all batches, never backwards', async () => {
    const server = fakeServer();
    const seen = [];
    await runBatchedUpload({
      batches: splitIntoBatches(entries(25)),
      siteSlug: 'S',
      post: server.post,
      sleep: noSleep,
      onProgress: ({ sentBytes, totalBytes }) => seen.push(sentBytes / totalBytes),
    });
    expect(seen.at(-1)).toBe(1);
    expect(seen).toEqual([...seen].sort((a, b) => a - b));
  });

  it('retries a failed batch with backoff, resending the same keys', async () => {
    const server = fakeServer({ failures: { 1: [network(), { response: { status: 503 } }] } });
    const sleep = vi.fn(async () => {});
    const onRetry = vi.fn();
    await runBatchedUpload({
      batches: splitIntoBatches(entries(30)),
      siteSlug: 'S',
      post: server.post,
      sleep,
      onRetry,
    });
    expect(sleep.mock.calls.map(([ms]) => ms)).toEqual([2000, 8000]);
    expect(onRetry).toHaveBeenCalledTimes(2);
    const second = server.calls.filter((c) => c.kind === 'batch' && c.index === 1);
    expect(second).toHaveLength(3);
    expect(second[0].keys).toEqual(second[2].keys);
  });

  it('gives up after three retries and says where to resume', async () => {
    const server = fakeServer({ failures: { 1: [network(), network(), network(), network()] } });
    const batches = splitIntoBatches(entries(45));
    const error = await runBatchedUpload({ batches, siteSlug: 'S', post: server.post, sleep: noSleep }).catch(
      (e) => e,
    );
    expect(error).toBeInstanceOf(BatchUploadError);
    expect(error.resumeFrom).toBe(1);
    expect(error.contributionId).toBe('c-1');
    expect(error.restart).toBe(false);
    expect(server.calls.filter((c) => c.kind === 'batch')).toHaveLength(1 + 4);
    expect(server.calls.some((c) => c.kind === 'finish')).toBe(false);

    // Retry: resumes at batch 1, into the same contribution.
    const again = fakeServer();
    await runBatchedUpload({
      batches,
      startAt: error.resumeFrom,
      contributionId: error.contributionId,
      siteSlug: 'S',
      post: again.post,
      sleep: noSleep,
    });
    expect(again.calls.map((c) => c.kind)).toEqual(['batch', 'batch', 'finish']);
    expect(again.calls[0].contributionId).toBe('c-1');
    expect(again.calls[0].keys).toEqual(batches[1].map((e) => e.key));
  });

  it('does not retry a refusal', async () => {
    const forbidden = { response: { status: 403, data: { error: 'forbidden', output: 'Not yours' } } };
    const server = fakeServer({ failures: { 0: [forbidden] } });
    const error = await runBatchedUpload({
      batches: splitIntoBatches(entries(2)),
      siteSlug: 'S',
      post: server.post,
      sleep: noSleep,
    }).catch((e) => e);
    expect(server.calls).toHaveLength(1);
    expect(error.output).toBe('Not yours');
  });

  it('starts over when the open upload has expired', async () => {
    const expired = { response: { status: 409, data: { error: 'expired' } } };
    const server = fakeServer({ failures: { 1: [expired] } });
    const error = await runBatchedUpload({
      batches: splitIntoBatches(entries(25)),
      siteSlug: 'S',
      post: server.post,
      sleep: noSleep,
    }).catch((e) => e);
    expect(error.restart).toBe(true);
    expect(error.contributionId).toBeNull();
  });

  it('retries the finish alone when only the finish failed', async () => {
    const server = fakeServer({ failures: { finish: [network(), network(), network(), network()] } });
    const batches = splitIntoBatches(entries(5));
    const error = await runBatchedUpload({ batches, siteSlug: 'S', post: server.post, sleep: noSleep }).catch(
      (e) => e,
    );
    expect(error.resumeFrom).toBe(batches.length);

    const again = fakeServer();
    await runBatchedUpload({
      batches,
      startAt: error.resumeFrom,
      contributionId: error.contributionId,
      siteSlug: 'S',
      post: again.post,
      sleep: noSleep,
    });
    expect(again.calls.map((c) => c.kind)).toEqual(['finish']);
    expect(again.calls[0].body).toEqual({ contribution_id: 'c-1' });
  });

  it('stops at once on cancel, and says which contribution was open', async () => {
    const controller = new AbortController();
    const server = fakeServer();
    const post = vi.fn(async (url, body, config) => {
      if (body instanceof FormData && body.get('contribution_id')) {
        controller.abort();
        throw Object.assign(new Error('canceled'), { name: 'CanceledError' });
      }
      return server.post(url, body, config);
    });
    const error = await runBatchedUpload({
      batches: splitIntoBatches(entries(45)),
      siteSlug: 'S',
      post,
      signal: controller.signal,
      sleep: noSleep,
    }).catch((e) => e);
    expect(error.name).toBe('CanceledError');
    expect(error.contributionId).toBe('c-1');
    expect(post).toHaveBeenCalledTimes(2);
  });

  it('refuses a 200 that is not a batch answer', async () => {
    const post = vi.fn(async () => ({ data: { output: 'Please sign in again.' } }));
    const error = await runBatchedUpload({
      batches: splitIntoBatches(entries(1)),
      siteSlug: 'S',
      post,
      sleep: noSleep,
    }).catch((e) => e);
    expect(error).toBeInstanceOf(BatchUploadError);
    expect(error.output).toBe('Please sign in again.');
  });
});

describe('discardUpload', () => {
  it('posts the id to discard', async () => {
    const server = fakeServer();
    expect(await discardUpload(server.post, 'c-9')).toBe(true);
    expect(server.calls).toEqual([{ kind: 'discard', body: { contribution_id: 'c-9' } }]);
  });

  it('does nothing without an id, and never throws', async () => {
    const post = vi.fn(async () => {
      throw new Error('offline');
    });
    expect(await discardUpload(post, null)).toBe(false);
    expect(post).not.toHaveBeenCalled();
    expect(await discardUpload(post, 'c-1')).toBe(false);
  });
});

describe('refusals', () => {
  const refusal = (status, error) => ({ response: { status, data: { error, output: 'x' } } });

  it.each([
    [413, 'too_many_files'],
    [413, 'too_large'],
    [429, 'too_many_open'],
    [409, 'finalized'],
  ])('%s %s is named and never retried', (status, code) => {
    expect(refusalCode(refusal(status, code))).toBe(code);
    expect(isRetryable(refusal(status, code))).toBe(false);
  });

  it("reads nginx's HTML 413 as too large", () => {
    expect(refusalCode({ response: { status: 413, data: '<html>' } })).toBe('too_large');
  });

  it('leaves a plain throttle retryable', () => {
    expect(refusalCode({ response: { status: 429, data: { error: 'Too many requests' } } })).toBeNull();
    expect(isRetryable({ response: { status: 429, data: {} } })).toBe(true);
  });

  it('has a message for every code in every language', () => {
    const lookup = (locale, path) => path.split('.').reduce((node, part) => node?.[part], locale);
    for (const key of Object.values(REFUSAL_MESSAGES)) {
      for (const locale of [en, hi, or]) {
        expect(typeof lookup(locale, key)).toBe('string');
      }
    }
  });

  it('stops at once with the code on the error', async () => {
    const server = fakeServer({ failures: { 1: [refusal(429, 'too_many_open')] } });
    const sleep = vi.fn(async () => {});
    const error = await runBatchedUpload({
      batches: splitIntoBatches(entries(25)),
      siteSlug: 'S',
      post: server.post,
      sleep,
    }).catch((e) => e);
    expect(error.code).toBe('too_many_open');
    expect(sleep).not.toHaveBeenCalled();
    expect(server.calls.filter((c) => c.kind === 'batch')).toHaveLength(2);
  });
});

describe('progress', () => {
  it('counts a batch only once the server confirms it; the batch on the wire is separate', async () => {
    const post = vi.fn(async (url, body, config) => {
      if (url.endsWith('/upload/finish/')) return { data: { status: 'Success' } };
      // Every byte of this batch is sent before it is answered.
      config.onUploadProgress?.({ loaded: 1, total: 1 });
      return { data: { status: 'Success', contribution_id: 'c-1', rejected: [] } };
    });
    const onProgress = vi.fn();
    const batches = splitIntoBatches(entries(25));
    await runBatchedUpload({ batches, siteSlug: 'S', post, sleep: noSleep, onProgress });
    const total = batches.reduce((acc, b) => acc + b.reduce((n, e) => n + e.file.size, 0), 0);
    // Before the first answer: nothing confirmed, the batch fully on the wire.
    const firstWire = onProgress.mock.calls.map(([p]) => p).find((p) => p.batch);
    expect(firstWire.sentBytes).toBe(0);
    expect(firstWire.sentFiles).toBe(0);
    expect(firstWire.batch).toMatchObject({ index: 0, count: 2, sentBytes: 20 * MB, totalBytes: 20 * MB });
    // After it: the first batch is counted, and nothing is in flight.
    const afterFirst = onProgress.mock.calls.map(([p]) => p).find((p) => !p.batch && p.sentFiles === 20);
    expect(afterFirst.sentBytes).toBe(20 * MB);
    expect(onProgress.mock.calls.at(-1)[0]).toMatchObject({ sentBytes: total, sentFiles: 25, batch: null });
  });

  it('announces each batch with its keys before it is sent, and the finish', async () => {
    const server = fakeServer();
    const onBatchStart = vi.fn();
    const onFinishing = vi.fn();
    const batches = splitIntoBatches(entries(25));
    await runBatchedUpload({ batches, siteSlug: 'S', post: server.post, sleep: noSleep, onBatchStart, onFinishing });
    expect(onBatchStart.mock.calls.map(([info]) => info)).toEqual([
      { index: 0, contributionId: null, keys: batches[0].map((e) => e.key) },
      { index: 1, contributionId: 'c-1', keys: batches[1].map((e) => e.key) },
    ]);
    expect(onFinishing).toHaveBeenCalledTimes(1);
  });
});

describe('discard without an id', () => {
  it('names the upload by site and keys', () => {
    expect(discardTarget({ contributionId: 'c-1', siteSlug: 'S', keys: ['a'] })).toEqual({ contribution_id: 'c-1' });
    expect(discardTarget({ siteSlug: 'S', keys: ['a', 'b'] })).toEqual({ mesh_vid: 'S', keys: ['a', 'b'] });
    expect(discardTarget({ siteSlug: 'S', keys: [] })).toBeNull();
    expect(discardTarget({})).toBeNull();
  });

  it('posts the keys when there is no id', async () => {
    const server = fakeServer();
    expect(await discardUpload(server.post, { siteSlug: 'S', keys: ['k1'] })).toBe(true);
    expect(server.calls).toEqual([{ kind: 'discard', body: { mesh_vid: 'S', keys: ['k1'] } }]);
  });
});

describe('beaconDiscard', () => {
  it('sends a form carrying the CSRF token, since a beacon has no headers', () => {
    const sendBeacon = vi.fn(() => true);
    expect(beaconDiscard({ siteSlug: 'S', keys: ['k1', 'k2'] }, 'tok', { sendBeacon })).toBe(true);
    const [url, form] = sendBeacon.mock.calls[0];
    expect(url).toMatch(/\/upload\/discard\/$/);
    expect(form.get('csrfmiddlewaretoken')).toBe('tok');
    expect(form.get('mesh_vid')).toBe('S');
    expect(form.getAll('keys')).toEqual(['k1', 'k2']);
    expect(form.get('contribution_id')).toBeNull();
  });

  it('prefers the id', () => {
    const sendBeacon = vi.fn(() => true);
    beaconDiscard({ contributionId: 'c-1', siteSlug: 'S', keys: ['k1'] }, 'tok', { sendBeacon });
    const form = sendBeacon.mock.calls[0][1];
    expect(form.get('contribution_id')).toBe('c-1');
    expect(form.getAll('keys')).toEqual([]);
  });

  it('does nothing with nothing to name, or no beacon', () => {
    const sendBeacon = vi.fn(() => true);
    expect(beaconDiscard({ siteSlug: 'S', keys: [] }, 'tok', { sendBeacon })).toBe(false);
    expect(beaconDiscard({ contributionId: 'c-1' }, 'tok', {})).toBe(false);
    expect(sendBeacon).not.toHaveBeenCalled();
  });
});
