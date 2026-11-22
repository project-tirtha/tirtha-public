/*
  Start the worker from a same-origin blob that imports the hashed worker file,
  not from the file's URL.

  The page is cross-origin isolated (`Cross-Origin-Opener-Policy: same-origin`
  + `Cross-Origin-Embedder-Policy: credentialless`, from Django's
  SecurityHeadersMiddleware). Chromium then requires a dedicated worker's own
  script *response* to carry a COEP header too, and nginx serves `/static/`
  without one -- so `new Worker('/static/assets/maplibre-gl-worker-*.js')`
  is refused (`requestfailed`, "Worker failed to load") and the map draws
  nothing. That is how the MapLibre 6 build failed in production.

  A blob worker has no response: it inherits the creating document's COEP and
  CSP (`worker-src 'self' blob:`, as MapLibre 5's inlined blob worker did), and
  its `import` of the same-origin worker file is an ordinary module-script
  subresource, which `credentialless` allows. No server change needed.

  The blob is never revoked: MapLibre starts several workers from it, and
  restarts them after a context loss.
*/
export function maplibreWorkerBootstrap(
  workerUrl,
  base = typeof location === 'undefined' ? undefined : location.href,
) {
  if (typeof Blob === 'undefined' || typeof URL.createObjectURL !== 'function' || !base) {
    return workerUrl;
  }
  const href = new URL(workerUrl, base).href;
  return URL.createObjectURL(
    new Blob([`import ${JSON.stringify(href)};\n`], { type: 'text/javascript' }),
  );
}

