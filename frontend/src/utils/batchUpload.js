/**
 * Batched uploads (the server side is `views._upload_batch`).
 *
 * A visit to a site is often a hundred phone photographs and a few videos --
 * several hundred MB. Sent as one POST, a dropped connection at 90% lost all
 * of it and a retry started from zero. Here the files go in batches of at most
 * `MAX_BATCH_FILES` files or `MAX_BATCH_BYTES` bytes into one *open*
 * contribution, then `upload/finish/` closes it:
 *
 *   POST upload/           batched=1, mesh_vid, files + keys  -> contribution_id
 *   POST upload/           ... + contribution_id               (each next batch)
 *   POST upload/finish/    { contribution_id }
 *   POST upload/discard/   { contribution_id }                 (cancel)
 *
 * Every file carries a stable key, so a batch whose *response* was lost can be
 * sent again without storing anything twice. That is what makes the automatic
 * retries and the Retry button safe.
 */
import { deploymentPrefix } from './deployment';

export const MAX_BATCH_FILES = 20;
export const MAX_BATCH_BYTES = 150 * 1024 * 1024;
/*
  The server's per-upload caps (`UPLOAD_MAX_FILES_PER_CONTRIBUTION`,
  `UPLOAD_MAX_BYTES_PER_CONTRIBUTION`), at their defaults. Checked before the
  first byte so a too-big selection is refused at once rather than after
  hundreds of megabytes; the server still enforces its own configured values.
*/
export const MAX_UPLOAD_FILES = 1000;
export const MAX_UPLOAD_BYTES = 3 * 1024 * 1024 * 1024;
/** Waits before the 1st, 2nd and 3rd automatic retry of one request. */
export const RETRY_DELAYS_MS = Object.freeze([2000, 8000, 30000]);

export const uploadUrl = () => `${deploymentPrefix()}/upload/`;
export const finishUrl = () => `${deploymentPrefix()}/upload/finish/`;
export const discardUrl = () => `${deploymentPrefix()}/upload/discard/`;

/**
 * A stable key for one file: a hash of its name, size and lastModified.
 *
 * Not a content hash -- reading 500 MB to hash it would cost more than the
 * retry it saves. Two cyrb53-style 32-bit lanes, hex, plus the size, which
 * keeps it inside the server's `[A-Za-z0-9_-]{1,64}`.
 */
export function fileKey(file) {
  const text = `${file?.name ?? ''}\u0000${file?.size ?? 0}\u0000${file?.lastModified ?? 0}`;
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  const hex = (n) => (n >>> 0).toString(16).padStart(8, '0');
  return `f${hex(h2)}${hex(h1)}-${Number(file?.size ?? 0).toString(36)}`;
}

/**
 * Split `entries` (`{ file, isVideo }`) into batches, in order.
 *
 * A batch closes at `maxFiles` files or before it would pass `maxBytes`,
 * whichever comes first. A single file over `maxBytes` (a long video) is a
 * batch on its own rather than an error: the per-file limits are enforced
 * elsewhere, and this only decides how files travel.
 *
 * Each entry gains its `key`; a key repeated in the selection is sent once.
 */
export function splitIntoBatches(entries, { maxFiles = MAX_BATCH_FILES, maxBytes = MAX_BATCH_BYTES } = {}) {
  const batches = [];
  let current = [];
  let currentBytes = 0;
  const seen = new Set();
  for (const entry of entries) {
    const key = entry.key || fileKey(entry.file);
    if (seen.has(key)) continue;
    seen.add(key);
    const size = entry.file?.size ?? 0;
    const full =
      current.length >= maxFiles || (current.length > 0 && currentBytes + size > maxBytes);
    if (full) {
      batches.push(current);
      current = [];
      currentBytes = 0;
    }
    current.push({ ...entry, key });
    currentBytes += size;
    if (size > maxBytes) {
      batches.push(current);
      current = [];
      currentBytes = 0;
    }
  }
  if (current.length) batches.push(current);
  return batches;
}

export const batchBytes = (batch) => batch.reduce((acc, { file }) => acc + (file?.size ?? 0), 0);

/** The multipart body for one batch. */
export function batchFormData(batch, { siteSlug, compress, contributionId }) {
  const form = new FormData();
  form.append('batched', '1');
  form.append('mesh_vid', siteSlug);
  if (contributionId) form.append('contribution_id', contributionId);
  if (compress) form.append('compress', 'true');
  for (const { file, isVideo, key } of batch) {
    form.append(isVideo ? 'videos' : 'images', file, file.name);
    form.append(isVideo ? 'video_keys' : 'image_keys', key);
  }
  return form;
}

export function isAbortError(error) {
  return error?.name === 'CanceledError' || error?.name === 'AbortError' || error?.code === 'ERR_CANCELED';
}

/*
  Refusals the server names, each with its own message. None is retried: the
  same request would only meet the same answer (a 429 `too_many_open` is not
  a throttle -- waiting does not close the other uploads).
*/
export const REFUSAL_MESSAGES = Object.freeze({
  too_many_files: 'contribute.refusal.tooManyFiles',
  too_large: 'contribute.refusal.tooLarge',
  too_many_open: 'contribute.refusal.tooManyOpen',
  finalized: 'contribute.refusal.finalized',
});

/** The server's refusal code for a failed request, if it gave one we know. */
export function refusalCode(error) {
  const response = error?.response;
  const code = response?.data?.error;
  if (typeof code === 'string' && REFUSAL_MESSAGES[code]) return code;
  // nginx's own 413 is an HTML page, with no code to read.
  if (response?.status === 413) return 'too_large';
  return null;
}

/**
 * Worth retrying automatically: no answer at all, a timeout, a throttle, or a
 * server-side failure. A 4xx other than those is a decision (signed out, not
 * yours, finished, expired, over a cap) that the same request will only meet
 * again.
 */
export function isRetryable(error) {
  if (isAbortError(error)) return false;
  if (refusalCode(error)) return false;
  const status = error?.response?.status;
  if (!status) return true;
  return status === 408 || status === 429 || status >= 500;
}

/** Waits `ms`, or rejects as an abort the moment `signal` fires. */
export function abortableSleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const abortError = () => Object.assign(new Error('canceled'), { name: 'CanceledError', code: 'ERR_CANCELED' });
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(abortError());
    }
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/** Thrown when a batch or the finish failed for good. Carries where to resume. */
export class BatchUploadError extends Error {
  constructor(cause, { resumeFrom, contributionId, restart }) {
    super(cause?.message || 'Upload failed');
    this.name = 'BatchUploadError';
    this.cause = cause;
    /** The first batch the server has not confirmed; `batches.length` = only finish is left. */
    this.resumeFrom = resumeFrom;
    this.contributionId = contributionId;
    /** The open upload cannot be continued (expired, finished, gone): start over. */
    this.restart = restart;
    this.output = cause?.response?.data?.output;
    /** A known refusal (`REFUSAL_MESSAGES`), or null. */
    this.code = refusalCode(cause);
  }
}

async function withRetries(attempt, { delays, signal, sleep, onRetry }) {
  for (let tries = 0; ; tries += 1) {
    try {
      return await attempt();
    } catch (error) {
      if (!isRetryable(error) || tries >= delays.length) throw error;
      onRetry?.({ attempt: tries + 1, delayMs: delays[tries], error });
      await sleep(delays[tries], signal);
    }
  }
}

/**
 * Send `batches` from `startAt`, then finish.
 *
 * `post(url, body, { signal, onUploadProgress })` is axios-shaped and resolves
 * to `{ data }`. Each request is retried up to `delays.length` times on a
 * retryable failure. On final failure this throws `BatchUploadError` with the
 * batch to resume from and the contribution to resume into; on abort it
 * rethrows the abort, with `contributionId` attached when one was opened.
 *
 * Progress is reported across *all* batches, in bytes, so the bar never jumps
 * back to zero between them. `sentBytes`/`sentFiles` count only batches the
 * server has *confirmed*; the batch on the wire is reported separately, as
 * `batch` (`{ index, count, sentBytes, totalBytes }`), because bytes sent are
 * not bytes stored -- a batch can be refused, or lost, after its last byte.
 *
 * `onBatchStart({ index, contributionId, keys })` fires before each batch, so
 * a caller can discard it by its keys if it is cancelled before an id exists.
 * `onFinishing` fires when only the finish is left.
 */
export async function runBatchedUpload({
  batches,
  startAt = 0,
  contributionId = null,
  siteSlug,
  compress = false,
  post,
  signal,
  delays = RETRY_DELAYS_MS,
  sleep = abortableSleep,
  onProgress,
  onRetry,
  onBatchStart,
  onBatchDone,
  onFinishing,
  jsonHeaders = {},
  headers = {},
}) {
  const totalBytes = batches.reduce((acc, batch) => acc + batchBytes(batch), 0);
  const totalFiles = batches.reduce((acc, batch) => acc + batch.length, 0);
  let sentBytes = batches.slice(0, startAt).reduce((acc, batch) => acc + batchBytes(batch), 0);
  let sentFiles = batches.slice(0, startAt).reduce((acc, batch) => acc + batch.length, 0);
  let id = contributionId;
  const rejected = [];

  const report = (batch = null) =>
    onProgress?.({
      sentBytes,
      totalBytes,
      sentFiles: Math.min(totalFiles, sentFiles),
      totalFiles,
      batch,
    });
  report();

  const fail = (error, index) => {
    if (isAbortError(error)) {
      throw Object.assign(error, { contributionId: id, resumeFrom: index });
    }
    const status = error?.response?.status;
    const code = error?.response?.data?.error;
    const restart = status === 404 || (status === 409 && (code === 'expired' || code === 'finalized'));
    throw new BatchUploadError(error, { resumeFrom: index, contributionId: restart ? null : id, restart });
  };

  for (let index = startAt; index < batches.length; index += 1) {
    const batch = batches[index];
    const bytes = batchBytes(batch);
    onBatchStart?.({ index, contributionId: id, keys: batch.map((entry) => entry.key) });
    let data;
    try {
      const response = await withRetries(
        () =>
          post(uploadUrl(), batchFormData(batch, { siteSlug, compress, contributionId: id }), {
            withCredentials: true,
            headers,
            signal,
            onUploadProgress: ({ loaded, total }) => {
              const fraction = total ? Math.min(1, loaded / total) : 0;
              report({
                index,
                count: batches.length,
                sentBytes: Math.round(bytes * fraction),
                totalBytes: bytes,
              });
            },
          }),
        { delays, signal, sleep, onRetry: (info) => onRetry?.({ ...info, batch: index }) },
      );
      data = response?.data || {};
    } catch (error) {
      fail(error, index);
    }
    if (data.status !== 'Success' || !data.contribution_id) {
      // A 200 that is not a batch answer: an old server, or the legacy view's
      // "200 with an explanation" for a signed-out session.
      throw new BatchUploadError(Object.assign(new Error(data.output || 'Upload failed'), { response: { data } }), {
        resumeFrom: index,
        contributionId: id,
        restart: !id,
      });
    }
    id = data.contribution_id;
    rejected.push(...(data.rejected || []));
    sentBytes += bytes;
    sentFiles += batch.length;
    report();
    onBatchDone?.({ index, contributionId: id, data });
  }

  onFinishing?.({ contributionId: id });
  try {
    const response = await withRetries(
      () => post(finishUrl(), { contribution_id: id }, { withCredentials: true, headers: jsonHeaders, signal }),
      { delays, signal, sleep, onRetry: (info) => onRetry?.({ ...info, batch: batches.length }) },
    );
    return { contributionId: id, rejected, data: response?.data || {} };
  } catch (error) {
    return fail(error, batches.length);
  }
}

/**
 * What identifies an open upload to `upload/discard/`: its id, or -- when the
 * first batch was cancelled before its answer brought one -- the site and that
 * batch's keys. Null when there is nothing to name.
 */
export function discardTarget({ contributionId, siteSlug, keys } = {}) {
  if (contributionId) return { contribution_id: contributionId };
  if (siteSlug && keys?.length) return { mesh_vid: siteSlug, keys: [...keys] };
  return null;
}

/**
 * Best-effort: delete an open upload the contributor cancelled. Never throws.
 *
 * `target` is a contribution id, or `{ contributionId, siteSlug, keys }`.
 */
export async function discardUpload(post, target, headers = {}) {
  const body = discardTarget(typeof target === 'string' ? { contributionId: target } : target || {});
  if (!body) return false;
  try {
    await post(discardUrl(), body, { withCredentials: true, headers });
    return true;
  } catch {
    // The sweep deletes an upload whose first batch was never acknowledged,
    // and closes any other after `UPLOAD_OPEN_TTL_MINUTES`.
    return false;
  }
}

/**
 * The same discard from a page that is going away (`pagehide`).
 *
 * `navigator.sendBeacon` survives the unload where a fetch would be cut off,
 * but it cannot set headers, so the CSRF token travels in the form body as
 * `csrfmiddlewaretoken` -- which Django's CSRF middleware accepts, alongside
 * its same-origin check. Returns whether the beacon was queued.
 */
export function beaconDiscard(target, csrfToken, nav = globalThis.navigator) {
  const body = discardTarget(target);
  if (!body || typeof nav?.sendBeacon !== 'function') return false;
  const form = new FormData();
  if (csrfToken) form.append('csrfmiddlewaretoken', csrfToken);
  if (body.contribution_id) form.append('contribution_id', body.contribution_id);
  if (body.mesh_vid) form.append('mesh_vid', body.mesh_vid);
  for (const key of body.keys || []) form.append('keys', key);
  try {
    return nav.sendBeacon(discardUrl(), form);
  } catch {
    return false;
  }
}
