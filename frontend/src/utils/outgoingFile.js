import { MAX_IMAGE_UPLOAD_BYTES } from './imageValidation';

/**
 * The bytes that will actually be sent for one selected file.
 *
 * With full resolution on, the original -- unless the original is over the
 * per-file limit, in which case the compressed copy made for exactly that case
 * goes instead. Sending the original there was the bypass: the per-file check
 * ran on the compressed copy, the upload sent the original, and the server
 * refused a file the dialog had marked ready.
 */
export function outgoingFile(item, allowFullResolution) {
  const original = item.originalFile || item.file;
  if (allowFullResolution && original.size <= MAX_IMAGE_UPLOAD_BYTES) return original;
  return item.file;
}
