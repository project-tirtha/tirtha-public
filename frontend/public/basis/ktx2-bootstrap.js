/*
  The KTX2 transcoder worker, loaded from a URL instead of a blob.

  Safari refuses to run the Basis transcoder under this site's CSP. Proved with
  a probe on the live host: with no CSP the worker initialises in 44ms; with
  `script-src 'self' 'wasm-unsafe-eval'` it fails with

      EvalError: Refused to evaluate a string as JavaScript

  because the Emscripten glue evaluates strings, and `'wasm-unsafe-eval'` covers
  `WebAssembly.compile` (which passes) but not `eval`. Chrome and Firefox take a
  different path and are unaffected -- but every browser on iOS is WebKit, so
  "use Chrome" is not an answer there.

  The fix is where the worker comes from. A blob worker inherits the creating
  document's policy; a worker fetched from a URL takes the policy of *its own*
  response, and nginx serves this directory with no CSP header. So the
  transcoder gets to evaluate its strings while the document stays locked down.
  Granting the whole application `'unsafe-eval'` was the alternative.

  `importScripts` runs at top level, synchronously: a worker script completes
  before any queued message is dispatched, so the transcoder installs its own
  `onmessage` before three's `init` message is handled.
*/
importScripts(new URL(self.location.href).searchParams.get('src'));
