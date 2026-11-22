import exifr from 'exifr';

/*
  The upload grid's thumbnails, at the size the grid draws them.

  Every card used to be an `<img>` of the original file -- 24 MP decoded to a
  ~96 MB bitmap to fill a 120 px tile, a hundred times over on a phone. Now:

  1. `createImageBitmap(file, {resizeWidth})`, which decodes once, keeps only
     the small copy, and honours EXIF orientation.
  2. The JPEG's embedded EXIF thumbnail, where (1) is unavailable -- no decode
     of the main image at all.
  3. The file itself, as the last resort it always was.

  HEIC gets no thumbnail here (the caller shows a placeholder tile); decoding
  one costs a full-resolution `heic2any` pass, which is spent only when a
  visitor opens that image in the lightbox.
*/
export const THUMBNAIL_WIDTH = 256;

async function bitmapToBlob(bitmap) {
  if (typeof OffscreenCanvas === 'function') {
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    const context = canvas.getContext('2d');
    if (!context) return null;
    context.drawImage(bitmap, 0, 0);
    return canvas.convertToBlob({ type: 'image/jpeg', quality: 0.8 });
  }
  const canvas = document.createElement('canvas');
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const context = canvas.getContext?.('2d');
  if (!context) return null;
  context.drawImage(bitmap, 0, 0);
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.8));
}

/**
 * An object URL for the grid, or `null` where the caller should draw a
 * placeholder. The caller owns the URL and must revoke it.
 */
export async function makePreviewUrl(file, { isVideo = false, isHeic = false } = {}) {
  if (!file) return null;
  // A `<video>` needs the file itself; it decodes only the frames it shows.
  if (isVideo) return URL.createObjectURL(file);
  if (isHeic) return null;

  if (typeof createImageBitmap === 'function') {
    try {
      const bitmap = await createImageBitmap(file, {
        resizeWidth: THUMBNAIL_WIDTH,
        resizeQuality: 'medium',
      });
      try {
        const blob = await bitmapToBlob(bitmap);
        if (blob) return URL.createObjectURL(blob);
      } finally {
        bitmap.close?.();
      }
    } catch {
      // Not decodable this way (TIFF, an old Safari ignoring the options):
      // try the next source.
    }
  }

  try {
    const thumb = await exifr.thumbnail(file);
    if (thumb && thumb.byteLength > 0) {
      return URL.createObjectURL(new Blob([thumb], { type: 'image/jpeg' }));
    }
  } catch {
    // No embedded thumbnail.
  }

  return URL.createObjectURL(file);
}
