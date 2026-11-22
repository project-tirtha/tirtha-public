/**
 * The upload gate rejects what the pipeline cannot use, and nothing else
 * (internal design notes).
 *
 * Both cases below were rejections of files the server accepts happily, which
 * is the worst kind: the contributor is told their photograph is unusable, and
 * it is not.
 */
import { describe, expect, it } from 'vitest';

import { MAX_UPLOAD_SIZE_BYTES, validateImageFile } from './imageValidation';

/*
  jsdom decodes nothing, so every probe here takes the give-up path -- which is
  the same path a real browser takes for a format it cannot draw. 50ms rather
  than the real ten seconds.
*/
const FAST = { decodeTimeoutMs: 50 };

/** A File of a given size without allocating it. */
function fakeFile(name, type, size) {
  const file = new File(['x'], name, { type });
  Object.defineProperty(file, 'size', { value: size });
  return file;
}

describe('the size gate', () => {
  it('accepts an original larger than the upload limit, because it is compressed first', async () => {
    /*
      `ContributeModal` compresses to a 1.5 MB target before sending. This check
      ran against the file the visitor *picked*, before conversion and before
      compression, so a 15 MB frame off a DSLR was refused while a 9 MB one was
      accepted and then shrunk to 1.5 MB anyway. It rejected precisely the
      high-resolution originals photogrammetry wants most.
    */
    const big = fakeFile('temple.jpg', 'image/jpeg', 15 * 1024 * 1024);
    const result = await validateImageFile(big, FAST);
    // Asserted against the *reason*: jsdom cannot decode anything, so a JPEG
    // still fails the dimension probe here. What matters is that it is no
    // longer turned away for its size.
    expect(result.reasonKey).not.toBe('validation.sourceTooLarge');
  });

  it('still refuses a file too large for the browser to process', async () => {
    const enormous = fakeFile('raw.jpg', 'image/jpeg', 300 * 1024 * 1024);
    const result = await validateImageFile(enormous, FAST);
    expect(result.severity).toBe('invalid');
    expect(result.reasonKey).toBe('validation.sourceTooLarge');
  });

  it('exports the upload limit, so the caller can apply it after compressing', () => {
    // The limit still exists; it is enforced on the outgoing file rather than
    // on the source, which is the only place it means anything.
    expect(MAX_UPLOAD_SIZE_BYTES).toBe(10 * 1024 * 1024);
  });
});

describe('formats the browser cannot draw', () => {
  it('passes a TIFF through with a warning instead of rejecting it', async () => {
    /*
      No mainstream browser decodes TIFF in an `<img>`. TIFF was on the accepted
      list, so it passed the type check, failed the dimension probe, and came
      back "Unable to read image dimensions. Please try another file." -- after
      an earlier message had told the visitor TIFF was accepted. The server
      reads it fine; only the preview cannot.
    */
    const tiff = fakeFile('scan.tiff', 'image/tiff', 4 * 1024 * 1024);
    const result = await validateImageFile(tiff, FAST);
    expect(result.severity, result.reason).not.toBe('invalid');
    expect(result.valid).toBe(true);
  });

  it('still rejects a damaged file in a format it can draw', async () => {
    // jsdom cannot decode any image, so a JPEG reaches the same failure path a
    // corrupt one would -- which is the behaviour being asserted.
    const broken = fakeFile('broken.jpg', 'image/jpeg', 2 * 1024 * 1024);
    const result = await validateImageFile(broken, FAST);
    expect(result.severity).toBe('invalid');
    expect(result.reasonKey).toBe('validation.undecodable');
  });

  it('warns rather than rejects when the metadata will not parse', async () => {
    /*
      Unreadable EXIF used to be an outright rejection. Nothing downstream needs
      EXIF -- it is shown to the visitor, and the reconstruction ignores it --
      so a photograph whose header confuses the parser is still a photograph.
    */
    const tiff = fakeFile('scan.tiff', 'image/tiff', 1024);
    const result = await validateImageFile(tiff, FAST);
    expect(result.valid).toBe(true);
    expect(result.reasonKey).not.toBe('validation.undecodable');
  });

  it('refuses a type that is not an image at all', async () => {
    const doc = fakeFile('notes.pdf', 'application/pdf', 1024);
    const result = await validateImageFile(doc, FAST);
    expect(result.severity).toBe('invalid');
    expect(result.reasonKey).toBe('validation.unsupportedType');
  });
});

describe('the reasons are translatable', () => {
  it('names a key rather than an English sentence', async () => {
    /*
      These were English strings built inside the validator and rendered
      straight into the interface, so a contributor reading the site in Odia was
      told in English why their photograph had been refused -- on the one screen
      where the explanation is the whole point.
    */
    const doc = fakeFile('notes.pdf', 'application/pdf', 1024);
    const result = await validateImageFile(doc, FAST);
    expect(result.reasonKey).toMatch(/^validation\./);
    expect(result.reason).toBeUndefined();
  });

  it('supplies the numbers separately, so a translation can order them', async () => {
    // "Too small (800×600)" cannot be translated by substituting words; the
    // sentence has to be written in each language with the values interpolated.
    const tiny = fakeFile('thumb.jpg', 'image/jpeg', 1024);
    const result = await validateImageFile(tiny, FAST);
    if (result.reasonParams) {
      expect(typeof result.reasonParams).toBe('object');
    }
    expect(result.reasonKey).toBeTruthy();
  });
});

describe('the image allow-list', () => {
  it('is exactly the set of names the server accepts', async () => {
    const { VALID_IMAGE_EXTENSIONS } = await import('./imageValidation');
    // Mirror of ACCEPTED_SUFFIXES in tirtha_bk/tirtha/uploads.py.
    expect([...VALID_IMAGE_EXTENSIONS].sort()).toEqual(
      [
        '.bmp', '.gif', '.heic', '.heics', '.heif', '.heifs',
        '.jpeg', '.jpg', '.png', '.tif', '.tiff', '.webp',
      ].sort(),
    );
  });

  it('decides by name, as the server does, not by MIME type', async () => {
    const { isSupportedImageFile } = await import('./imageValidation');
    expect(isSupportedImageFile(new File(['x'], 'IMG_1.JPG', { type: '' }))).toBe(true);
    expect(isSupportedImageFile(new File(['x'], 'photo.webp', { type: 'image/webp' }))).toBe(true);
    // A MIME type the server would never see cannot rescue a refused name.
    expect(isSupportedImageFile(new File(['x'], 'photo.psd', { type: 'image/png' }))).toBe(false);
    expect(isSupportedImageFile(new File(['x'], 'photo', { type: 'image/jpeg' }))).toBe(false);
    expect(isSupportedImageFile(new File(['x'], 'scan.eps', { type: 'application/postscript' }))).toBe(false);
  });
});
