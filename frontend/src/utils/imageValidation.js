import exifr, { fileParsers } from 'exifr';

// Dynamically register extra HEIF brands in exifr to support all HEIF variations (.heif, .heics, .heifs, mif1, msf1, etc.)
if (fileParsers && fileParsers.has('heic')) {
  const HeicParser = fileParsers.get('heic');
  const HeifParserBase = Object.getPrototypeOf(HeicParser);
  if (HeifParserBase) {
    const brands = ['heif', 'heifs', 'heics', 'mif1', 'msf1', 'hevc', 'hevx'];
    brands.forEach(brand => {
      if (!fileParsers.has(brand)) {
        class CustomParser extends HeifParserBase {}
        CustomParser.type = brand;
        fileParsers.set(brand, CustomParser);
      }
    });
  }
}

/*
  Two different limits, because they answer two different questions.

  `MAX_UPLOAD_SIZE_BYTES` is now the *video* limit only. Images have their own,
  far higher one (`MAX_IMAGE_UPLOAD_BYTES` below): `ContributeModal` sends
  photographs at full resolution and compresses only one that would exceed it.
  (Historically every image was compressed to a 1.5 MB target.) It used to be
  checked here, against the file the visitor
  picked, *before* HEIC conversion and before compression. A 15 MB frame off a
  DSLR was refused outright while a 9 MB one was accepted and then shrunk to
  1.5 MB: the gate rejected exactly the high-resolution originals photogrammetry
  wants most, on the grounds of a size the upload never sees.

  `MAX_SOURCE_SIZE_BYTES` is what this browser will attempt to decode and
  compress at all, which is a real constraint -- an enormous file is a tab that
  runs out of memory, not a slow upload.
*/
export const MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024;

/*
  The per-image ceiling, and it is deliberately far above the video one.

  Full resolution is the default now (`allowFullResolution` starts on), so this
  number is no longer a limit on a compressed derivative -- it is the limit on
  the photograph itself. At 10 MB it would have refused exactly the files
  photogrammetry wants most: the 97 images of the Marcus Aurelius statue are
  24.5 MP and 5-6 MB each straight off an iPhone, and a 45 MP DSLR frame is
  routinely 20-30 MB.

  Nothing downstream needs a smaller number. `prepare_upload` has no per-file
  cap, Django's DATA_UPLOAD_MAX_MEMORY_SIZE is 1 GB, and nginx allows 1 GB on
  `/project/tirtha/`. 64 MB admits every camera JPEG and HEIC in circulation
  while still keeping one pathological file from filling the request on its own.
*/
export const MAX_IMAGE_UPLOAD_BYTES = 64 * 1024 * 1024;

/*
  The whole request, because every file goes in one POST.

  nginx caps `/project/tirtha/` at 1 GB and answers past it with its own HTML
  413, which axios surfaces as a bare "upload failed" -- no count, no size, no
  hint that splitting the batch would work. At full resolution this is easy to
  reach: 97 photographs were already 521 MB. Checking it here buys a message
  that says what to do.

  900 MB rather than 1 GB leaves room for multipart boundaries and headers.
*/
export const MAX_REQUEST_UPLOAD_BYTES = 900 * 1024 * 1024;

const MAX_SOURCE_SIZE_BYTES = 200 * 1024 * 1024;

/*
  Formats this browser can be expected to draw into a canvas.

  TIFF is not among them -- no mainstream browser decodes it in an `<img>` --
  and neither is HEIC. Both were on the accepted list, so a TIFF passed the type
  check, failed the dimension probe, and was refused with "Unable to read image
  dimensions. Please try another file." after being told TIFF was accepted. The
  server reads both perfectly well; only the preview cannot.
*/
const BROWSER_DECODABLE_TYPES = new Set([
  'image/jpeg',
  'image/png',
  'image/webp',
  'image/gif',
  'image/bmp',
]);
const BROWSER_DECODABLE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'];

function isBrowserDecodable(file) {
  const fileType = (file.type || '').toLowerCase();
  if (BROWSER_DECODABLE_TYPES.has(fileType)) return true;
  if (fileType) return false;
  const name = file.name || '';
  return BROWSER_DECODABLE_EXTENSIONS.includes(name.slice(name.lastIndexOf('.')).toLowerCase());
}
const MIN_IMAGE_DIMENSION = 640;
const WARNING_IMAGE_DIMENSION = 1080;

/*
  Exactly the names the server accepts: `ACCEPTED_SUFFIXES` in
  `tirtha_bk/tirtha/uploads.py`. A backend test parses this array and a vitest
  pins it, so the two cannot drift again.

  Decided by the name alone, as the server decides. This used to accept a file
  on its MIME type too, so a photograph the browser called `image/webp` but
  whose name the server refused was marked Ready here and then silently
  dropped on upload with nothing but a server log line to show for it. The
  content need not match the name: the server decodes within a safe raster set
  and converts anything that is not already a JPEG or PNG.

  `.heics`/`.heifs` are HEIF sequences; the server converts their primary still.
*/
export const VALID_IMAGE_EXTENSIONS = Object.freeze([
  '.bmp',
  '.gif',
  '.heic',
  '.heics',
  '.heif',
  '.heifs',
  '.jpeg',
  '.jpg',
  '.png',
  '.tif',
  '.tiff',
  '.webp',
]);

export function isSupportedImageFile(file) {
  if (!file) return false;
  const fileName = file.name || '';
  const dot = fileName.lastIndexOf('.');
  if (dot < 0) return false;
  return VALID_IMAGE_EXTENSIONS.includes(fileName.slice(dot).toLowerCase());
}

const VALID_VIDEO_EXTENSIONS = ['.mp4', '.mov'];
const SUPPORTED_VIDEO_TYPES = new Set(['video/mp4', 'video/quicktime']);

export function isSupportedVideoFile(file) {
  if (!file) return false;
  const fileType = (file.type || '').toLowerCase();
  if (SUPPORTED_VIDEO_TYPES.has(fileType)) {
    return true;
  }

  const fileName = file.name || '';
  const extension = fileName.slice(fileName.lastIndexOf('.')).toLowerCase();
  return VALID_VIDEO_EXTENSIONS.includes(extension);
}

export function getFileExtension(file) {
  const fileName = file.name || '';
  return fileName.slice(fileName.lastIndexOf('.')).toLowerCase();
}

/*
  Decode far enough to learn the dimensions, and give up if it does not happen.

  There was no timeout and no third outcome: if the decoder fired neither `load`
  nor `error` -- which a browser under memory pressure does, and which is the
  normal outcome for a format it does not support -- the promise never settled,
  the object URL was never revoked, and the file sat "checking" forever with
  nothing to click.
*/
const DECODE_TIMEOUT_MS = 10_000;

function loadImageSize(file, timeoutMs = DECODE_TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    let timer = null;

    const settle = (fn, value) => {
      if (timer !== null) clearTimeout(timer);
      timer = null;
      image.onload = null;
      image.onerror = null;
      URL.revokeObjectURL(url);
      fn(value);
    };

    image.onload = () => settle(resolve, { width: image.width, height: image.height });
    image.onerror = (error) => settle(reject, error);
    timer = setTimeout(() => settle(reject, new Error('Timed out decoding the image')), timeoutMs);
    image.src = url;
  });
}

/*
  A HEIC's pixel size, from its EXIF header rather than from decoding it.
  `null` when the header does not say, which the caller treats as "cannot
  check the size here" -- a warning, never a refusal.
*/
function heicSizeFromExif(exif) {
  if (!exif) return null;
  const width = Number(exif.ExifImageWidth ?? exif.ImageWidth ?? exif.PixelXDimension);
  const height = Number(exif.ExifImageHeight ?? exif.ImageHeight ?? exif.PixelYDimension);
  if (!(width > 0 && height > 0)) return null;
  return { width, height };
}

const HEIC_PREVIEW_TIMEOUT_MS = 30_000;

/**
 * One HEIC, decoded for the lightbox -- on demand, one at a time.
 *
 * `heic2any` has no cancellation API, so a timeout cannot stop the decode it
 * gave up on; it can only stop waiting for it. The late result, if one ever
 * arrives, is dropped rather than handed to a lightbox that has moved on.
 */
export async function convertHeicForPreview(file, timeoutMs = HEIC_PREVIEW_TIMEOUT_MS) {
  const module = await import('heic2any');
  const heic2any = module.default || module;
  let timer = null;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('HEIC conversion timed out')), timeoutMs);
  });
  try {
    let blob = await Promise.race([
      heic2any({ blob: file, toType: 'image/jpeg', quality: 0.8 }),
      timeout,
    ]);
    if (Array.isArray(blob)) blob = blob[0];
    if (!blob) throw new Error('No converted blob returned');
    return blob;
  } finally {
    clearTimeout(timer);
  }
}

/*
  Returns a translation *key*, not a sentence.

  Every one of these reasons used to be an English string built here and
  rendered straight into the interface, so a contributor reading the site in
  Odia or Hindi was told in English why their photograph had been refused --
  on the one screen where the explanation is the entire point. A validator has
  no business knowing the display language; it names the reason and the
  component says it.
*/
export async function validateImageFile(file, options = {}) {
  const {
    minDimension = MIN_IMAGE_DIMENSION,
    warningDimension = WARNING_IMAGE_DIMENSION,
    // Exposed so the tests can drive the give-up path without waiting out the
    // real one.
    decodeTimeoutMs = DECODE_TIMEOUT_MS,
  } = options;

  if (!file) {
    return {
      valid: false,
      severity: 'invalid',
      reasonKey: 'validation.noFile',
    };
  }

  if (isSupportedVideoFile(file)) {
    // Video is not compressed in the browser, so for video the two limits are
    // the same one.
    if (file.size > MAX_UPLOAD_SIZE_BYTES) {
      return {
        valid: false,
        severity: 'invalid',
        reasonKey: 'validation.videoTooLarge',
      };
    }
    return {
      valid: true,
      severity: 'valid',
      reasonKey: 'validation.ready',
      fileType: 'video',
    };
  }

  if (!isSupportedImageFile(file)) {
    return {
      valid: false,
      severity: 'invalid',
      reasonKey: 'validation.unsupportedType',
    };
  }

  if (file.size > MAX_SOURCE_SIZE_BYTES) {
    return {
      valid: false,
      severity: 'invalid',
      reasonKey: 'validation.sourceTooLarge',
    };
  }

  const extension = getFileExtension(file);
  const fileType = (file.type || '').toLowerCase();
  const isHeic =
    extension === '.heic' ||
    extension === '.heif' ||
    extension === '.heics' ||
    extension === '.heifs' ||
    fileType === 'image/heic' ||
    fileType === 'image/heif' ||
    fileType === 'image/heic-sequence' ||
    fileType === 'image/heif-sequence';

  /*
    HEIC is no longer decoded here.

    This ran every HEIC through `heic2any` at full resolution, on the main
    thread, to learn its dimensions and to have something to preview -- so a
    hundred iPhone photographs meant a hundred 12-48 MP decodes in a row, which
    is how a phone tab runs out of memory. The dimensions come from the EXIF
    header below instead, the grid shows a placeholder tile, and the lightbox
    decodes the one image a visitor actually opens (`convertHeicForPreview`).
    The server converts HEIC itself, at quality 100 with the EXIF kept, so
    nothing that is uploaded depends on a browser-side conversion.
  */
  const convertedFile = null;

  /*
    One pass over the header.

    This used to call a hand-written `checkExif` that read the *entire* file
    into a `Uint8Array` and scanned it byte by byte for the `Exif\0\0` marker,
    and then -- if it found one -- handed the same file to `exifr`, which reads
    the header itself. Every image was therefore read twice and one of those
    reads pulled the whole thing into memory, for an answer `exifr` already
    gives. On a batch of forty photographs that is forty full-file reads spent
    deciding whether to do the work that finds the answer anyway.
  */
  let exifData = null;
  try {
    exifData = await exifr.parse(file);
  } catch {
    /*
      Unreadable metadata is not an unusable photograph, and this used to reject
      the file outright with "Corrupted EXIF metadata". EXIF is not required by
      anything downstream -- it is read to show the visitor the camera and date,
      and the reconstruction does not consult it. A photograph whose header
      confuses `exifr` still reconstructs perfectly well, so it falls through to
      the "missing EXIF" warning below like any other image without one.
    */
  }
  const hasExif = isHeic || Boolean(exifData && Object.keys(exifData).length > 0);

  let size;
  try {
    if (isHeic) {
      size = heicSizeFromExif(exifData);
    } else {
      size = await loadImageSize(file, decodeTimeoutMs);
    }
  } catch {
    /*
      A format this browser cannot draw is not a broken file. TIFF is the case
      that matters -- accepted by the type check, undecodable by `<img>`, and so
      rejected with a message telling the visitor to try another file. It is
      passed through with a warning instead, like HEIC, and the dimension check
      simply does not run for it.
    */
    if (!isBrowserDecodable(file)) {
      return {
        valid: true,
        severity: 'warning',
        reasonKey: 'validation.previewUnavailable',
        exif: exifData,
        convertedFile,
      };
    }
    return {
      valid: false,
      severity: 'invalid',
      reasonKey: 'validation.undecodable',
    };
  }

  if (size) {
    const shortSide = Math.min(size.width, size.height);
    if (shortSide < minDimension) {
      return {
        valid: false,
        severity: 'invalid',
        reasonKey: 'validation.tooSmall',
        reasonParams: { width: size.width, height: size.height, minimum: minDimension },
      };
    }

    if (!hasExif) {
      return {
        valid: true,
        severity: 'warning',
        reasonKey: 'validation.noExif',
        exif: null,
        convertedFile,
      };
    }

    if (shortSide < warningDimension) {
      return {
        valid: true,
        severity: 'warning',
        reasonKey: 'validation.lowResolution',
        reasonParams: { width: size.width, height: size.height, minimum: warningDimension },
        exif: exifData,
        convertedFile,
      };
    }
  } else if (isHeic) {
    return {
      valid: true,
      severity: 'warning',
      reasonKey: 'validation.heicPreviewUnavailable',
      exif: exifData,
      convertedFile: null,
    };
  }

  return {
    valid: true,
    severity: 'valid',
    reasonKey: 'validation.ready',
    exif: exifData,
    convertedFile,
  };
}

