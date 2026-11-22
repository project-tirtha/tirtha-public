import React, { useCallback, useEffect, useRef, useState } from 'react';
import Icon from './Icon';
import { useTranslation } from 'react-i18next';
import axios from 'axios';
import imageCompression from 'browser-image-compression';
import FileUploadBox from './FileUploadBox';
import Snackbar from './Snackbar';
import { useModalBehaviour } from '../hooks/useModalBehaviour';
import { csrfHeaders, getCsrfToken } from '../utils/csrf';
import {
  fetchSession,
  signOut,
  startSignIn,
  uploadState,
} from '../utils/session';
import {
  convertHeicForPreview,
  MAX_IMAGE_UPLOAD_BYTES,
  validateImageFile,
} from '../utils/imageValidation';
import { makePreviewUrl } from '../utils/thumbnail';
import { outgoingFile } from '../utils/outgoingFile';
import {
  batchBytes,
  beaconDiscard,
  discardUpload,
  MAX_UPLOAD_BYTES,
  MAX_UPLOAD_FILES,
  REFUSAL_MESSAGES,
  RETRY_DELAYS_MS,
  runBatchedUpload,
  splitIntoBatches,
} from '../utils/batchUpload';
import { useAnnouncement } from '../utils/announce';
import '../styles/ContributeModal.css';
import { deploymentPrefix } from '../utils/deployment';
import { formatBytes as formatBytesIn } from '../utils/format';

const HEIC_NAME = /\.(heic|heif|heics|heifs)$/i;

function isHeicItem(item) {
  return HEIC_NAME.test(item?.originalFile?.name || item?.file?.name || '');
}

function isAbort(error) {
  return (
    error?.name === 'CanceledError' ||
    error?.name === 'AbortError' ||
    error?.code === 'ERR_CANCELED'
  );
}

/**
 * `siteSlug` is the site's `verbose_id`, which is what the upload endpoints
 * identify a site by (`mesh_vid`, internal design notes). It replaces `siteId`, a
 * ShortUUID those endpoints do not look anything up by -- so every upload from
 * this modal answered "Site not found."
 */
function ContributeModal({
  isOpen,
  onClose,
  targetName = 'Tirtha',
  siteName = null,
  siteSlug = null,
  // Injectable so tests need not wait out the real backoff.
  retryDelays = RETRY_DELAYS_MS,
}) {
  const { t, i18n } = useTranslation();
  const toastSeq = useRef(0);
  const panelRef = useRef(null);
  const titleId = 'contribute-modal-title';
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [authMessage, setAuthMessage] = useState('');
  const [termsAccepted, setTermsAccepted] = useState(false);
  /*
    On by default.

    The server has defaulted to full resolution since internal design notes --
    `prepare_upload(..., compress=False)` -- but this box started unticked, and
    an unticked box sends `compress=true`. So the default contribution was
    re-encoded server-side regardless of what the backend preferred, and the
    only way to get an untouched photograph in was to know to tick a box below
    the fold.

    Ticked, this sends no `compress` parameter and uploads `originalFile`, so
    the bytes on disk are the bytes the camera wrote.
  */
  const [allowFullResolution, setAllowFullResolution] = useState(true);
  const [error, setError] = useState('');
  const [toastItems, setToastItems] = useState([]);
  const [isUploadBusy, setIsUploadBusy] = useState(false);
  const [isValidationBusy, setIsValidationBusy] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadStatus, setUploadStatus] = useState('');
  const [session, setSession] = useState(null);
  const [isAuthLoading, setIsAuthLoading] = useState(false);
  const [previewFilter, setPreviewFilter] = useState('all');
  const [selectedPreviewImage, setSelectedPreviewImage] = useState(null);
  /*
    The lightbox shows the full image; the grid shows 256 px thumbnails. The
    full-size object URL exists only while the lightbox is open, one at a time,
    and is revoked when it changes or closes.
  */
  const [lightboxUrl, setLightboxUrl] = useState(null);
  const previewIdRef = useRef(null);
  useEffect(
    () => () => {
      if (lightboxUrl) URL.revokeObjectURL(lightboxUrl);
    },
    [lightboxUrl],
  );
  /*
    Bytes sent, waiting for the server's answer. Storing the images, reading
    their EXIF and queueing the checks takes a while for a large batch, and the
    bar used to sit at 100% with nothing to say the upload had not hung.
  */
  const [isProcessing, setIsProcessing] = useState(false);
  // A failed or cancelled upload keeps its selection and offers this.
  const [canRetry, setCanRetry] = useState(false);
  const uploadControllerRef = useRef(null);
  /*
    Where a failed batched upload stands: its planned batches, the first one
    the server has not confirmed, and the open contribution they go into.
    Retry continues from here; null means the next upload starts afresh.
  */
  const resumeRef = useRef(null);
  /*
    A different selection, or a different compression choice, is a different
    upload: Retry must not send the old plan. The open contribution is *not*
    discarded here -- only Cancel does that. Its photographs are kept, and a
    new upload sharing files with it resumes into it by their keys; if none
    does, the server's sweep finalizes it.

    Ignored while an upload is running, so a late validation update cannot
    pull the plan out from under it.
  */
  const busyRef = useRef(false);
  useEffect(() => {
    busyRef.current = isUploadBusy;
  }, [isUploadBusy]);
  /*
    Set synchronously as `handleUpload` starts, so a double tap cannot start a
    second upload in the gap before `isUploadBusy` re-renders the button.
  */
  const uploadRunningRef = useRef(false);
  /*
    The batch on the wire and the open upload it goes into, for a discard if
    the page goes away mid-upload; and whether only the finish is left, after
    which nothing is discarded.
  */
  const openUploadRef = useRef(null);
  const finishingRef = useRef(false);
  // Sending progress within the current batch, shown under the overall bar.
  const [batchProgress, setBatchProgress] = useState(null);
  useEffect(() => {
    if (!busyRef.current) resumeRef.current = null;
  }, [selectedFiles, allowFullResolution]);

  const closePreview = () => {
    previewIdRef.current = null;
    setSelectedPreviewImage(null);
    setLightboxUrl(null);
  };

  const openPreview = (item) => {
    previewIdRef.current = item.id;
    setSelectedPreviewImage(item);
    setLightboxUrl(null);
    if (item.validation?.fileType === 'video') return;
    if (isHeicItem(item)) {
      // Decoded now, for this one image, rather than for every HEIC at
      // selection time. Dropped if the visitor has moved on by the time it
      // finishes.
      convertHeicForPreview(item.originalFile || item.file)
        .then((blob) => {
          if (previewIdRef.current !== item.id) return;
          setLightboxUrl(URL.createObjectURL(blob));
        })
        .catch(() => {
          // The placeholder stays; the server converts HEIC itself.
        });
      return;
    }
    setLightboxUrl(URL.createObjectURL(item.originalFile || item.file));
  };

  /*
    Leaving the page mid-upload abandons it, and nothing said so: a tap on the
    wrong link threw away a 500 MB transfer. The browser shows its own generic
    prompt -- the text cannot be customised any more -- but it does ask.
  */
  useEffect(() => {
    if (!isUploadBusy) return undefined;
    const onBeforeUnload = (event) => {
      event.preventDefault();
      // Still required by some browsers to show the prompt at all.
      event.returnValue = '';
    };
    /*
      And if they leave anyway, the open upload is discarded, the same as a
      Cancel -- by beacon, since nothing else outlives the page. Not once the
      finish has started: by then the upload is the contributor's, complete.
      If the beacon is lost, the server's sweep still deletes an upload whose
      first batch was never acknowledged, and finalizes any other.
    */
    const onPageHide = () => {
      if (finishingRef.current || !openUploadRef.current) return;
      beaconDiscard({ ...openUploadRef.current, siteSlug }, getCsrfToken());
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    window.addEventListener('pagehide', onPageHide);
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
      window.removeEventListener('pagehide', onPageHide);
    };
  }, [isUploadBusy, siteSlug]);

  // Thumbnails still on screen when the dialog itself goes away.
  const selectedFilesRef = useRef(selectedFiles);
  useEffect(() => {
    selectedFilesRef.current = selectedFiles;
  }, [selectedFiles]);
  useEffect(
    () => () => {
      selectedFilesRef.current.forEach((item) => {
        if (item?.previewUrl) URL.revokeObjectURL(item.previewUrl);
      });
      uploadControllerRef.current?.abort();
    },
    [],
  );

  /*
    Escape, the focus trap, and returning focus to whatever opened this
    (WCAG 2.1.2, 2.4.3). The hand-written Escape handler that used to live here
    was a quarter of that: focus stayed on the page underneath when the modal
    opened, Tab walked straight out of it into the map behind, and closing left
    the tab position wherever it had wandered to.

    `role="dialog" aria-modal="true"` below is a promise those hold, which is
    why they are not separable.
  */
  /*
    Escape closes the innermost thing that is open.

    It was wired straight to `onClose`, so pressing it with the image preview
    open closed the *whole dialog* -- discarding every selected file, its EXIF
    read, and its compression. The one key a keyboard user reaches for to back
    out of a preview threw away all the work.

    Routed through a ref so the callback identity stays stable: passing a new
    function whenever the preview opens would re-run the focus trap and pull
    focus back to the first control.
  */
  const escapeRef = useRef(null);
  useEffect(() => {
    escapeRef.current = () => {
      if (selectedPreviewImage) {
        closePreview();
        return;
      }
      /*
        Not while the upload is in flight. The POST does not stop when the
        dialog does, so dismissing it here left the request running with nobody
        to hear the answer: the contributor's photographs may have arrived, or
        may have failed, and they were never told which. The one confirmation
        they will ever get was being thrown away by the key people press to
        dismiss things.
      */
      if (isUploadBusy) return;
      onClose?.();
    };
  }, [selectedPreviewImage, onClose, isUploadBusy]);
  const handleEscape = useCallback(() => escapeRef.current?.(), []);
  useModalBehaviour(isOpen, handleEscape, panelRef);

  /*
    The preview is a second dialog, on top of this one, and owes the same four
    things.

    It covers the whole form with an 80%-opaque scrim, and nothing used to move
    focus into it: focus stayed on the thumbnail that opened it, so Tab walked
    on through the terms checkbox and the upload button *behind* the scrim,
    where the focus ring cannot be seen and Enter removes a file the visitor
    cannot look at (WCAG 2.4.11). A screen reader was told nothing had happened
    at all, because the preview declared no role and took no focus (4.1.2).

    `useModalBehaviour` cannot do this one: it keys off `isOpen` and queries
    `panelRef`, which is the form underneath. Tab is caught in the capture phase
    so it never reaches that trap, whose idea of "the last control" is the last
    control of the dialog behind.
  */
  const lightboxRef = useRef(null);
  useEffect(() => {
    if (!selectedPreviewImage) return undefined;

    const opener = document.activeElement;
    /*
      The same selector `useModalBehaviour` uses, plus `video`.

      A narrower `'button, [href]'` here was worse than no trap: the preview
      renders `<video controls>` for a video file, which is natively focusable,
      and because this handler preventDefaults *every* Tab rather than only at
      the boundaries, Tab cycled between the close button and itself and could
      never reach the player. Before the trap existed, native Tab still got
      there. A focus trap that excludes a control traps the user away from it.
    */
    const focusable = () =>
      Array.from(
        lightboxRef.current?.querySelectorAll(
          'button, [href], input, select, textarea, video, [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      );

    focusable()[0]?.focus();

    const onKeyDown = (event) => {
      if (event.key !== 'Tab') return;
      const items = focusable();
      if (items.length === 0) return;
      const index = items.indexOf(document.activeElement);
      const next = event.shiftKey
        ? items[(index <= 0 ? items.length : index) - 1]
        : items[(index + 1) % items.length];
      event.preventDefault();
      event.stopPropagation();
      next.focus();
    };

    window.addEventListener('keydown', onKeyDown, true);
    return () => {
      window.removeEventListener('keydown', onKeyDown, true);
      // Not when the whole dialog is closing: the thumbnail has gone with it,
      // and `useModalBehaviour` is already returning focus to whatever opened
      // the dialog.
      if (opener instanceof HTMLElement && document.body.contains(opener)) {
        opener.focus();
      }
    };
  }, [selectedPreviewImage]);

  // The page behind must not scroll while a modal covers it -- on a phone,
  // scrolling the upload form scrolled the map instead.
  useEffect(() => {
    if (!isOpen) return undefined;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  /*
    Who is signed in. Was `/api/auth/current-contributor/`, which is not a route
    on this backend, inside a `catch {}` that swallowed the 404 -- so the modal
    silently believed nobody was ever signed in, and the upload button stayed
    disabled for every approved contributor.
  */
  useEffect(() => {
    if (!isOpen) return undefined;

    const controller = new AbortController();

    fetchSession(controller.signal)
      .then((data) => setSession(data))
      .catch((err) => {
        if (err?.name === 'AbortError') return;
        setSession({ authenticated: false, contributor: null });
        setAuthMessage(t('contribute.authUnavailable'));
      })
    return () => controller.abort();
  }, [isOpen, t]);

  // `null` means "not asked yet". Keeping that distinct from
  // `{authenticated: false}` is what lets the header show a spinner instead of
  // flashing a sign-in button at someone who is already signed in.
  const isSessionLoading = session === null;

  const handleSignOut = async () => {
    setIsAuthLoading(true);
    try {
      await signOut();
      setSession({ authenticated: false, contributor: null });
      addToast(t('contribute.signedOut'), 'success');
    } catch {
      addToast(t('contribute.signOutFailed'), 'error');
    } finally {
      setIsAuthLoading(false);
    }
  };

  const handleAuthRequired = () => {
    setAuthMessage(t('contribute.signInToUpload'));
  };

  const revokePreviewUrls = (items) => {
    items.forEach((item) => {
      if (item?.previewUrl) {
        URL.revokeObjectURL(item.previewUrl);
      }
    });
  };

  const handleClear = () => {
    revokePreviewUrls(selectedFiles);
    setSelectedFiles([]);
    setPreviewFilter('all');
    closePreview();
    setUploadProgress(0);
    setUploadStatus('');
    setCanRetry(false);
    setIsProcessing(false);
    setAllowFullResolution(false);
    setTermsAccepted(false);
    setError('');
  };

  const handleModalClose = () => {
    // Same reason as the Escape guard above: the upload outlives this dialog.
    if (isUploadBusy) return;
    handleClear();
    onClose();
  };

  /*
    Clear the form when the modal closes, so reopening it does not present
    somebody else's half-finished selection -- and, more to the point, so the
    accepted-terms checkbox is not still ticked from last time.

    During render, not in an effect: React runs this before painting, so the
    stale state never reaches the screen.
  */
  const [wasOpen, setWasOpen] = useState(isOpen);
  if (wasOpen !== isOpen) {
    setWasOpen(isOpen);
    if (!isOpen) {
      revokePreviewUrls(selectedFiles);
      setSelectedFiles([]);
      setPreviewFilter('all');
      setSelectedPreviewImage(null);
      setLightboxUrl(null);
      setCanRetry(false);
      setAllowFullResolution(false);
      setTermsAccepted(false);
      setError('');
    }
  }

  const handleRemoveFile = (fileId) => {
    setSelectedFiles((current) => {
      const updated = current.filter((item) => {
        if (item.id === fileId) {
          if (item.previewUrl) {
            URL.revokeObjectURL(item.previewUrl);
          }
          return false;
        }
        return true;
      });
      return updated;
    });
  };



  const addToast = (message, variant = 'info', duration = 5000) => {
    // A monotonic counter rather than `Date.now()`-plus-random: two toasts
    // raised in the same millisecond could collide on the key, and React would
    // reuse one row's DOM for the other's message.
    toastSeq.current += 1;
    const id = `toast-${toastSeq.current}`;
    setToastItems((current) => [...current, { id, message, variant }]);
    // If duration is provided (number), auto-dismiss; otherwise persist until user click
    if (typeof duration === 'number' && duration > 0) {
      window.setTimeout(() => {
        setToastItems((current) => current.filter((item) => item.id !== id));
      }, duration);
    }
  };

  const dismissToast = (id) => {
    setToastItems((current) => current.filter((item) => item.id !== id));
  };

  // Dismiss all toasts when user clicks anywhere in the document (per UX request)
  useEffect(() => {
    if (!isOpen) return undefined;
    const handleGlobalClick = () => {
      if (toastItems.length > 0) setToastItems([]);
    };
    document.addEventListener('click', handleGlobalClick);
    return () => document.removeEventListener('click', handleGlobalClick);
  }, [isOpen, toastItems]);

  const handleRawFiles = async (files) => {
    if (!files || files.length === 0) {
      return;
    }

    setIsValidationBusy(true);
    try {
      const nextFiles = [];
      const existingKeys = new Set(selectedFiles.map((item) => item.id));
      let addedCount = 0;

      for (const file of Array.from(files)) {
        const fileKey = `${file.name}-${file.size}-${file.lastModified}`;
        if (existingKeys.has(fileKey)) {
          addToast(t('contribute.alreadyAdded', { name: file.name }), 'info', 3000);
          continue;
        }

        let validation = await validateImageFile(file, { minDimension: 640, warningDimension: 1080 });
        const displayFile = validation.convertedFile || file;
        /*
          A 256 px thumbnail, not the original: see `utils/thumbnail.js`. One
          file at a time, because this loop awaits each, so a hundred
          photographs never decode at once.
        */
        const previewUrl = await makePreviewUrl(displayFile, {
          isVideo: validation.fileType === 'video',
          isHeic: HEIC_NAME.test(file.name) && !validation.convertedFile,
        });

        const exif = validation.exif;
        const parseExifDate = (val) => {
          if (!val) return null;
          if (val instanceof Date) return val.toLocaleString();
          try {
            const d = new Date(String(val).replace(/^(\d{4}):(\d{2}):(\d{2})/, '$1-$2-$3'));
            return isNaN(d.getTime()) ? String(val) : d.toLocaleString();
          } catch {
            return String(val);
          }
        };

        const convertDMSToDecimal = (dms, ref) => {
          if (!dms || dms.length < 3) return null;
          const parseVal = (val) => {
            if (typeof val === 'number') return val;
            if (val && typeof val === 'object' && val.numerator !== undefined) {
              return val.numerator / (val.denominator || 1);
            }
            return parseFloat(val) || 0;
          };
          const degrees = parseVal(dms[0]);
          const minutes = parseVal(dms[1]);
          const seconds = parseVal(dms[2]);
          let decimal = degrees + minutes / 60.0 + seconds / 3600.0;
          if (ref === 'S' || ref === 'W') {
            decimal = -decimal;
          }
          return decimal;
        };

        let latitude = exif?.latitude !== undefined && exif?.latitude !== null ? exif.latitude : null;
        let longitude = exif?.longitude !== undefined && exif?.longitude !== null ? exif.longitude : null;

        if (exif && latitude === null && exif.GPSLatitude && exif.GPSLatitudeRef) {
          latitude = convertDMSToDecimal(exif.GPSLatitude, exif.GPSLatitudeRef);
        }
        if (exif && longitude === null && exif.GPSLongitude && exif.GPSLongitudeRef) {
          longitude = convertDMSToDecimal(exif.GPSLongitude, exif.GPSLongitudeRef);
        }

        let altitude = null;
        if (exif && exif.GPSAltitude !== undefined && exif.GPSAltitude !== null) {
          if (typeof exif.GPSAltitude === 'number') {
            altitude = exif.GPSAltitude;
          } else if (Array.isArray(exif.GPSAltitude)) {
            altitude = exif.GPSAltitude[0] / (exif.GPSAltitude[1] || 1);
          } else if (typeof exif.GPSAltitude === 'object' && exif.GPSAltitude.numerator !== undefined) {
            altitude = exif.GPSAltitude.numerator / (exif.GPSAltitude.denominator || 1);
          } else {
            altitude = parseFloat(exif.GPSAltitude);
          }
          if (altitude !== null && exif.GPSAltitudeRef === 1) {
            altitude = -altitude;
          }
        }

        const metadata = exif ? {
          cameraMake: exif.Make || null,
          cameraModel: exif.Model || null,
          dateTaken: parseExifDate(exif.DateTimeOriginal || exif.DateTime),
          focalLength: exif.FocalLength ? `${exif.FocalLength} mm` : null,
          gpsStatus: (latitude !== null && longitude !== null)
            ? t('contribute.gpsAvailable')
            : t('contribute.gpsUnavailable'),
          latitude: latitude,
          longitude: longitude,
          altitude: altitude,
        } : {
          cameraMake: null,
          cameraModel: null,
          dateTaken: null,
          focalLength: null,
          gpsStatus: t('contribute.gpsUnavailable'),
          latitude: null,
          longitude: null,
          altitude: null,
        };

        let compressedFile = null;
        let compressedSize = null;
        let isCompressed = false;

        /*
          Compression is a last resort now, not the default.

          Every image used to go through `maxSizeMB: 1.5, maxWidthOrHeight: 4096,
          quality 0.8`, which re-encodes through a canvas -- so a 24 MP, 6.5 MB
          photograph arrived as 12.6 MP and 1.4 MB with its EXIF gone. Both
          halves of that hurt:

          * `uploads.py` already says it -- "1.5 MB on a 12 MP photograph
            discards exactly the detail feature matching depends on". The
            backend was changed to keep full resolution by default; this was
            never changed to match, so it undid that before the bytes left the
            browser.
          * Canvas re-encoding drops EXIF, so `camera_make`, `camera_model` and
            `focal_length` were empty for all 19,394 images in the archive.
            COLMAP then has nothing to group cameras by and solves separate
            intrinsics per photograph -- 73 cameras for 73 images of one
            fountain taken on one phone, each calibrated from one view.

          `MAX_IMAGE_UPLOAD_BYTES` is 64 MB and a full-resolution photograph is
          5-30 MB, so the common case uploads untouched -- the 97 iPhone frames
          of the Marcus Aurelius statue are 24.5 MP at 5-6 MB each. Only a file
          that would actually be rejected is compressed, and then as gently as
          possible with its metadata kept.
        */
        /*
          Decided on the bytes that will actually be sent.

          This read `displayFile`, which for a HEIC is the preview JPEG that
          `heic2any` produced -- while `outgoing` below sends `file`, the HEIC
          itself. The two differ, so a 12 MB HEIC with an 8 MB preview was
          judged small enough, sent whole, and then refused by the size check
          below as too large: a file the user could do nothing about, rejected
          for a reason they were never shown.

          For a non-HEIC these are the same object, so nothing changes there.

          A HEIC that is genuinely too large still falls back to compressing the
          preview, which loses EXIF -- but that is the rare case now rather than
          the common one, because the server converts HEIC itself at quality 100
          and keeps the EXIF while doing it.
        */
        const needsCompression = file.size > MAX_IMAGE_UPLOAD_BYTES;

        if (
          needsCompression &&
          validation.severity !== 'invalid' &&
          validation.fileType !== 'video'
        ) {
          try {
            const options = {
              // Just under the limit, rather than a tenth of it.
              maxSizeMB: 9.5,
              // Above any current phone's long edge, so this only bites on
              // genuinely huge images rather than on every one.
              maxWidthOrHeight: 8192,
              useWebWorker: true,
              initialQuality: 0.92,
              // Without this the camera is unknowable downstream.
              preserveExif: true,
            };
            const compressedBlob = await imageCompression(displayFile, options);
            compressedFile = new File([compressedBlob], displayFile.name, {
              type: displayFile.type,
              // The original's timestamp, not now. A photograph's date is part
              // of what it records; stamping it with the compression time
              // destroys that for every image that goes through this path.
              lastModified: displayFile.lastModified,
            });
            compressedSize = compressedFile.size;
            isCompressed = compressedFile.size < file.size;
          } catch (err) {
            console.error('Compression failed, using original file:', err);
            compressedFile = file;
            compressedSize = file.size;
            isCompressed = false;
          }
        }

        /*
          The per-file limit, on the copy this item falls back to. Compression
          targets 9.5 MB (`maxSizeMB` above), so this only bites when
          compression failed and we fell back to the original -- which is
          exactly when it should. Which copy is finally *sent* depends on the
          full-resolution box, so `outgoingFile` makes that choice at upload
          time and never picks an original over this limit.
        */
        const outgoing = isCompressed ? compressedFile : file;
        if (validation.severity !== 'invalid' && outgoing.size > MAX_IMAGE_UPLOAD_BYTES) {
          validation = {
            ...validation,
            valid: false,
            severity: 'invalid',
            /*
              A key, like every other reason. This wrote a finished sentence to
              `reason`, and nothing reads `reason` any more -- the toast and the
              card both render `reasonKey`, which the spread above leaves as
              whatever the file passed with. So the one file rejected for its
              size was refused with the word "Ready" under an "Invalid" badge,
              and the sentence built here was never shown to anyone.

              The stale params go with it: they belong to the key being
              replaced, and this message interpolates nothing.
            */
            reasonKey: 'contribute.tooLargeAfterCompression',
            reasonParams: undefined,
          };
        }

        nextFiles.push({
          id: fileKey,
          file: outgoing,
          originalFile: file,
          compressedFile: compressedFile || file,
          previewUrl,
          validation,
          metadata,
          isCompressed,
          originalSize: file.size,
          compressedSize: compressedSize || file.size,
        });
        existingKeys.add(fileKey);
        addedCount += 1;

        if (validation.severity === 'invalid') {
          addToast(
            t('validation.ignored', {
              name: file.name,
              reason: t(validation.reasonKey, validation.reasonParams ?? {}),
            }),
            'warning',
            6000,
          );
        }
      }

      if (nextFiles.length > 0) {
        setSelectedFiles((current) => [...current, ...nextFiles]);
        /*
          Counted by i18next, not by a trailing "s". Odia and Hindi do not form
          a plural that way, so the English rule spliced into the template
          literal produced "2 files added to preview." inside an otherwise Odia
          dialog -- and would have been wrong in Odia even translated.
        */
        addToast(t('contribute.filesAdded', { count: addedCount }), 'info');
      }
    } catch (error) {
      console.error('Error during file handling/validation:', error);
      addToast(t('contribute.validationFailed'), 'error');
    } finally {
      setIsValidationBusy(false);
    }
  };

  /**
   * Ask the server whether this site is accepting contributions, before asking
   * anyone to wait through an upload (internal design notes).
   *
   * The path was `/api/contributions/upload-check/?site_id=`. The view is
   * `preUpload/`, it takes `mesh_vid` (the site's `verbose_id`, which the list
   * serializer returns as `slug`), and it answers with `allowupload` -- one
   * word, no underscore -- and `output`. Every one of those was different, so
   * the check 404'd, `allow_upload` was `undefined`, and the upload was refused
   * before it began with "Upload validation failed."
   */
  const checkPreUpload = async () => {
    if (!siteSlug) {
      return { allowUpload: false, message: t('contribute.noSite') };
    }

    try {
      const response = await fetch(
        `${deploymentPrefix()}/preUpload/?mesh_vid=${encodeURIComponent(siteSlug)}`,
        { credentials: 'same-origin', headers: { Accept: 'application/json' } },
      );
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        return { allowUpload: false, message: data?.output || t('contribute.checkFailed') };
      }
      return {
        allowUpload: data?.allowupload === true,
        // The server's own words. It is the only thing that knows *why* --
        // banned, not yet activated, site closed to contributions.
        message: data?.output || t('contribute.checkFailed'),
      };
    } catch {
      return { allowUpload: false, message: t('contribute.checkUnavailable') };
    }
  };


  const contributionTarget = siteName || targetName;
  const totalSelected = selectedFiles.length;
  const readyCount = selectedFiles.filter((item) => item.validation?.severity === 'valid').length;
  const warningCount = selectedFiles.filter((item) => item.validation?.severity === 'warning').length;
  const invalidCount = selectedFiles.filter((item) => item.validation?.severity === 'invalid').length;
  const uploadableFiles = selectedFiles.filter((item) => item.validation?.severity !== 'invalid');
  
  const totalOriginalSize = uploadableFiles.reduce((acc, item) => acc + (item.originalSize || item.file.size), 0);
  const totalCompressedSize = uploadableFiles.reduce((acc, item) => acc + (item.compressedSize || item.file.size), 0);
  const totalSavedPercent = totalOriginalSize > 0 ? Math.round(((totalOriginalSize - totalCompressedSize) / totalOriginalSize) * 100) : 0;
  const hasHeicFile = uploadableFiles.some(
    (item) =>
      (item.file?.name || '').match(/\.(heic|heif|heics|heifs)$/i) ||
      (item.originalFile?.name || '').match(/\.(heic|heif|heics|heifs)$/i)
  );

  // One formatter for every size in this dialog. There were two, and both were
  // on screen at once: a per-file size in Latin digits beside a total in the
  // reading language's own.
  const formatBytes = (bytes) => formatBytesIn(bytes, i18n.language);

  const hasVideo = uploadableFiles.some((item) => item.validation?.fileType === 'video');
  /*
    Was `Upload ${n} ${fileWord}${n > 1 ? 's' : ''}` -- English pluralisation
    hardcoded into a template literal, which no other language shares. i18next
    counts with the target language's own plural rules.
  */
  const uploadButtonLabel =
    uploadableFiles.length > 0
      ? t(hasVideo ? 'contribute.uploadFiles' : 'contribute.uploadImages', {
          count: uploadableFiles.length,
        })
      : t('contribute.upload');
  const filteredPreviewFiles = selectedFiles.filter((item) => {
    if (previewFilter === 'all') return true;
    if (previewFilter === 'ready') return item.validation?.severity === 'valid';
    if (previewFilter === 'warning') return item.validation?.severity === 'warning';
    if (previewFilter === 'invalid') return item.validation?.severity === 'invalid';
    return true;
  });

  /*
    Four states, not two. "Not signed in", "waiting for an administrator" and
    "banned" have completely different remedies, and the old code collapsed them
    into `status === 'approved'` -- a disabled button and no explanation.
  */
  const uploadPermission = uploadState(session);
  const canUpload = uploadPermission === 'approved';

  const handleUpload = async () => {
    if (uploadRunningRef.current) return;
    uploadRunningRef.current = true;
    try {
      await runUpload();
    } finally {
      uploadRunningRef.current = false;
    }
  };

  const refusalMessage = (code) => t(REFUSAL_MESSAGES[code]);

  const runUpload = async () => {
    if (uploadableFiles.length === 0) {
      /*
        The per-file reasons moved to the locale files; these did not, and they
        are the ones that explain why the button did nothing. A contributor
        reading in Odia got their refusal in Odia under the thumbnail and in
        English on the line above the button.
      */
      const msg = hasVideo
        ? t('contribute.noValidFiles')
        : t('contribute.noValidImages');
      setError(msg);
      addToast(msg, 'error', 5000);
      return;
    }
    if (!termsAccepted) {
      const msg = t('contribute.acceptTerms');
      setError(msg);
      addToast(msg, 'error', 5000);
      return;
    }
    if (!siteSlug) {
      const msg = t('contribute.noSite');
      setError(msg);
      addToast(msg, 'error', 5000);
      return;
    }
    if (!canUpload) {
      const msg = t(`contribute.refused.${uploadPermission}`);
      setError(msg);
      addToast(msg, 'error', 5000);
      return;
    }

    setError('');
    setCanRetry(false);
    setIsProcessing(false);
    setIsUploadBusy(true);

    const validation = await checkPreUpload();
    if (!validation.allowUpload) {
      const msg = validation.message;
      setError(msg);
      addToast(msg, 'error', 5000);
      setIsUploadBusy(false);
      return;
    }

    /*
      Batched (`utils/batchUpload.js`): up to 20 files or 150 MB per request
      into one open contribution, then `upload/finish/`. A failed batch is
      retried three times on its own; after that, Retry resumes from the first
      batch the server has not confirmed, into the same contribution, and the
      per-file keys make re-sending a batch whose answer was lost harmless.

      The batches are fixed when the upload starts and kept with the resume
      point, so a Retry sends exactly what was planned. Changing the selection
      or the compression choice discards the open upload instead (the effect
      on `selectedFiles` above).
    */
    let resume = resumeRef.current;
    if (!resume) {
      /*
        And `compress`, not `allow_full_resolution`. The sense is inverted on
        purpose: full resolution is the default now, so a client that forgets
        this parameter gets the better behaviour, not the lossy one
        (internal design notes).
      */
      const entries = uploadableFiles.map((item) => ({
        file: outgoingFile(item, allowFullResolution),
        isVideo: item.validation?.fileType === 'video',
      }));
      const batches = splitIntoBatches(entries);
      /*
        The server's per-upload caps, checked before anything is sent: a
        selection over them would otherwise be refused only after the first
        thousand files or three gigabytes had travelled.
      */
      const plannedFiles = batches.reduce((acc, batch) => acc + batch.length, 0);
      const plannedBytes = batches.reduce((acc, batch) => acc + batchBytes(batch), 0);
      if (plannedFiles > MAX_UPLOAD_FILES || plannedBytes > MAX_UPLOAD_BYTES) {
        const msg = refusalMessage(plannedFiles > MAX_UPLOAD_FILES ? 'too_many_files' : 'too_large');
        setError(msg);
        addToast(msg, 'error', 6000);
        setIsUploadBusy(false);
        return;
      }
      resume = {
        batches,
        nextBatch: 0,
        contributionId: null,
        compress: !allowFullResolution,
      };
      resumeRef.current = resume;
    }

    const controller = new AbortController();
    uploadControllerRef.current = controller;
    const totalFiles = resume.batches.reduce((acc, batch) => acc + batch.length, 0);
    setUploadProgress(0);
    setUploadStatus(t('contribute.uploading', { done: 0, total: totalFiles }));

    try {
      const result = await runBatchedUpload({
        batches: resume.batches,
        startAt: resume.nextBatch,
        contributionId: resume.contributionId,
        /*
          `mesh_vid`, not `site_id`. The view looks the site up by `verbose_id`
          (internal design notes keeps the old wire name until both sides move
          together).
        */
        siteSlug,
        compress: resume.compress,
        post: axios.post,
        signal: controller.signal,
        delays: retryDelays,
        headers: csrfHeaders(),
        jsonHeaders: csrfHeaders(),
        /*
          The bar counts only batches the server has confirmed storing; the
          batch on the wire is shown on its own line underneath. Bytes sent
          are not bytes kept -- a batch can still be refused or lost after its
          last byte -- so the bar never claims them early.
        */
        onProgress: ({ sentBytes, totalBytes, sentFiles, batch }) => {
          const percent = totalBytes ? Math.round((sentBytes / totalBytes) * 100) : 0;
          setUploadProgress(percent);
          setUploadStatus(t('contribute.uploading', { done: sentFiles, total: totalFiles }));
          setBatchProgress(
            batch && batch.count > 1
              ? {
                  batch: batch.index + 1,
                  count: batch.count,
                  percent: batch.totalBytes ? Math.round((batch.sentBytes / batch.totalBytes) * 100) : 0,
                }
              : null,
          );
        },
        onBatchStart: ({ contributionId, keys }) => {
          openUploadRef.current = { contributionId, keys };
        },
        /*
          Every batch stored: the finish closes the upload and queues the
          checks. Only now does the bar say "processing" and Cancel go --
          until here a Cancel still discards what was sent.
        */
        onFinishing: () => {
          finishingRef.current = true;
          setBatchProgress(null);
          setUploadProgress(100);
          setIsProcessing(true);
          setUploadStatus(t('contribute.processing'));
        },
        onRetry: ({ delayMs }) => {
          setUploadStatus(t('contribute.retrying', { seconds: Math.round(delayMs / 1000) }));
        },
        onBatchDone: ({ index, contributionId }) => {
          resume.nextBatch = index + 1;
          resume.contributionId = contributionId;
        },
      });

      // Finalized: there is nothing left to resume or to discard.
      resumeRef.current = null;
      const data = result.data || {};
      const message = data.output || t('contribute.processing');
      setUploadProgress(100);
      setUploadStatus(message);
      addToast(message, 'success', 3000);

      const skipped = invalidCount + (result.rejected?.length || 0);
      if (skipped > 0) {
        addToast(t('contribute.skipped', { count: skipped }), 'warning', 4000);
      }

      setTimeout(() => {
        handleClear();
        onClose();
      }, 3200);
    } catch (uploadError) {
      // The selection stays either way: a failed upload of a hundred
      // photographs should be one tap to retry, not a hundred to re-pick.
      setCanRetry(true);
      setUploadProgress(0);
      if (isAbort(uploadError)) {
        /*
          Cancel means cancel: what was already stored is deleted, and a later
          Retry starts a new upload. (A first batch cancelled before its answer
          arrived has no id to discard; Retry resumes it by its keys, and if
          nobody retries, the server's sweep keeps those photographs.)
        */
        const openId = uploadError.contributionId || resume.contributionId;
        // No id yet (the first batch was cancelled before its answer): the
        // server finds the upload by this contributor's keys at this site.
        const pending = resume.batches[uploadError.resumeFrom ?? resume.nextBatch] || [];
        resumeRef.current = null;
        discardUpload(
          axios.post,
          { contributionId: openId, siteSlug, keys: pending.map((entry) => entry.key) },
          csrfHeaders(),
        );
        const msg = t('contribute.cancelled');
        setUploadStatus(msg);
        addToast(msg, 'info', 4000);
        return;
      }
      console.error(uploadError);
      if (uploadError?.restart) {
        // Expired, finished elsewhere or gone: the next attempt starts over.
        resumeRef.current = null;
      } else if (uploadError?.resumeFrom !== undefined) {
        resume.nextBatch = uploadError.resumeFrom;
        resume.contributionId = uploadError.contributionId ?? resume.contributionId;
      }
      // A cap or a finished upload has a message of its own, in the
      // contributor's language; nothing is retried automatically for one.
      const msg = uploadError?.code
        ? refusalMessage(uploadError.code)
        : uploadError?.output || uploadError?.response?.data?.output || t('contribute.uploadFailed');
      setUploadStatus(t('contribute.uploadFailedShort'));
      setError(msg);
      addToast(msg, 'error');
    } finally {
      uploadControllerRef.current = null;
      openUploadRef.current = null;
      finishingRef.current = false;
      setBatchProgress(null);
      setIsProcessing(false);
      setIsUploadBusy(false);
    }
  };

  const handleCancelUpload = () => {
    uploadControllerRef.current?.abort();
  };

  /*
    The first unmet requirement, in the order the form asks for them. Only the
    two the contributor can act on: "no site" is a bug, and the busy states
    resolve on their own.
  */
  const blockedReason =
    uploadableFiles.length === 0
      ? t(hasVideo ? 'contribute.noValidFiles' : 'contribute.noValidImages')
      : !termsAccepted
        ? t('contribute.acceptTerms')
        : null;

  /*
    The banners and the blocked reason are `role="status"` elements that are
    rendered *with* their text, which announces nothing -- the region has to
    predate the change. The visible markup stays where it is; the persistent
    region carries the same sentence.
  */
  useAnnouncement(
    uploadPermission === 'awaiting_approval'
      ? t('contribute.pendingBody')
      : uploadPermission === 'banned'
        ? t('contribute.bannedBody')
        : blockedReason,
  );

  const submitDisabled =
    uploadableFiles.length === 0 ||
    !termsAccepted ||
    !siteSlug ||
    !canUpload ||
    isUploadBusy ||
    isValidationBusy;

  if (!isOpen) return null;

  return (
    /*
      `role="presentation"` on the scrim says it carries no semantics of its own,
      which is what makes a click handler with no key handler acceptable: there
      is nothing here for a keyboard to reach, and Escape -- handled by
      `useModalBehaviour` -- is the keyboard equivalent of clicking away.
    */
    <div
      role="presentation"
      className="contribute-modal-overlay"
      onClick={(event) => {
        if (event.target === event.currentTarget) handleModalClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={panelRef}
        className="contribute-modal"
      >
        <button
          type="button"
          className="modal-close-button"
          onClick={handleModalClose}
          disabled={isUploadBusy}
          title={isUploadBusy ? t('contribute.uploadInProgress') : undefined}
          aria-label={t('a11y.closeDialog')}
        >
          <Icon name="close" />
        </button>

        <div className="contribute-header">
          <h2 id={titleId}>{t('contribute.heading', { site: contributionTarget })}</h2>
          {siteName && (
            <p className="site-target-note">{t('contribute.selectedSite', { site: siteName })}</p>
          )}
          {isSessionLoading || isAuthLoading ? (
            <div className="auth-loading-spinner-container">
              <div className="auth-loading-spinner" />
              <span className="auth-loading-text">{t('contribute.checkingSession')}</span>
            </div>
          ) : session?.contributor ? (
            <div className="contributor-profile">
              {session.contributor.picture ? (
                <img
                  src={session.contributor.picture}
                  alt=""
                  className="contributor-avatar"
                  referrerPolicy="no-referrer"
                />
              ) : (
                /* Decorative: the name is right beside it in text. Announcing
                   the initial as well just repeats it. */
                <div className="contributor-avatar-fallback" aria-hidden="true">
                  {(session.contributor.name || '?').trim().charAt(0).toUpperCase()}
                </div>
              )}
              <div className="contributor-details">
                <span className="contributor-name">
                  {session.contributor.name || t('contribute.contributor')}
                </span>
                <span className="contributor-email">{session.contributor.email}</span>
              </div>
              <button type="button" className="switch-account-btn" onClick={handleSignOut}>
                <Icon name="logout" />
                {t('contribute.signOut')}
              </button>
            </div>
          ) : (
            /*
              An ordinary button. What was here was an empty `<div>` waiting for
              Google's script to inject a button into it -- which needed a client
              ID that is not configured, an endpoint that does not exist, and a
              CSP allowance for accounts.google.com. It rendered as nothing at
              all: a modal whose sign-in control was a zero-height div.
            */
            <button
              type="button"
              className="btn btn-primary signin-btn"
              onClick={startSignIn}
            >
              {t('contribute.signIn')}
            </button>
          )}
        </div>

        {selectedPreviewImage && (
          <div className="image-lightbox-overlay" onClick={closePreview}>
            <div
              role="dialog"
              aria-modal="true"
              aria-label={t('a11y.imagePreview', { name: selectedPreviewImage.file.name })}
              ref={lightboxRef}
              className="image-lightbox-content"
              onClick={(e) => e.stopPropagation()}
            >
              <button
                type="button"
                className="lightbox-close-btn"
                onClick={closePreview}
                aria-label={t('a11y.closePreview')}
              >
                <Icon name="close" />
              </button>
              {selectedPreviewImage.validation?.fileType === 'video' ? (
                <video
                  className="lightbox-video"
                  src={selectedPreviewImage.previewUrl}
                  controls
                  autoPlay
                />
              ) : !lightboxUrl && isHeicItem(selectedPreviewImage) ? (
                <div className="lightbox-heic-fallback">
                  <Icon name="image" className="fallback-icon" />
                  <span className="fallback-text">{t('contribute.heicNoPreview')}</span>
                  <span className="fallback-subtext">{t('contribute.heicNote')}</span>
                </div>
              ) : (
                <img
                  className="lightbox-img"
                  src={lightboxUrl || selectedPreviewImage.previewUrl}
                  alt={selectedPreviewImage.file.name}
                  decoding="async"
                />
              )}
              <div className="lightbox-caption">
                <div className="lightbox-caption-header">
                  <strong>{selectedPreviewImage.file.name}</strong>
                  <span>{formatBytes(selectedPreviewImage.file.size)}</span>
                </div>
                <div className="lightbox-exif">
                  <div><strong>{t('contribute.camera')}:</strong> {selectedPreviewImage.metadata?.cameraMake || ''} {selectedPreviewImage.metadata?.cameraModel || t('contribute.unknown')}</div>
                  <div><strong>{t('contribute.focalLength')}</strong> {selectedPreviewImage.metadata?.focalLength || t('contribute.unknown')}</div>
                  <div><strong>{t('contribute.dateTaken')}</strong> {selectedPreviewImage.metadata?.dateTaken || t('contribute.unknown')}</div>
                  {selectedPreviewImage.metadata?.latitude !== null && selectedPreviewImage.metadata?.longitude !== null ? (
                    <>
                      <div><strong>{t('contribute.latitude')}:</strong> {parseFloat(selectedPreviewImage.metadata.latitude).toFixed(4)}</div>
                      <div><strong>{t('contribute.longitude')}:</strong> {parseFloat(selectedPreviewImage.metadata.longitude).toFixed(4)}</div>
                      {selectedPreviewImage.metadata.altitude !== null && (
                        <div><strong>{t('contribute.altitude')}:</strong> {parseFloat(selectedPreviewImage.metadata.altitude).toFixed(1)} m</div>
                      )}
                    </>
                  ) : (
                    <div>
                      <strong>{t('contribute.gps')}:</strong> {t('contribute.notAvailable')}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        {authMessage && (
          <div className="auth-banner auth-info">
            {authMessage}
          </div>
        )}

        {/* `role="status"`: a banner explaining why upload is unavailable is
            of no use to someone who is never told it appeared. */}
        {uploadPermission === 'awaiting_approval' && (
          <div className="auth-banner auth-waiting" role="status">
            <strong>{t('contribute.pendingTitle')}</strong>
            <p>{t('contribute.pendingBody')}</p>
          </div>
        )}

        {uploadPermission === 'banned' && (
          <div className="auth-banner auth-banned" role="status">
            {t('contribute.bannedBody')}
          </div>
        )}
        <div className="section">
          <h3>{t('contribute.modelUpload')}</h3>
          <FileUploadBox
            selectedFiles={selectedFiles}
            onFilesChange={handleRawFiles}
            allowOpen={canUpload}
            onAuthRequired={handleAuthRequired}
          />
          <p className="upload-summary">
            {isValidationBusy
              ? t('contribute.validating')
              : totalSelected
                ? t('contribute.filesSelected', { count: totalSelected })
                : t('contribute.noFilesYet')}
          </p>

          {/*
            The "Segmentation Masks (Optional)" upload box was here. It accepted
            files, previewed them, let you remove them -- and `handleUpload`
            never sent them: no field on the request, nowhere to put them, and
            unit 5.10 not yet built. It told people their masks would "improve
            automatic cleaning of the generated 3D model" and then dropped every
            one. Collecting something and silently discarding it is worse than
            not offering it, so it came out.

            5.10 has since landed, and the reason the box is still absent is a
            different one. `POST /upload/` takes a `masks` multipart field and
            `views.attach_masks` pairs each PNG to its image by filename,
            refusing ambiguous names rather than guessing. The intake works.
            What does not work is the pairing of *this form* with that intake:
            `uploads.prepare_mask` requires the mask to match the dimensions of
            the image as *stored*, and a contribution sent with `compress=true`
            -- the default, see `allowFullResolution` above -- is thumbnailed
            server-side to `UPLOAD_COMPRESS_MAX_DIMENSION` (4096 px). A mask
            drawn on the 24 MP photograph the contributor actually has would be
            refused against a 4096 px stored copy they never saw. Most
            contributor masks would land as rejections: the same collect-then-
            discard failure as before, now with an error message.

            And it is not yet known to be worth their trouble. Both consumers,
            `GAUSSIAN_CUT_ENABLE` and `COLMAP_USE_MASKS`, are `False` in
            production; internal design notes leaves the value question open until the
            Lab has measured it. A dropzone here would have to promise "cleaner
            models", which is not currently a true statement.

            Masks come in through the Django admin instead -- per image on the
            Image page, in bulk from the Site page -- where the operator can see
            the stored dimensions and the flags are theirs to turn on. Bringing
            the box back once the Lab reports is small: append `masks` to the
            FormData in `handleUpload` and add a PNG rule to `imageValidation.js`.
            Deferred on purpose, not forgotten.
          */}

          {totalSelected > 0 && (
            <div className="upload-preview-section">
              <div className="upload-preview-header">
                <div>
                  <strong>{t('contribute.selectedCount', { count: totalSelected })}</strong>
                  <p className="preview-help-text">
                    {t('contribute.readyForUpload', { count: readyCount })}{' '}
                    {t('contribute.willBeSkipped', { count: invalidCount })}
                  </p>
                </div>
                <button type="button" className="preview-clear-btn" onClick={handleClear}>
                  {t('contribute.clearAll')}
                </button>
              </div>

              {/*
                `aria-pressed`, because which filter is in force is otherwise
                carried by `.preview-tab.active`'s background colour alone --
                nothing a screen reader can read and nothing a visitor who does
                not see that contrast can tell apart (WCAG 4.1.2, 1.4.1). The
                same convention the map's basemap buttons already use.

                Three of the four labels were English literals beside a fourth
                that came from `t()`, so the row read "All | ପ୍ରସ୍ତୁତ | Warning
                | Invalid" -- two scripts inside one control group.
              */}
              <div className="preview-tab-row">
                {[
                  { id: 'all', label: t('contribute.filterAll') },
                  { id: 'ready', label: t('validation.ready') },
                  { id: 'warning', label: t('contribute.severityWarning') },
                  { id: 'invalid', label: t('contribute.severityInvalid') },
                ].map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    className={`preview-tab ${previewFilter === tab.id ? 'active' : ''}`}
                    aria-pressed={previewFilter === tab.id}
                    onClick={() => setPreviewFilter(tab.id)}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              {/*
                And say what the filter *did*. `aria-pressed` above answers
                "which one is on"; it does not answer "the list under it just
                went from five cards to none", which is the whole effect of
                pressing one. Visually hidden because the card row already shows
                it to anyone who can see it.
              */}
              <p className="sr-only" role="status">
                {t('contribute.filterResult', { count: filteredPreviewFiles.length })}
              </p>

              <div className="upload-preview-summary">
                <span className="summary-pill valid">
                  {t('contribute.pillReady', { count: readyCount })}
                </span>
                <span className="summary-pill warning">
                  {t('contribute.pillWarning', { count: warningCount })}
                </span>
                <span className="summary-pill invalid">
                  {t('contribute.pillInvalid', { count: invalidCount })}
                </span>
              </div>

              <div className="compression-batch-summary">
                {allowFullResolution ? (
                  <div className="summary-details">
                    <span>
                      <strong>{t('contribute.totalSize')}</strong> {formatBytes(totalOriginalSize)}
                    </span>
                    <span className="mode-badge full-res">{t('contribute.fullResBadge')}</span>
                  </div>
                ) : (
                  <div className="summary-details">
                    <span>
                      <strong>{t('contribute.originalSize')}</strong> {formatBytes(totalOriginalSize)}
                    </span>
                    <span>
                      <strong>{t('contribute.compressedSize')}</strong>{' '}
                      {formatBytes(totalCompressedSize)}
                    </span>
                    <span className="savings-badge">
                      {t('contribute.saved', { percent: totalSavedPercent })}
                    </span>
                  </div>
                )}
              </div>

              {hasHeicFile && !allowFullResolution && (
                <div className="heic-compression-note">
                  <Icon name="info" className="note-icon" />
                  <span className="note-text">
                    {t('contribute.heicCompressionNote')}
                  </span>
                </div>
              )}

              {uploadStatus && (
                <div
                  className={`upload-progress-wrapper${isProcessing ? ' is-processing' : ''}`}
                  role="status"
                >
                  <div className="upload-progress-top">
                    <span className="upload-progress-text">{uploadStatus}</span>
                    {!isProcessing && (
                      <span className="upload-progress-percent">{uploadProgress}%</span>
                    )}
                  </div>
                  <div className="upload-progress-track">
                    <div className="upload-progress-fill" style={{ width: `${uploadProgress}%` }} />
                  </div>
                  {batchProgress && !isProcessing && (
                    <span className="upload-progress-batch">
                      {t('contribute.sendingBatch', batchProgress)}
                    </span>
                  )}
                </div>
              )}

              <div className="preview-card-row">
                {filteredPreviewFiles.map((item) => (
                  <div
                    key={item.id}
                    className={`preview-card ${item.validation?.severity || 'invalid'}`}
                  >
                    <button
                      type="button"
                      className="preview-remove-btn"
                      onClick={(event) => {
                        event.stopPropagation();
                        handleRemoveFile(item.id);
                      }}
                      aria-label={t('contribute.removeFile', { name: item.file.name })}
                    >
                      <Icon name="close" />
                    </button>
                    {/*
                      The thumbnail is the control that opens the preview, and it
                      is a `<button>`. The whole card used to carry the click
                      handler on a `<div>`: not focusable, no accessible name, no
                      keyboard route to the preview at all (WCAG 2.1.1). It could
                      not simply become a button either -- it already contains a
                      remove button and, for video, a player with its own
                      controls, and nesting those inside a button is invalid.
                    */}
                    <div className="preview-image-wrapper">
                      {item.validation?.fileType === 'video' ? (
                        <video src={item.previewUrl} controls preload="metadata" className="preview-video" />
                      ) : (
                        <button
                          type="button"
                          className="preview-open-btn"
                          onClick={() => openPreview(item)}
                          aria-label={t('contribute.openPreview', { name: item.file.name })}
                        >
                          {!item.previewUrl || (isHeicItem(item) && !item.validation?.convertedFile) ? (
                            <span className="preview-heic-fallback">
                              <Icon name="image" className="fallback-icon" />
                              <span className="fallback-text">HEIC</span>
                            </span>
                          ) : (
                            <img src={item.previewUrl} alt="" loading="lazy" decoding="async" />
                          )}
                        </button>
                      )}
                    </div>
                      <div className="preview-card-body">
                      <div className="preview-filename">{item.file.name}</div>
                      <div className="preview-meta">
                        {allowFullResolution ? (
                          <span>{formatBytes(item.originalSize)} ({t('contribute.fullResolution')})</span>
                        ) : item.isCompressed ? (
                          <>
                            <span className="original-size-strike">{formatBytes(item.originalSize)}</span>
                            <span className="compressed-size-label"> → {formatBytes(item.compressedSize)}</span>
                            <span className="savings-badge-small"> (-{Math.round(((item.originalSize - item.compressedSize) / item.originalSize) * 100)}%)</span>
                          </>
                        ) : (
                          formatBytes(item.originalSize)
                        )}
                      </div>
                      
                      <div className="preview-exif-info">
                        <div><strong>{t('contribute.model')}:</strong> {item.metadata?.cameraModel || t('contribute.unknown')}</div>
                        <div><strong>{t('contribute.focalLength')}</strong> {item.metadata?.focalLength || t('contribute.unknown')}</div>
                        <div><strong>{t('contribute.date')}:</strong> {item.metadata?.dateTaken || t('contribute.unknown')}</div>
                        {item.metadata?.latitude !== null && item.metadata?.longitude !== null ? (
                          <>
                            <div><strong>{t('contribute.latitude')}:</strong> {parseFloat(item.metadata.latitude).toFixed(4)}</div>
                            <div><strong>{t('contribute.longitude')}:</strong> {parseFloat(item.metadata.longitude).toFixed(4)}</div>
                          </>
                        ) : (
                          <div>
                      <strong>{t('contribute.gps')}:</strong> {t('contribute.notAvailable')}
                    </div>
                        )}
                      </div>

                      <span className={`status-badge ${item.validation?.severity || 'invalid'}`}>
                        {item.validation?.severity === 'valid'
                          ? t('validation.ready')
                          : item.validation?.severity === 'warning'
                            ? /*
                                The severity, not one of its causes. This read
                                "Low resolution" for *every* warning, so a
                                photograph with no EXIF, or one this browser
                                cannot preview, was badged "Low resolution"
                                above a reason line saying something else. The
                                specific cause is that reason line's job.
                              */
                              t('contribute.severityWarning')
                            : t('contribute.severityInvalid')}
                      </span>
                      {item.validation?.severity !== 'valid' && (
                        <p className="preview-reason">
                          {item.validation?.reasonKey
                            ? t(item.validation.reasonKey, item.validation.reasonParams ?? {})
                            : null}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/*
          One block, not two collapsibles.

          "Upload options" and "What makes a good set" were separate sections,
          each behind its own disclosure, each holding one thing: a checkbox and
          a bullet list. Two clicks to reach two paragraphs, in a dialog whose
          job is to accept photographs.

          The guidance is prose now rather than seven bullets. Two of those
          bullets are also gone from all three languages: one told contributors
          to "turn on ordered uploads", a control removed when it was found to
          send nothing to the server, and the other restated the full-resolution
          option standing immediately below it.
        */}
        <div className="section upload-guidance">
          <h3>{t('contribute.checklistHeading')}</h3>
          <p className="upload-guidance-text">
            {t('contribute.checklist', { returnObjects: true }).join(' ')}
          </p>
          <label className="option-label">
            <input
              type="checkbox"
              checked={allowFullResolution}
              onChange={(e) => setAllowFullResolution(e.target.checked)}
            />
            {t('contribute.fullResLabel')}
          </label>
          <p className="option-help">{t('contribute.fullResHelp')}</p>
        </div>

        <div className="section terms-section">
          <label className="terms-label">
            <input
              type="checkbox"
              checked={termsAccepted}
              onChange={(e) => setTermsAccepted(e.target.checked)}
            />
            {/*
              Built from the deployment prefix and pointing at the canonical
              documents. They were absolute to production and aimed at `#terms`
              -- so the demo and beta both asked people to consent to
              production's copy, at a fragment anchor on a page that is being
              retired.
            */}
            {t('contribute.agree')}{' '}
            <a
              href={`${deploymentPrefix()}/terms/`}
              target="_blank"
              rel="noopener noreferrer"
            >
              {t('request.terms')}
            </a>{' '}
            {t('contribute.and')}{' '}
            <a
              href={`${deploymentPrefix()}/privacy/`}
              target="_blank"
              rel="noopener noreferrer"
            >
              {t('request.privacy')}
            </a>
          </label>
          <p className="license-text">
            {t('contribute.licence')}{' '}
            <a href="https://creativecommons.org/licenses/by-nc-sa/4.0/" target="_blank" rel="noopener noreferrer">
              CC BY-NC-SA 4.0
            </a>
          </p>
        </div>

        {error && <p className="error-text">{error}</p>}
        <div className="snackbar-container">
          {toastItems.map((toast) => (
            <Snackbar
              key={toast.id}
              message={toast.message}
              variant={toast.variant}
              onClose={() => dismissToast(toast.id)}
            />
          ))}
        </div>

        <div className="modal-actions">
          {isUploadBusy && !isProcessing ? (
            /*
              Only while bytes are moving. Once the server has them, aborting
              would only stop us hearing the answer -- the contribution may be
              stored regardless -- so the button goes away with the bar.
            */
            <button type="button" className="btn clear-btn cancel-upload-btn" onClick={handleCancelUpload}>
              {t('contribute.cancel')}
            </button>
          ) : (
            <button
              type="button"
              className="btn clear-btn"
              onClick={handleClear}
              disabled={isUploadBusy}
            >
              {t('contribute.clear')}
            </button>
          )}
          <button
            type="button"
            className="btn upload-btn"
            onClick={handleUpload}
            disabled={submitDisabled}
            aria-describedby={blockedReason ? 'upload-blocked-reason' : undefined}
          >
            {canRetry && !isUploadBusy ? t('contribute.retry') : uploadButtonLabel}
          </button>
        </div>
        {/*
          Why the button is not available.

          `handleUpload` has messages for "no usable files" and "terms not
          accepted", and neither could ever be seen: `submitDisabled` covers
          exactly those two states, so the click never happened. The contributor
          got a dead control and no explanation in any language. Said here
          instead, before the click rather than after it, and tied to the button
          with `aria-describedby` so a screen reader gives the reason along with
          the disabled state.
        */}
        {blockedReason && (
          <p className="upload-blocked-reason" id="upload-blocked-reason" role="status">
            {blockedReason}
          </p>
        )}
      </div>
    </div>
  );
}

export default ContributeModal;
