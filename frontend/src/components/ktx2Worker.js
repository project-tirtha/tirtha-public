/*
  The KTX2 transcoder worker, in its own module.

  Split out of `ModelViewer3D.jsx` so it can be tested without importing
  three.js, drei and the splat viewer: importing that module from a second test
  file was enough to push `Home.test.jsx` past its `waitFor` budget. This file
  has no heavy dependency, and the tests next to it run in milliseconds.
*/

const debugError = (...args) => {
  console.error("[tirtha:viewer]", ...args);
};

/*
  Whether the transcoder has failed for the model being shown.

  Module state because the failure surfaces on a worker, far from the component
  that has to explain it. Cleared per model by `resetTranscoderFailed` -- as a
  latch it made one bad model blame the texture decoder for every later stall.
*/
let transcoderFailed = false;

export function resetTranscoderFailed() {
  transcoderFailed = false;
}

export function hasTranscoderFailed() {
  return transcoderFailed;
}

/**
 * Build the KTX2 transcoder worker, replacing three's own creator.
 *
 * Exported, and taking `WorkerCtor`/`baseUrl` as parameters, for one reason:
 * this lived inside a `useCallback` in a component that mounts a WebGL canvas,
 * so nothing tested it -- and the first version shipped without the `init`
 * message, which broke every KTX2 mesh in every browser. It is ordinary code
 * with ordinary inputs now, and `ModelViewer3D.test.jsx` exercises it.
 *
 * `create` is three's original creator, used only if constructing the
 * same-origin worker throws.
 */
export function createTranscoderWorker(
  ktx2,
  create,
  onError,
  WorkerCtor = typeof Worker === "undefined" ? undefined : Worker,
  baseUrl = import.meta.env.BASE_URL,
) {
  /*
          A worker from a URL, not from a blob.

          three builds its transcoder worker with
          `URL.createObjectURL(new Blob([body]))`, and a blob worker inherits
          the creating document's CSP. Safari then refuses the Emscripten
          glue's string evaluation -- `EvalError: Refused to evaluate a string
          as JavaScript` under `script-src 'self' 'wasm-unsafe-eval'` -- and
          the worker dies at birth. Measured on this deployment: the same
          worker initialises in 44ms when the page carries no CSP.

          A worker fetched from a URL takes the policy of its own response
          instead, and nginx serves `/static/` with no CSP header. So the
          bootstrap is loaded from there and `importScripts` pulls in the blob
          three already built. The document keeps its strict policy; the
          alternative was granting the whole app `'unsafe-eval'`.

          If anything about this fails, the `error` listener below reports it
          rather than letting the transcode promise hang for ever, which is
          what produced "Loading the model... 97%" with nothing in the console.
        */
  let worker;
  try {
    const bootstrap = `${baseUrl}basis/ktx2-bootstrap.js`;
    worker = new WorkerCtor(
      `${bootstrap}?src=${encodeURIComponent(ktx2.workerSourceURL)}`,
    );
    /*
            The `init` message, which three's own creator sends and this one
            replaced.

            `KTX2Loader.BasisWorker` assigns `transcoderPending` only inside
            its `init` handler, and its `transcode` handler opens with
            `transcoderPending.then(...)`. A worker that is never initialised
            therefore throws `TypeError: Cannot read properties of undefined`
            on its first texture -- so swapping the creator without carrying
            this across turned a Safari-only failure into every browser.

            Ordering is safe: `importScripts` runs synchronously at worker top
            level, so the imported script's `message` listener is registered
            before this queued message is ever dispatched.

            `.slice(0)` because the buffer is transferred, and the loader
            reuses `transcoderBinary` for the next worker in the pool.
          */
    const transcoderBinary = ktx2.transcoderBinary.slice(0);
    worker.postMessage(
      { type: "init", config: ktx2.workerConfig, transcoderBinary },
      [transcoderBinary],
    );
  } catch (err) {
    debugError("Same-origin transcoder worker failed; falling back:", err);
    worker = create();
  }

  worker.addEventListener("error", (event) => {
    transcoderFailed = true;
    debugError(
      "KTX2 transcoder worker failed to start:",
      event.message || event,
    );
    onError?.(
      new Error(
        "The texture decoder could not start in this browser" +
          (event.message ? ` (${event.message})` : "") +
          ".",
      ),
    );
  });
  return worker;
}
