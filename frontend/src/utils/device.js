/**
 * Models above this size ask before downloading on a constrained device.
 *
 * Published splats run to 288 MB. A phone tab that downloads one and then
 * expands it into GPU buffers is a tab the OS kills, with no message -- the
 * visitor sees the page reload and nothing else.
 */
export const LARGE_MODEL_BYTES = 50 * 1024 * 1024;

/**
 * Mobile, low-memory (`navigator.deviceMemory` <= 4 GB) or Save-Data.
 *
 * Each signal is optional: `deviceMemory` and `connection` are Chromium-only,
 * so Safari on an iPhone is recognised by the coarse pointer and narrow screen
 * instead. Every read is guarded -- a feature probe must never be the thing
 * that throws.
 */
export function isConstrainedDevice(nav = typeof navigator !== 'undefined' ? navigator : {}) {
  try {
    if (nav.connection?.saveData === true) return true;
    if (typeof nav.deviceMemory === 'number' && nav.deviceMemory <= 4) return true;
    if (nav.userAgentData?.mobile === true) return true;
    if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
      const coarse = window.matchMedia('(pointer: coarse)')?.matches;
      const narrow = window.matchMedia('(max-width: 900px)')?.matches;
      if (coarse && narrow) return true;
    }
    return /Android|iPhone|iPad|iPod|Mobile/i.test(nav.userAgent || '');
  } catch {
    return false;
  }
}

/** Whether to ask before loading a model of `bytes` bytes. Unknown size: no. */
export function shouldConfirmModelLoad(bytes, nav) {
  if (typeof bytes !== 'number' || !Number.isFinite(bytes)) return false;
  return bytes > LARGE_MODEL_BYTES && isConstrainedDevice(nav);
}

/** Whole megabytes, rounded up: "Load 51 MB" is truer than "Load 50 MB". */
export function toMegabytes(bytes) {
  return Math.ceil(bytes / (1024 * 1024));
}
