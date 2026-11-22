/**
 * What the upload dialog tells a contributor, and in which language.
 *
 * Every message here was wrong in a way no status code and no console warning
 * shows: a file refused for its size was refused with the word "Ready", the
 * toasts stayed English inside an Odia dialog, and the preview opened over the
 * form without taking focus with it. All three are only visible from the
 * outside, which is what this renders.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import axios from 'axios';

import i18n from '../i18n/config';
import ContributeModal from './ContributeModal';
import { outgoingFile } from '../utils/outgoingFile';
import { axeViolations } from '../test/axe';

/*
  The server, as the batched upload sees it: each batch answers with the open
  contribution's id, and `upload/finish/` with the closing message.
*/
vi.mock('axios', () => ({
  default: {
    post: vi.fn(async (url) =>
      String(url).includes('/upload/finish/')
        ? { data: { status: 'Success', output: 'Stored 1 image.' } }
        : { data: { status: 'Success', contribution_id: 'c-1', received: [], rejected: [], count: 1 } },
    ),
  },
}));

/** The multipart batches sent, in order (finish and discard are JSON). */
const batchCalls = () => axios.post.mock.calls.filter(([url]) => /\/upload\/$/.test(String(url)));

vi.mock('../utils/session', async (importOriginal) => ({
  ...(await importOriginal()),
  fetchSession: vi.fn(async () => ({
    authenticated: true,
    contributor: {
      name: 'Contributor',
      email: 'contributor@example.com',
      active: true,
      banned: false,
    },
  })),
}));

/*
  Compression throws, which is not a contrived case: it is a web worker, jsdom
  has none, and a browser that runs out of memory on a 70 MB frame lands in the
  same catch. It is also the only way a file reaches the upload limit at all --
  compression aims just under it.
*/
vi.mock('browser-image-compression', () => ({
  default: vi.fn(async () => {
    throw new Error('no worker here');
  }),
}));

/*
  jsdom decodes no image, so the real validator refuses everything as
  undecodable and never reaches the branches below. This stands in for a
  photograph a browser accepts.
*/
const validation = vi.hoisted(() => ({
  current: { valid: true, severity: 'valid', reasonKey: 'validation.ready' },
}));
vi.mock('../utils/imageValidation', async (importOriginal) => ({
  ...(await importOriginal()),
  validateImageFile: vi.fn(async () => ({ ...validation.current })),
}));

/** A File of a given size without allocating it. */
function fakeFile(name, size) {
  const file = new File(['x'], name, { type: 'image/jpeg' });
  Object.defineProperty(file, 'size', { value: size });
  return file;
}

function select(...files) {
  const input = document.querySelector('input[type="file"]');
  Object.defineProperty(input, 'files', { value: files, configurable: true });
  fireEvent.change(input);
}

async function open() {
  const view = render(
    <ContributeModal
      isOpen
      onClose={() => {}}
      siteName="Konark Sun Temple"
      siteSlug="India__Odisha__Puri__Konark_Sun_Temple"
      retryDelays={[0, 0, 0]}
    />,
  );
  // The file input refuses anything until the session says this contributor may
  // upload, and that is a fetch.
  await screen.findByText('contributor@example.com');
  return view;
}

beforeEach(() => {
  validation.current = { valid: true, severity: 'valid', reasonKey: 'validation.ready' };
  // jsdom has no object URLs, and the preview is built from one per file.
  globalThis.URL.createObjectURL = vi.fn(() => 'blob:preview');
  globalThis.URL.revokeObjectURL = vi.fn();
  axios.post.mockClear();
  // `checkPreUpload` gates every submit and is a bare fetch.
  globalThis.fetch = vi.fn(async () => ({
    ok: true,
    json: async () => ({ allowupload: true, output: 'ok' }),
  }));
});

afterEach(async () => {
  cleanup();
  await i18n.changeLanguage('en');
});

describe('full resolution', () => {
  /*
    The substance of the change, and invisible if it regresses: an unticked box
    sends `compress=true`, so the server re-encodes every photograph and the
    only sign is EXIF and pixels missing from the archive months later.
  */
  it('is on before the contributor touches anything', async () => {
    await open();
    const box = screen.getByLabelText(i18n.t('contribute.fullResLabel'));
    expect(box).toBeChecked();
  });

  it('sends no compress parameter while it is on', async () => {
    await open();
    select(fakeFile('statue.jpg', 6 * 1024 * 1024));
    await screen.findByText(i18n.t('contribute.filesAdded', { count: 1 }));

    fireEvent.click(screen.getByLabelText(i18n.t('contribute.agree'), { exact: false }));
    fireEvent.click(document.querySelector('.upload-btn'));

    await waitFor(() => expect(batchCalls().length).toBeGreaterThan(0));
    const sent = batchCalls()[0][1];
    expect(sent.get('compress')).toBeNull();
    expect(sent.get('batched')).toBe('1');
  });
});

describe('a file that is still too large after compression', () => {
  it('is refused for its size, not with the reason it arrived with', async () => {
    /*
      The rejection wrote a finished sentence to `reason`, which nothing reads
      any more. The card and the toast both render `reasonKey`, and the spread
      left that as `validation.ready` -- so the contributor was told "Ready"
      under an "Invalid" badge and never learnt the file was too big.
    */
    await open();
    select(fakeFile('temple.jpg', 70 * 1024 * 1024));

    await waitFor(() => expect(document.querySelector('.preview-reason')).toBeTruthy());
    expect(document.querySelector('.preview-reason')).toHaveTextContent(
      i18n.t('contribute.tooLargeAfterCompression'),
    );
    expect(document.querySelector('.preview-reason')).not.toHaveTextContent(
      i18n.t('validation.ready'),
    );
  });

  it('says so in the toast as well', async () => {
    await open();
    select(fakeFile('temple.jpg', 70 * 1024 * 1024));

    expect(
      await screen.findByText(
        i18n.t('validation.ignored', {
          name: 'temple.jpg',
          reason: i18n.t('contribute.tooLargeAfterCompression'),
        }),
      ),
    ).toBeInTheDocument();
  });
});

describe('the dialog in Odia', () => {
  it('counts added files with Odia words rather than an English "s"', async () => {
    await i18n.changeLanguage('or');
    await open();
    select(fakeFile('one.jpg', 900 * 1024), fakeFile('two.jpg', 900 * 1024));

    expect(
      await screen.findByText(i18n.t('contribute.filesAdded', { count: 2 })),
    ).toBeInTheDocument();
    expect(screen.queryByText('2 files added to preview.')).toBeNull();
  });

  it('says a file is already added in the reading language', async () => {
    await i18n.changeLanguage('or');
    await open();
    const file = fakeFile('one.jpg', 900 * 1024);
    select(file);
    await screen.findByText(i18n.t('contribute.filesAdded', { count: 1 }));
    select(file);

    expect(
      await screen.findByText(i18n.t('contribute.alreadyAdded', { name: 'one.jpg' })),
    ).toBeInTheDocument();
  });

  it('labels the whole filter row in one script', async () => {
    await i18n.changeLanguage('or');
    await open();
    select(fakeFile('one.jpg', 900 * 1024));

    await screen.findByRole('button', { name: i18n.t('contribute.filterAll') });
    for (const label of ['All', 'Warning', 'Invalid']) {
      expect(screen.queryByRole('button', { name: label })).toBeNull();
    }
  });
});

describe('the preview filter row', () => {
  it('says which filter is in force, rather than only colouring it', async () => {
    await open();
    select(fakeFile('one.jpg', 900 * 1024));

    const all = await screen.findByRole('button', { name: i18n.t('contribute.filterAll') });
    const invalid = screen.getByRole('button', {
      name: i18n.t('contribute.severityInvalid'),
    });
    expect(all).toHaveAttribute('aria-pressed', 'true');
    expect(invalid).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(invalid);
    expect(invalid).toHaveAttribute('aria-pressed', 'true');
    expect(all).toHaveAttribute('aria-pressed', 'false');
  });
});

describe('the image preview', () => {
  async function openPreview() {
    await open();
    select(fakeFile('one.jpg', 900 * 1024));
    const thumbnail = await screen.findByRole('button', {
      name: i18n.t('contribute.openPreview', { name: 'one.jpg' }),
    });
    // A browser focuses a button when it is clicked and jsdom does not, so this
    // is the keyboard route: Tab to the thumbnail, then Enter.
    thumbnail.focus();
    fireEvent.click(thumbnail);
    return thumbnail;
  }

  it('is a dialog, and takes focus with it', async () => {
    /*
      It is `position: fixed; inset: 0` over an 80%-opaque scrim. Focus used to
      stay on the thumbnail underneath it, where the focus ring cannot be seen,
      and a screen reader was told nothing had opened.
    */
    await openPreview();

    const preview = await screen.findByRole('dialog', {
      name: i18n.t('a11y.imagePreview', { name: 'one.jpg' }),
    });
    expect(preview).toContainElement(document.activeElement);
  });

  it('keeps Tab inside it, not on the form behind the scrim', async () => {
    await openPreview();

    const preview = await screen.findByRole('dialog', {
      name: i18n.t('a11y.imagePreview', { name: 'one.jpg' }),
    });
    fireEvent.keyDown(window, { key: 'Tab' });
    expect(preview).toContainElement(document.activeElement);
  });

  it('gives focus back to the thumbnail when it closes', async () => {
    const thumbnail = await openPreview();

    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(document.activeElement).toBe(thumbnail));
  });
});

/**
 * The reason the Upload button is unavailable (internal design notes).
 *
 * `handleUpload` carried messages for "no usable files" and "terms not
 * accepted", and neither could ever be seen: `submitDisabled` covers exactly
 * those two states, so the click that would have shown them never happened. The
 * contributor got a dead control and no explanation, in any language.
 *
 * Saying it before the click is also better than saying it after -- nothing has
 * to be attempted to learn what is missing.
 */
describe('why the upload button is unavailable', () => {
  it('names the missing requirement rather than leaving a dead button', async () => {
    await open();

    // By class: several controls in this dialog match /upload/i by name.
    const upload = document.querySelector('.upload-btn');
    expect(upload).toBeDisabled();

    const reason = document.getElementById('upload-blocked-reason');
    expect(reason, 'no reason was rendered beside the disabled button').toBeTruthy();
    expect(reason.textContent.trim()).not.toBe('');
    // Tied to the button, so a screen reader gives the reason with the state
    // rather than announcing "Upload, dimmed" and stopping there.
    expect(upload).toHaveAttribute('aria-describedby', 'upload-blocked-reason');
  });

  it('moves on to the next requirement once one is met', async () => {
    await open();
    // Captured *before* the files land: afterwards the reason has already moved
    // on, and comparing it with itself would pass whatever the code did.
    const withNoFiles = document.getElementById('upload-blocked-reason').textContent;

    select(fakeFile('temple.jpg', 2 * 1024 * 1024));

    await waitFor(() => {
      const now = document.getElementById('upload-blocked-reason');
      expect(now?.textContent).not.toBe(withNoFiles);
    });
    // The terms checkbox is the only thing left, and it says so.
    expect(document.getElementById('upload-blocked-reason').textContent).toMatch(/terms/i);
  });
});


/*
  Upload robustness (review 2026-11-22 §6): a single POST of up to 900 MB, with
  no cancel, no retry, no warning before leaving the page and no sign that the
  server was still working after the last byte.
*/
async function readyToUpload(file = fakeFile('statue.jpg', 6 * 1024 * 1024)) {
  await open();
  select(file);
  await screen.findByText(i18n.t('contribute.filesAdded', { count: 1 }));
  fireEvent.click(screen.getByLabelText(i18n.t('contribute.agree'), { exact: false }));
}

/** A POST that stays open until the test settles it or the signal aborts. */
function pendingPost() {
  const control = {};
  axios.post.mockImplementationOnce(
    (url, body, config) =>
      new Promise((resolve, reject) => {
        control.config = config;
        control.resolve = resolve;
        config.signal?.addEventListener('abort', () => {
          const error = new Error('canceled');
          error.name = 'CanceledError';
          error.code = 'ERR_CANCELED';
          reject(error);
        });
      }),
  );
  return control;
}

describe('upload robustness', () => {
  it('can be cancelled, keeps the selection, and offers a retry', async () => {
    const post = pendingPost();
    await readyToUpload();
    fireEvent.click(document.querySelector('.upload-btn'));
    await waitFor(() => expect(post.config).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: i18n.t('contribute.cancel') }));

    expect(
      (await screen.findAllByText(i18n.t('contribute.cancelled'))).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText('statue.jpg')).toBeInTheDocument();
    const retry = screen.getByRole('button', { name: i18n.t('contribute.retry') });
    expect(retry).toBeEnabled();

    fireEvent.click(retry);
    await waitFor(() => expect(batchCalls()).toHaveLength(2));
  });

  it('keeps the selection and offers a retry after a failure', async () => {
    // Every automatic retry fails too, so the contributor is the one to decide.
    for (let i = 0; i < 4; i += 1) {
      axios.post.mockImplementationOnce(async () => {
        throw Object.assign(new Error('Network Error'), { response: undefined });
      });
    }
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await readyToUpload();
    fireEvent.click(document.querySelector('.upload-btn'));

    expect(
      await screen.findByRole('button', { name: i18n.t('contribute.retry') }),
    ).toBeEnabled();
    expect(screen.getByText('statue.jpg')).toBeInTheDocument();
    console.error.mockRestore();
  });

  it('keeps Cancel until the finish starts, and only then says processing', async () => {
    const batch = pendingPost();
    const finish = pendingPost();
    await readyToUpload();
    fireEvent.click(document.querySelector('.upload-btn'));
    await waitFor(() => expect(batch.config).toBeTruthy());

    // Every byte of the batch sent, but not yet confirmed: still cancellable,
    // and the bar has not counted it.
    act(() => batch.config.onUploadProgress({ loaded: 100, total: 100 }));
    expect(screen.queryByText(i18n.t('contribute.processing'))).toBeNull();
    expect(screen.getByRole('button', { name: i18n.t('contribute.cancel') })).toBeInTheDocument();
    expect(screen.getByText('0%')).toBeInTheDocument();

    await act(async () =>
      batch.resolve({ data: { status: 'Success', contribution_id: 'c-1', received: [], count: 1 } }),
    );
    await waitFor(() => expect(finish.config).toBeTruthy());
    expect(screen.getByText(i18n.t('contribute.processing'))).toBeInTheDocument();
    // No cancel once the finish is under way: the upload is complete.
    expect(screen.queryByRole('button', { name: i18n.t('contribute.cancel') })).toBeNull();

    await act(async () => finish.resolve({ data: { status: 'Success', output: 'Thanks' } }));
  });

  it('warns before the page is left mid-upload, and not otherwise', async () => {
    const post = pendingPost();
    await readyToUpload();

    const idle = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(idle);
    expect(idle.defaultPrevented).toBe(false);

    fireEvent.click(document.querySelector('.upload-btn'));
    await waitFor(() => expect(post.config).toBeTruthy());
    const leaving = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(leaving);
    expect(leaving.defaultPrevented).toBe(true);

    await act(async () =>
      post.resolve({ data: { status: 'Success', contribution_id: 'c-1', received: [], count: 1 } }),
    );
  });
});

describe('batched upload', () => {
  const many = (n) => Array.from({ length: n }, (_, i) => fakeFile(`IMG_${i}.jpg`, 2 * 1024 * 1024));

  async function readyWith(files) {
    await open();
    select(...files);
    await screen.findByText(i18n.t('contribute.filesAdded', { count: files.length }));
    fireEvent.click(screen.getByLabelText(i18n.t('contribute.agree'), { exact: false }));
  }

  const urls = () => axios.post.mock.calls.map(([url]) => String(url).replace(/^.*\/upload\//, 'upload/'));

  it('sends 20 files per batch into one contribution, then finishes', async () => {
    await readyWith(many(25));
    fireEvent.click(document.querySelector('.upload-btn'));
    await waitFor(() => expect(urls()).toEqual(['upload/', 'upload/', 'upload/finish/']));
    const [first, second] = batchCalls().map(([, body]) => body);
    expect(first.getAll('images')).toHaveLength(20);
    expect(first.getAll('image_keys')).toHaveLength(20);
    expect(first.get('contribution_id')).toBeNull();
    expect(second.getAll('images')).toHaveLength(5);
    expect(second.get('contribution_id')).toBe('c-1');
    expect(axios.post.mock.calls[2][1]).toEqual({ contribution_id: 'c-1' });
    expect(await screen.findAllByText('Stored 1 image.')).not.toHaveLength(0);
  });

  it('discards the open upload on cancel', async () => {
    await readyWith(many(25));
    // The first batch answers at once; the second is held open.
    axios.post.mockImplementationOnce(async () => ({
      data: { status: 'Success', contribution_id: 'c-1', received: [], count: 20 },
    }));
    const second = pendingPost();
    fireEvent.click(document.querySelector('.upload-btn'));
    await waitFor(() => expect(second.config).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: i18n.t('contribute.cancel') }));
    await waitFor(() => expect(urls()).toContain('upload/discard/'));
    const discard = axios.post.mock.calls.find(([url]) => String(url).endsWith('/upload/discard/'));
    expect(discard[1]).toEqual({ contribution_id: 'c-1' });
    expect(urls()).not.toContain('upload/finish/');
  });

  it('resumes from the first unsent batch, into the same contribution', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await readyWith(many(45));
    axios.post.mockImplementationOnce(async () => ({
      data: { status: 'Success', contribution_id: 'c-1', received: [], count: 20 },
    }));
    for (let i = 0; i < 4; i += 1) {
      axios.post.mockImplementationOnce(async () => {
        throw Object.assign(new Error('Network Error'), { response: undefined });
      });
    }
    fireEvent.click(document.querySelector('.upload-btn'));
    const retry = await screen.findByRole('button', { name: i18n.t('contribute.retry') });
    expect(batchCalls()).toHaveLength(5);
    const failedKeys = batchCalls()[1][1].getAll('image_keys');

    axios.post.mockClear();
    fireEvent.click(retry);
    await waitFor(() => expect(urls()).toEqual(['upload/', 'upload/', 'upload/finish/']));
    const resumed = batchCalls()[0][1];
    expect(resumed.get('contribution_id')).toBe('c-1');
    expect(resumed.getAll('image_keys')).toEqual(failedKeys);
    console.error.mockRestore();
  });

  it('starts one upload however fast the button is pressed', async () => {
    const batch = pendingPost();
    await readyWith(many(1));
    /*
      Two activations in the same tick, before React re-renders the button
      disabled -- a double tap, or Enter and a click together. A real
      `click` would find it disabled the second time, so the handler itself
      is called.
    */
    const button = document.querySelector('.upload-btn');
    const props = button[Object.keys(button).find((key) => key.startsWith('__reactProps$'))];
    await act(async () => {
      props.onClick();
      props.onClick();
    });
    await waitFor(() => expect(batch.config).toBeTruthy());
    expect(batchCalls()).toHaveLength(1);
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    await act(async () =>
      batch.resolve({ data: { status: 'Success', contribution_id: 'c-1', received: [], count: 1 } }),
    );
  });

  it('discards by keys when cancelled before the first batch answers', async () => {
    const first = pendingPost();
    await readyWith(many(3));
    fireEvent.click(document.querySelector('.upload-btn'));
    await waitFor(() => expect(first.config).toBeTruthy());
    const keys = batchCalls()[0][1].getAll('image_keys');

    fireEvent.click(screen.getByRole('button', { name: i18n.t('contribute.cancel') }));
    await waitFor(() => expect(urls()).toContain('upload/discard/'));
    const discard = axios.post.mock.calls.find(([url]) => String(url).endsWith('/upload/discard/'));
    expect(discard[1]).toEqual({ mesh_vid: 'India__Odisha__Puri__Konark_Sun_Temple', keys });
  });

  it.each([
    [413, 'too_many_files', 'contribute.refusal.tooManyFiles'],
    [413, 'too_large', 'contribute.refusal.tooLarge'],
    [429, 'too_many_open', 'contribute.refusal.tooManyOpen'],
    [409, 'finalized', 'contribute.refusal.finalized'],
  ])('explains a %s %s in the visitor\'s language and does not retry it', async (status, code, key) => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await readyWith(many(1));
    axios.post.mockImplementationOnce(async () => {
      throw Object.assign(new Error('refused'), {
        response: { status, data: { status: 'Error', error: code, output: 'server words' } },
      });
    });
    fireEvent.click(document.querySelector('.upload-btn'));
    expect((await screen.findAllByText(i18n.t(key))).length).toBeGreaterThan(0);
    expect(screen.queryByText('server words')).toBeNull();
    expect(batchCalls()).toHaveLength(1);
    console.error.mockRestore();
  });

  it('beacons a discard if the page goes away mid-upload', async () => {
    const sendBeacon = vi.fn(() => true);
    Object.defineProperty(navigator, 'sendBeacon', { value: sendBeacon, configurable: true });
    const first = pendingPost();
    await readyWith(many(2));
    fireEvent.click(document.querySelector('.upload-btn'));
    await waitFor(() => expect(first.config).toBeTruthy());
    const keys = batchCalls()[0][1].getAll('image_keys');

    window.dispatchEvent(new Event('pagehide'));
    expect(sendBeacon).toHaveBeenCalledTimes(1);
    const [url, form] = sendBeacon.mock.calls[0];
    expect(String(url)).toMatch(/\/upload\/discard\/$/);
    expect(form.getAll('keys')).toEqual(keys);
    expect(form.get('mesh_vid')).toBe('India__Odisha__Puri__Konark_Sun_Temple');

    await act(async () =>
      first.resolve({ data: { status: 'Success', contribution_id: 'c-1', received: [], count: 2 } }),
    );
    delete navigator.sendBeacon;
  });

  it('beacons nothing once the finish has started', async () => {
    const sendBeacon = vi.fn(() => true);
    Object.defineProperty(navigator, 'sendBeacon', { value: sendBeacon, configurable: true });
    axios.post.mockImplementationOnce(async () => ({
      data: { status: 'Success', contribution_id: 'c-1', received: [], count: 1 },
    }));
    const finish = pendingPost();
    await readyWith(many(1));
    fireEvent.click(document.querySelector('.upload-btn'));
    await waitFor(() => expect(finish.config).toBeTruthy());

    window.dispatchEvent(new Event('pagehide'));
    expect(sendBeacon).not.toHaveBeenCalled();

    await act(async () => finish.resolve({ data: { status: 'Success', output: 'Thanks' } }));
    delete navigator.sendBeacon;
  });
});

describe('which bytes are sent', () => {
  const MB = 1024 * 1024;
  const original = { size: 30 * MB, name: 'a.jpg' };
  const compressed = { size: 9 * MB, name: 'a.jpg' };

  it('sends the original at full resolution when it is within the limit', () => {
    expect(outgoingFile({ originalFile: original, file: original }, true)).toBe(original);
  });

  it('never sends an original over the per-file limit, even at full resolution', () => {
    const huge = { size: 70 * MB, name: 'b.jpg' };
    expect(outgoingFile({ originalFile: huge, file: compressed }, true)).toBe(compressed);
  });

  it('sends the checked copy when full resolution is off', () => {
    expect(outgoingFile({ originalFile: original, file: compressed }, false)).toBe(compressed);
  });

  it('uploads the compressed copy of an oversized photograph with full resolution on', async () => {
    const imageCompression = (await import('browser-image-compression')).default;
    imageCompression.mockImplementationOnce(async () => new Blob(['y'], { type: 'image/jpeg' }));
    await readyToUpload(fakeFile('huge.jpg', 70 * 1024 * 1024));
    fireEvent.click(document.querySelector('.upload-btn'));
    await waitFor(() => expect(batchCalls().length).toBeGreaterThan(0));
    const sent = batchCalls()[0][1].getAll('images');
    expect(sent).toHaveLength(1);
    expect(sent[0].size).toBeLessThan(64 * 1024 * 1024);
  });
});

describe('the preview grid', () => {
  it('lazy-loads thumbnails and decodes them off the main path', async () => {
    await open();
    select(fakeFile('one.jpg', 900 * 1024));
    const thumbnail = await screen.findByRole('button', {
      name: i18n.t('contribute.openPreview', { name: 'one.jpg' }),
    });
    const img = thumbnail.querySelector('img');
    expect(img).toHaveAttribute('loading', 'lazy');
    expect(img).toHaveAttribute('decoding', 'async');
  });

  it('shows a placeholder tile for HEIC instead of decoding it', async () => {
    await open();
    const heic = new File(['x'], 'IMG_0001.HEIC', { type: 'image/heic' });
    select(heic);
    const thumbnail = await screen.findByRole('button', {
      name: i18n.t('contribute.openPreview', { name: 'IMG_0001.HEIC' }),
    });
    expect(thumbnail.querySelector('img')).toBeNull();
    expect(thumbnail.querySelector('.preview-heic-fallback')).not.toBeNull();
  });

  it('revokes every object URL when the dialog unmounts', async () => {
    const view = await open();
    select(fakeFile('one.jpg', 900 * 1024));
    await screen.findByText(i18n.t('contribute.filesAdded', { count: 1 }));
    view.unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview');
  });
});

describe('accessibility', () => {
  it('has no axe violations when open with a selection', async () => {
    const { container } = await open();
    select(fakeFile('one.jpg', 900 * 1024));
    await screen.findByText(i18n.t('contribute.filesAdded', { count: 1 }));
    expect(await axeViolations(container)).toEqual([]);
  });
});
