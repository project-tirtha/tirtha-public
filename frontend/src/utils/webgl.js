/**
 * Can this browser draw WebGL at all?
 *
 * Asked once, up front, so a browser without it gets a list of sites rather
 * than MapLibre throwing during mount -- which used to reach the page-level
 * error boundary and replace everything, list and Contribute included, with
 * "Something went wrong".
 *
 * The probe context is released straight away: browsers cap live contexts
 * (16 in Chrome), and the map and the viewer each need one of their own.
 */
let cached;

export function hasWebGL() {
  if (cached !== undefined) return cached;
  try {
    if (typeof window === 'undefined' || !window.WebGL2RenderingContext) {
      cached = false;
      return cached;
    }
    const canvas = document.createElement('canvas');
    // WebGL2 only. MapLibre 6 dropped WebGL1 and throws without a WebGL2
    // context, and three.js (the viewer) has required it since r163 -- so a
    // WebGL1-only browser passing this probe would get a map that cannot start.
    const gl = canvas.getContext('webgl2');
    cached = Boolean(gl);
    gl?.getExtension?.('WEBGL_lose_context')?.loseContext();
  } catch {
    cached = false;
  }
  return cached;
}

/** Tests only. */
export function resetWebGLProbe() {
  cached = undefined;
}
