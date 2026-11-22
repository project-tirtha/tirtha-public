import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls, useGLTF, useProgress } from "@react-three/drei";
import * as THREE from "three";
import { KTX2Loader } from "three-stdlib";
import * as GaussianSplats3D from "@mkkellogg/gaussian-splats-3d";
import { useTranslation } from "react-i18next";

import ErrorBoundary from "./ErrorBoundary";
import {
  createTranscoderWorker,
  hasTranscoderFailed,
  resetTranscoderFailed,
} from "./ktx2Worker";
import { eulerQuaternion } from "../utils/splatRotation";
import { withStyleNonce } from "../utils/styleNonce";
import "../styles/ModelViewer3D.css";
import { prefersReducedMotion } from "../utils/motion";
import {
  isConstrainedDevice,
  LARGE_MODEL_BYTES,
  toMegabytes,
} from "../utils/device";
import { viewerErrorKey } from "../utils/viewerErrors";

/*
  Which loader a URL needs.

  `.splat` is the legacy archive format and what `run_postprocess` now publishes
  beside every `.sog`; `.ply` is the uncompressed interchange format.
  gaussian-splats-3d reads both (`SceneFormat.Splat`, `SceneFormat.Ply`).
  Everything else goes to drei's `useGLTF`, which is right for the
  photogrammetry path's `.glb`.

  Not `.spz`: the library's SPZ loader accepts versions 1-2 and splat-transform
  writes 4, so routing one here would trade a silent blank for a load error.
*/
/*
  `LoaderStatus.Downloading`, inlined.

  The library defines the enum but does not export it -- its export list is
  AbortablePromise, DropInViewer, the loaders, LogLevel, and so on, with no
  `LoaderStatus` among them. Reading it off the namespace gives `undefined`, and
  `undefined.Downloading` throws *inside the progress callback*, which the
  loader treats as a failed load: the viewer showed "could not be loaded" and
  never issued the request at all. Checked against the installed 0.4.7.
*/
const LOADER_DOWNLOADING = 0;

/*
  How long to wait for the *first* byte before calling a model stalled. Not a
  budget for the whole load -- see the effect that uses it.
*/
const NO_START_TIMEOUT_MS = 60_000;

/*
  How long to wait for the *next* file once files are arriving. Separate from
  the constant above, and deliberately longer: this one is about a load that has
  stopped, not one that never began.
*/
const STALL_TIMEOUT_MS = 90_000;

/*
  Set when `detectSupport` finds no compressed texture format, which turns every
  texture into an RGBA32 buffer. Module-level because the loader that discovers
  it and the timer that has to explain it are in different components.
*/
let noCompressedTextures = false;

/*
  Set when the KTX2 transcoder worker dies on startup. Module-level for the same
  reason as the flag above: the loader that discovers it and the message the
  visitor reads are in different components.
*/

const SPLAT_FORMATS = /\.(splat|ply)(?:\?|$)/i;
const isSplatUrl = (value) =>
  typeof value === "string" && SPLAT_FORMATS.test(value.trim());

const debugLog = (...args) => {
  if (import.meta.env.DEV) {
    console.log(...args);
  }
};

const debugWarn = (...args) => {
  if (import.meta.env.DEV) {
    console.warn(...args);
  }
};

/*
  Errors log in production too, unlike the two above.

  This was `import.meta.env.DEV`-gated with them, so a model that failed on the
  live site printed *nothing*: the visitor got "The model could not be loaded"
  and the console was clean. Diagnosing one then meant reasoning from the
  server side about a failure that only happens in a browser -- which is
  possible, and is not the same as being told.

  A caught error, reported once, at the moment it happens, is not debug noise.
  `debugLog` and `debugWarn` stay development-only precisely so this stays
  legible when it fires.
*/
const debugError = (...args) => {
  console.error("[tirtha:viewer]", ...args);
};

/**
 * Only http(s) URLs reach a loader.
 *
 * A cross-origin model used to be rewritten to `/api/proxy-model/?url=<any>`.
 * That endpoint does not exist on this backend -- so every such model 404'd --
 * and it should not: a view that fetches whatever URL a query string names is
 * a server-side request forgery sink, reachable by anyone who can get a URL
 * into the page. Tirtha serves its own artifacts from its own origin
 * (`ARK.url`, made absolute by `api._absolute`), so there is nothing to proxy.
 *
 * What is left is the scheme check. `file_url` comes from the database, and a
 * `javascript:` or `data:` value handed to a loader is a script-execution
 * primitive; refusing it here costs nothing and closes that off regardless of
 * how the row was written.
 */
function resolveModelUrl(modelUrl) {
  if (!modelUrl || typeof modelUrl !== "string") return null;

  try {
    const parsed = new URL(modelUrl, window.location.origin);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      debugWarn("Refusing a model URL that is not http(s):", parsed.protocol);
      return null;
    }
    return parsed.href;
  } catch (error) {
    debugWarn("Unparseable model URL:", error);
    return null;
  }
}

function disposeObject3D(root) {
  root.traverse((obj) => {
    if (obj.geometry) {
      obj.geometry.dispose();
    }

    if (obj.material) {
      const materials = Array.isArray(obj.material)
        ? obj.material
        : [obj.material];
      materials.forEach((material) => {
        if (!material) return;

        Object.values(material).forEach((value) => {
          if (
            value &&
            typeof value === "object" &&
            typeof value.dispose === "function"
          ) {
            value.dispose();
          }
        });

        material.dispose?.();
      });
    }
  });
}

/**
 * A .glb from the photogrammetry path.
 *
 * There used to be a `try/catch` around the whole body. `useGLTF` reports a
 * *pending* load by throwing a Promise -- that is how Suspense works -- so the
 * catch intercepted the loading signal itself and reported it to `onError` as
 * though the download had failed, on the very first render of every model.
 * Suspense above handles pending; the boundary the caller wraps this in handles
 * failure. Neither belongs in a catch here.
 */
/*
  The KTX2 transcoder currently in use, at module scope.

  Not a ref inside `GLTFModel`: that component remounts for every model, so a
  ref would be reset to null before it could ever dispose the loader belonging
  to the model being left. There is one viewer on the page, so one holder.
*/
let activeKtx2 = null;

function GLTFModel({ url, rotation, onLoadComplete, onError }) {
  const group = useRef();
  const gl = useThree((state) => state.gl);

  /*
    The published meshes need a KTX2 transcoder, and nothing was giving them one.

    `run_meshopt` writes the `.glb` through gltfpack, so every one of the 47
    published meshes declares `extensionsRequired: [KHR_mesh_quantization,
    EXT_meshopt_compression, KHR_texture_basisu]` with `image/ktx2` textures.
    drei's `useGLTF` handles the first two on its own -- quantization is native
    to three, and meshopt is on by default with `meshoptimizer` installed -- but
    a KTX2 texture needs a transcoder registered, and without one the load
    throws and the viewer says "the model could not be loaded".

    So **every AliceVision site in the archive failed to open**, which is most
    of what predates the Gaussian-splat pipeline. It looked like one bad model.

    The Basis transcoder ships in `public/basis/` rather than from a CDN: the
    CSP allows scripts and WASM from `'self'` only, and a `.wasm` fetched from
    anywhere else is blocked with no error the page can see.

    `KTX2Loader` comes from `three-stdlib`, which drei already depends on and
    already bundles. Importing it from `three/examples/jsm/` instead pulled a
    second copy of that tree into the graph and the build stopped producing
    output entirely -- no error, no progress, ten minutes and counting.
  */
  const extendLoader = useCallback(
    (loader) => {
      /*
        Dispose the previous transcoder before building another.

        r3f caches one `GLTFLoader` per class but calls `extendLoader` again for
        every new URL, so each mesh opened constructed a fresh `KTX2Loader` --
        its own blob worker source, its own pool of four workers, its own wasm
        heap -- and `setKTX2Loader` simply dropped the previous one. Ten meshes
        left forty workers alive holding their heaps; three.js warns about
        exactly this ("Multiple active KTX2 loaders") and the warning was being
        printed and ignored.

        `dispose()` terminates the pool's workers and frees the transcoder. The
        outgoing loader is never the one about to be used, because r3f has
        already moved to the new URL by the time this runs.
      */
      if (activeKtx2) {
        try {
          activeKtx2.dispose();
        } catch (err) {
          debugError("Disposing the previous KTX2 transcoder failed:", err);
        }
        activeKtx2 = null;
      }

      const ktx2 = new KTX2Loader()
        // `BASE_URL` is what vite.config.js was built with -- the deployment's
        // own static base -- so the same bundle finds its transcoder on the
        // demo, on beta and in production without being told where it is.
        .setTranscoderPath(`${import.meta.env.BASE_URL}basis/`)
        .detectSupport(gl);

      /*
        No compressed-texture support is a memory problem, not a quality one.

        `detectSupport` asks the context which of ASTC / ETC2 / S3TC / BPTC /
        PVRTC it has. If the answer is none, KTX2Loader says so once in a
        console warning and decodes every texture to **RGBA32** -- 16.8 MB for
        each 2048x2048 image. These meshes carry 64 to 895 of them, so that is
        1 GB for Brahmeswara and 1.9 GB for Gopinath, and the tab dies partway
        through: the load stops a few files short and sits there, which is
        exactly what both of them do.

        It is not the file. Every one of Brahmeswara's 64 textures transcodes
        cleanly through this same transcoder outside the browser.

        A context ends up here when the GPU is unavailable and WebGL falls back
        to software -- hardware acceleration switched off, a driver on the
        browser's blocklist, a remote or virtualised display. So this is worth
        saying plainly rather than leaving as a warning three logs and nothing
        reads.

        One worker rather than four in that state: it does not make 1 GB fit,
        but it stops four simultaneous 16.8 MB allocations racing to be the one
        that fails.
      */
      const cfg = ktx2.workerConfig || {};
      const compressed =
        cfg.astcSupported ||
        cfg.etc2Supported ||
        cfg.etc1Supported ||
        cfg.dxtSupported ||
        cfg.bptcSupported ||
        cfg.pvrtcSupported;
      noCompressedTextures = !compressed;
      // Cleared per model: latched, one bad model made every later
      // stall message on any model blame the texture decoder.
      resetTranscoderFailed();
      if (noCompressedTextures) {
        ktx2.setWorkerLimit(1);
        debugError(
          "This browser reports no compressed-texture support, so every texture " +
            "decodes to RGBA32 (~16.8 MB per 2048x2048). Models with many textures " +
            "will exhaust memory and stall. Hardware acceleration is most likely off " +
            "or unavailable -- check chrome://gpu.",
        );
      }

      /*
        A transcoder worker that dies must not become a silent wait.

        `WorkerPool` never attaches an `onerror`, so when the worker fails to
        start its transcode promise simply never settles: `useGLTF` suspends
        for ever and the viewer sits on a percentage. That is the whole of the
        Safari failure -- the Basis glue evaluates a string, the document's
        `script-src 'self' 'wasm-unsafe-eval'` refuses it, the worker dies at
        birth, and nothing was listening.

        `setWorkerCreator` is public API, so the creator can be wrapped without
        patching the library: three's own creator still builds the worker, and
        this adds the listener it omits. The failure is then immediate and says
        what happened, instead of ninety seconds of nothing.
      */
      const pool = ktx2.workerPool;
      const registerCreator = pool.setWorkerCreator.bind(pool);
      pool.setWorkerCreator = (create) =>
        registerCreator(() => createTranscoderWorker(ktx2, create, onError));

      activeKtx2 = ktx2;
      loader.setKTX2Loader(ktx2);
    },
    [gl, onError],
  );

  const gltf = useGLTF(url, true, true, extendLoader);

  useEffect(() => {
    const node = group.current;
    let clonedScene = null;

    if (gltf.scene && node) {
      debugLog("GLTF model loaded");

      clonedScene = gltf.scene.clone(true);

      const box = new THREE.Box3().setFromObject(clonedScene);
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());

      const maxDim = Math.max(size.x, size.y, size.z);
      const scale = maxDim > 0 ? 2 / maxDim : 1;

      clonedScene.scale.multiplyScalar(scale);
      clonedScene.position.set(
        -center.x * scale,
        -center.y * scale - 0.5,
        -center.z * scale,
      );

      /*
        `Run.model_rotation`, in degrees (internal design notes). It comes from
        `rotaX/Y/Z`, which the old page fed to `<model-viewer>` as
        `orientation="Zdeg Xdeg Ydeg"` -- roll, pitch, yaw, applied to the
        model. 82 runs carry a framing an operator tuned by hand, and this
        viewer ignored it: a scan whose up-axis is not Y arrived on its side.

        On the group, not the clone, so it composes with the centring above
        instead of rotating the model out from under its own offset.
      */
      const [rx, ry, rz] = rotation ?? [0, 0, 0];
      node.rotation.set(
        THREE.MathUtils.degToRad(rx),
        THREE.MathUtils.degToRad(ry),
        THREE.MathUtils.degToRad(rz),
        "ZXY",
      );

      node.clear();
      node.add(clonedScene);
      onLoadComplete?.();
    }

    return () => {
      if (clonedScene) {
        disposeObject3D(clonedScene);
      }
      // Copied out of the ref: by the time cleanup runs React may already have
      // detached the node, and `group.current` would be null.
      node?.clear();
    };
  }, [gltf, rotation, onLoadComplete]);

  return <group ref={group} />;
}

// Uses @mkkellogg/gaussian-splats-3d (same library & version as the old Tirtha
// Public site) instead of drei's <Splat>. Tirtha's .splat pipeline writes the
// antimatter15 32-byte-per-splat format with no header; drei's loader hard-
// assumes the streamed body divides evenly into 32-byte rows and throws
// "byte length of Float32Array should be a multiple of 4" the moment that
// assumption is violated (e.g. a truncated or non-binary response). This
// loader buffers the full response first and surfaces real HTTP errors
// instead of trying to parse them as splat data.
function SplatModel({ url, rotation, onLoadComplete, onError, onProgress }) {
  /*
    Wrapped because this constructor injects three `<style>` elements for a
    loading UI we disable below -- and `style-src` refuses them, six CSP errors
    per model load. `withStyleNonce` stamps the nonce the server already issued,
    for the duration of this call only.
  */
  const [dropInViewer] = useState(() =>
    withStyleNonce(
      () =>
        new GaussianSplats3D.DropInViewer({
          logLevel: GaussianSplats3D.LogLevel.Warning,
          /*
            **This is why the viewer never worked in Safari.**

            The library defaults `sharedMemoryForWorkers` to true and only turns
            it off for iOS below 16. A `SharedArrayBuffer` requires the document
            to be cross-origin isolated, which requires
            `COEP: require-corp` -- and we send `COEP: credentialless`, which
            Safari does not implement. So Safari is never isolated, the
            constructor is nonetheless told to use shared memory, and the sort
            worker cannot start.

            `crossOriginIsolated` is the browser's own answer to "did you
            actually grant me this", which is the right thing to ask in every
            browser rather than maintaining a list of the ones that say no.
          */
          sharedMemoryForWorkers:
            typeof crossOriginIsolated !== "undefined" &&
            crossOriginIsolated === true,
        }),
    ),
  );

  useEffect(() => {
    if (!url) {
      onError?.(new Error("No model URL available for Gaussian Splat."));
      return undefined;
    }

    let cancelled = false;

    dropInViewer
      .addSplatScene(url, {
        splatAlphaRemovalThreshold: 5,
        showLoadingUI: false,
        position: [0, 0, 0],
        scale: [1, 1, 1],
        /*
          `Run.model_rotation` in degrees, as a **flat [x, y, z, w] array**.

          The library does `new THREE.Quaternion().fromArray(rotationArray)`,
          which reads `array[0..3]`. A `THREE.Quaternion` has no numeric indices,
          so handing it the object gives four `undefined`s, and
          `makeRotationFromQuaternion` turns those into a NaN matrix -- which is
          every splat in the scene at no position at all. It runs even for the
          default `[0, 0, 0]`, so passing the wrong type here would have made
          *every* Gaussian splat model invisible, while fixing the bug where the
          stored rotation was ignored.
        */
        rotation: eulerQuaternion(rotation).toArray(),
        /*
          A published model is several megabytes, and the overlay said only
          "Loading the model…" for the whole of it -- so a slow connection is
          indistinguishable from a broken one, and the visitor's only way to
          tell is to keep waiting. The library reports bytes as they arrive;
          `LoaderStatus.Downloading` is the phase where a percentage means
          something, and the rest of the work has no measurable denominator.
        */
        onProgress: (percent, _label, status) => {
          onProgress?.(percent, status === LOADER_DOWNLOADING);
        },
      })
      .then(() => {
        if (cancelled) return;
        debugLog("Gaussian splat scene loaded");
        onLoadComplete?.();
      })
      .catch((err) => {
        if (cancelled) return;
        debugError("Error loading Gaussian splat scene:", err);
        onError?.(err instanceof Error ? err : new Error(String(err)));
      });

    return () => {
      cancelled = true;
      // Actually remove it. The flag alone left the scene in the viewer, so a
      // re-run stacked a second copy on top of the first.
      try {
        dropInViewer.removeSplatScene?.(0);
      } catch (err) {
        debugWarn("Could not remove the previous splat scene:", err);
      }
    };
  }, [url, rotation, dropInViewer, onLoadComplete, onError, onProgress]);

  useEffect(() => {
    return () => {
      dropInViewer.viewer.dispose();
    };
  }, [dropInViewer]);

  return <primitive object={dropInViewer} />;
}

function Model({ url, rotation, onLoadComplete, onError, onProgress }) {
  // Falling through to `TestModel` here put a spinning red debug cube in front
  // of the visitor for every site without a published model, which reads as a
  // broken site rather than one nobody has reconstructed yet. The caller now
  // renders a real empty state instead, so there is nothing to draw and --
  // importantly -- nothing to report from inside a render pass.
  if (!url || !url.trim()) return null;

  const safeUrl = resolveModelUrl(url);
  if (!safeUrl) return null;

  if (isSplatUrl(url)) {
    return (
      <SplatModel
        url={safeUrl}
        rotation={rotation}
        onLoadComplete={onLoadComplete}
        onError={onError}
        onProgress={onProgress}
      />
    );
  }

  return (
    <GLTFModel
      url={safeUrl}
      rotation={rotation}
      onLoadComplete={onLoadComplete}
      // The mesh path reports failures now: a transcoder worker that dies at
      // startup used to be a silent, permanent wait.
      onError={onError}
    />
  );
}

/**
 * Where the run's own camera becomes r3f's camera (internal design notes).
 *
 * Every site is framed differently -- a gopuram wants a camera high and back,
 * a stepwell wants one looking down -- and an operator stores that framing per
 * run, in `Run.cam_position` / `cam_target` / `cam_fov`. The old viewer honoured
 * it; this one opened every one of the 59 sites at `[0, 0, 4]`, so a tall temple
 * arrived clipped through the middle and the visitor had to hunt for it.
 *
 * Only a full 3-vector is trusted. A partly-filled record is likelier to point
 * the camera into the ground than to improve on the default.
 */
function vec3(value, fallback) {
  if (!Array.isArray(value) || value.length !== 3) return fallback;
  const numbers = value.map(Number);
  return numbers.every(Number.isFinite) ? numbers : fallback;
}

function ModelViewerCanvas({
  modelPath,
  camera,
  siteName,
  resolved = true,
  onContributeClick,
  fetchFailed = false,
  mapStatus = null,
  onRetry = null,
}) {
  const { t } = useTranslation();
  const [isLoading, setIsLoading] = useState(true);
  /*
    `null` while there is no measurable figure -- before the first byte, and
    during the parse and sort that follow the download. A bar that sits at 100%
    for the last few seconds is a worse lie than no bar.
  */
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState(null);
  // Whether anything at all has arrived for the current model.
  const startedRef = useRef(false);
  const currentCanvasRef = useRef(null);

  /*
    Reset when the site changes. Without this, `error` and `isLoading` were
    per-*component* rather than per-*model*: after opening one site whose model
    failed, every site opened afterwards showed that first error over its own
    perfectly good model, because the viewer stays mounted between selections.

    Adjusted during render rather than in an effect. React re-runs this function
    before touching the DOM, so the visitor never sees the stale error for a
    frame -- an effect would show it, then clear it.
  */
  const [shownModel, setShownModel] = useState(modelPath);
  if (modelPath !== shownModel) {
    setShownModel(modelPath);
    setError(null);
    setIsLoading(Boolean(modelPath));
    setProgress(null);
  }

  /*
    Stable identities, and it matters more than it looks. These sit in
    `SplatModel`'s effect dependencies; recreated every render, they made the
    effect re-run on every render of *this* component -- and this component
    re-renders whenever `Home` does, which includes every keystroke in the
    search box. Each re-run called `addSplatScene` again: another multi-megabyte
    download, and a second copy of the scene appended to the same viewer.
  */
  const handleModelLoad = useCallback(() => {
    debugLog("Model loaded");
    setIsLoading(false);
    setError(null);
    setProgress(null);
  }, []);

  const handleModelProgress = useCallback((percent, measurable) => {
    startedRef.current = true;
    setProgress(
      measurable ? Math.max(0, Math.min(100, Math.round(percent))) : null,
    );
  }, []);

  /*
    Progress for the mesh path, which reported none.

    `SplatModel` has always driven `onProgress`; `GLTFModel` never did, so every
    photogrammetry site showed "Loading the model..." with no figure at all --
    on files up to 325 MB. `useProgress` reads three's loading manager, which
    `useGLTF` goes through.

    **These are items, not bytes.** `LoadingManager.onProgress` is
    `(url, itemsLoaded, itemsTotal)`, and for a `.glb` an item is the mesh plus
    one per texture -- 112 of them for Gopinath. So the bar moves in visible
    steps of about a percent, and a number like 97% means "three files are
    still outstanding", not "3% of the data is left". That distinction is the
    whole diagnostic value of it: a stuck percentage names how many *files*
    never came back.

    Read here rather than inside `GLTFModel`: that component suspends on
    `useGLTF`, and a suspended component's hooks do not run, so a progress hook
    inside it would be silent for exactly the period it exists to describe.
  */
  const {
    loaded: itemsLoaded,
    total: itemsTotal,
    item: lastItem,
  } = useProgress();

  /*
    Counters relative to this model, because three's are relative to the page.

    `LoadingManager` only ever increments `itemsLoaded`/`itemsTotal` -- there is
    no reset between loads. So after a 112-item model finished, opening a second
    one showed 112/113 = 99% while its `.glb` downloaded, 100% when it landed,
    then *back* to 64% as its textures registered. The stall message counted the
    same way ("Stalled at 112/113 files").

    The baseline is taken the first time a render sees a new `modelPath`, when
    the counters still hold the previous model's totals. Writing a ref during
    render is safe here: it is idempotent, so StrictMode's double render agrees
    with itself.
  */
  const [baseline, setBaseline] = useState({
    path: modelPath,
    loaded: itemsLoaded,
    total: itemsTotal,
  });
  if (baseline.path !== modelPath) {
    // React's documented "adjusting state when a prop changes": setting state
    // during render re-renders this component immediately, before anything is
    // committed, and no child renders with the stale value. A ref would say the
    // same thing but cannot be touched during render.
    setBaseline({ path: modelPath, loaded: itemsLoaded, total: itemsTotal });
  }
  const relLoaded = Math.max(0, itemsLoaded - baseline.loaded);
  const relTotal = Math.max(0, itemsTotal - baseline.total);

  /*
    "Started" is a load that has *begun*, not a file that has finished.

    `itemEnd` is what moves `itemsLoaded`, and a `.glb` is a single item and the
    first one -- so this said a mesh had not started until it had entirely
    downloaded, and a 200 MB mesh on an ordinary connection was reported as
    "the download never began" at 60s. `itemStart` moves `itemsTotal`, which is
    the event actually being asked about.
  */
  useEffect(() => {
    if (relTotal > 0) startedRef.current = true;
  }, [relTotal]);

  /*
    Derived, not stored. The mesh's percentage is a pure function of what the
    loading manager reports, so putting it in state would mean a second render
    to say the thing the first render already knew -- and `setState` in an
    effect keyed on that value is a cascade the linter is right to refuse.

    The splat path keeps `progress` state because its loader pushes numbers at
    us rather than being asked.
  */
  const meshProgress =
    !isSplatUrl(modelPath) && relTotal > 0
      ? Math.min(100, Math.round((relLoaded / relTotal) * 100))
      : null;
  const shownProgress = isSplatUrl(modelPath) ? progress : meshProgress;

  const handleModelError = useCallback((err) => {
    debugError("Model error:", err);
    /*
      The error's own name as well as its message. A bare `.message` from the
      loaders that fail here is often empty or a bare "Failed to fetch", and the
      constructor name is the part that separates a network failure from a parse
      failure from a transcoder rejection.
    */
    const name = err?.name && err.name !== "Error" ? `${err.name}: ` : "";
    setError(`${name}${err?.message || "load failed"}`);
    setIsLoading(false);
  }, []);

  /*
    A model that never *starts* is a failure; a model that is merely large is
    not.

    Deliberately not a total-time budget. The largest published mesh is 325 MB
    with 895 textures to transcode, and a deadline generous enough for that one
    would be no deadline at all for the rest -- so this only fires when nothing
    has arrived: no progress event, no byte through the loading manager. Once
    the first byte lands the timer is cancelled and the load may take as long as
    it takes.

    Without it a stalled load is indistinguishable from a slow one, forever, and
    the viewer sits on "Loading the model..." with nothing in the console. That
    is the one outcome it should never produce.
  */
  useEffect(() => {
    if (!modelPath || !isLoading) return undefined;
    startedRef.current = false;
    const timer = setTimeout(() => {
      if (startedRef.current) return;
      handleModelError(
        new Error(
          `No data arrived for this model within ${NO_START_TIMEOUT_MS / 1000}s -- ` +
            "the download never began",
        ),
      );
    }, NO_START_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [modelPath, isLoading, handleModelError]);

  /*
    A load that has stopped, as distinct from one that is merely slow.

    The no-start timer above cannot see this: files *are* arriving, so it is
    cancelled, and then the last few never come and the viewer sits on a
    percentage forever. That is what Gopinath does -- 97%, which with 112 items
    means three textures that never came back.

    Reset on every item, so a large model that keeps making progress is never
    interrupted no matter how long it takes in total. It only fires when the
    count has not moved for 90 seconds.

    Not armed once `itemsLoaded === itemsTotal`. Everything has been fetched by
    then and what remains is transcoding and parsing, which report nothing and
    can legitimately take minutes on the 895-texture model. Timing that out
    would be the total-budget mistake wearing a different hat.

    The message names how many files are outstanding and the last one seen,
    because that is the difference between "it broke" and knowing which texture
    to look at.
  */
  useEffect(() => {
    if (!isLoading || !modelPath || isSplatUrl(modelPath)) return undefined;
    // `relLoaded === 0` is the initial `.glb` still downloading: one item, no
    // byte counts, so there is nothing here that can tell slow from stopped and
    // arming would just fail every large mesh on a slow line. The no-start
    // timer covers "nothing ever began"; this covers the texture phase, where
    // the counts do move.
    if (relTotal === 0 || relLoaded === 0) return undefined;
    if (relLoaded >= relTotal) return undefined;

    const timer = setTimeout(() => {
      const outstanding = relTotal - relLoaded;
      const name =
        typeof lastItem === "string" ? lastItem.split("/").pop() : "unknown";
      handleModelError(
        new Error(
          `Stalled at ${relLoaded}/${relTotal} files with ${outstanding} outstanding ` +
            `for ${STALL_TIMEOUT_MS / 1000}s (last seen: ${name})` +
            (hasTranscoderFailed()
              ? ". The texture decoder could not start in this browser -- on Safari " +
                "this is usually the cause. Chrome or Firefox on a desktop will " +
                "work; on iPhone and iPad every browser uses the same engine, so " +
                "switching will not help there."
              : noCompressedTextures
                ? ". This browser reports no compressed-texture support, so each " +
                  "texture needs ~16.8 MB of memory. Enabling hardware " +
                  "acceleration should fix it."
                : ""),
        ),
      );
    }, STALL_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [relLoaded, relTotal, isLoading, modelPath, lastItem, handleModelError]);

  const controlsRef = useRef(null);

  /*
    The model is the thing this project exists to produce, and it could not be
    reached from a keyboard at all.

    `<Canvas>` renders a bare `<canvas>`, which is not focusable and has no
    accessible name -- the `aria-label` was on the element r3f *replaces*. So a
    visitor who cannot use a mouse could open the viewer and then do nothing
    with it: no orbit, no zoom, and a screen reader announcing an unlabelled
    graphic.

    `role="application"` because the arrow keys mean something here other than
    "scroll", which is the one case the role is for.
  */
  const handleCanvasReady = ({ gl }) => {
    const canvas = gl?.domElement;
    if (!canvas) return;
    /*
      Which canvas is the live one.

      `<Canvas>` is keyed on the camera framing, so nearly every change of site
      remounts it, and r3f's unmount deliberately calls `forceContextLoss()` on
      the outgoing renderer half a second later. That fires `webglcontextlost`
      on the *old* canvas, whose listener is closed over the still-mounted
      component -- which by then is showing the next site. The result was a
      "ran out of graphics memory" error thrown over a model that was loading
      perfectly well, half a second after clicking it, which also cleared
      `isLoading` and disarmed both watchdogs.
    */
    currentCanvasRef.current = canvas;
    canvas.tabIndex = 0;
    canvas.setAttribute("role", "application");
    canvas.setAttribute(
      "aria-label",
      siteName ? t("a11y.modelOf", { site: siteName }) : t("a11y.model"),
    );
    canvas.setAttribute("aria-description", t("a11y.modelKeys"));

    /*
      A lost WebGL context, said out loud.

      Nothing watched for this, and it is the failure the heaviest meshes in
      this archive are most likely to produce: the published set runs from 0.7 MB
      to 325 MB, and the largest carries 895 separate 2048x2048 textures --
      roughly 3,750 megapixels to transcode and hold, which is more than a
      laptop GPU will give one page. When the browser drops the context, three's
      next call throws something that reads like a parse error, so the viewer
      blamed the file.

      `preventDefault` marks the loss handled, which is what lets a browser
      restore the context rather than leaving the canvas dead.
    */
    canvas.addEventListener(
      "webglcontextlost",
      (event) => {
        // A deliberate teardown of the previous canvas, not a failure of this one.
        if (currentCanvasRef.current !== canvas) {
          debugLog("Ignoring context loss from a replaced canvas");
          return;
        }
        event.preventDefault();
        debugError("WebGL context lost while showing", siteName || "a model");
        handleModelError(
          new Error(
            "WebGL context lost -- the browser ran out of graphics memory for this model",
          ),
        );
      },
      { once: true },
    );

    debugLog("Canvas initialised");
  };

  /*
    Arrow keys orbit, +/- dolly, Home returns to the framing the run was
    published with. Driven through the controls' own methods rather than by
    moving the camera directly, so the up-axis and the distance limits set below
    still hold.
  */
  const handleCanvasKeyDown = (event) => {
    const controls = controlsRef.current;
    if (!controls) return;

    const STEP = Math.PI / 24;

    /*
      Orbit through the angle setters, not `rotateLeft`/`rotateUp`.

      Those two are module-scope functions inside three-stdlib's
      `OrbitControls`; they are never assigned to the instance. The public
      surface is `connect dispose dollyIn dollyOut domElement getAzimuthalAngle
      getDistance getPolarAngle getScale getZoomScale listenToKeyEvents object
      reset saveState setAzimuthalAngle setPolarAngle setScale
      stopListenToKeyEvents update` -- so every arrow press threw
      `TypeError: controls.rotateLeft is not a function` and the model did not
      move. `+`/`-` worked, because `dollyIn`/`dollyOut` really are public,
      which is why this looked like it worked at all.

      Keyboard orbit is not a nicety here: it is how this viewer is reachable
      without a mouse, and it was signed off as such.

      The polar angle is clamped away from the poles. `setPolarAngle` respects
      `minPolarAngle`/`maxPolarAngle`, which default to 0 and pi, and at exactly
      either the azimuth becomes undefined and the camera flips.
    */
    const EPS = 1e-4;
    const orbit = (dAzimuth, dPolar) => {
      if (dAzimuth) {
        controls.setAzimuthalAngle(controls.getAzimuthalAngle() + dAzimuth);
      }
      if (dPolar) {
        const next = controls.getPolarAngle() + dPolar;
        controls.setPolarAngle(Math.max(EPS, Math.min(Math.PI - EPS, next)));
      }
    };

    const handled = {
      ArrowLeft: () => orbit(-STEP, 0),
      ArrowRight: () => orbit(STEP, 0),
      ArrowUp: () => orbit(0, -STEP),
      ArrowDown: () => orbit(0, STEP),
      "+": () => controls.dollyIn(1.1),
      "=": () => controls.dollyIn(1.1),
      "-": () => controls.dollyOut(1.1),
      Home: () => controls.reset(),
    }[event.key];

    if (!handled) return;
    event.preventDefault();
    // Any deliberate interaction ends the auto-rotation, the same as a drag.
    controls.autoRotate = false;
    handled();
    controls.update();
  };

  /*
    Memoised for the same reason: `vec3` builds a new array each call, and these
    are props on components whose effects depend on them.
  */
  const framing = useMemo(
    () => ({
      position: vec3(camera?.position, [0, 0, 4]),
      target: vec3(camera?.target, [0, 0, 0]),
      // `Run.cam_up`. Y-up is the three.js default and the migration's
      // fallback, but a splat reconstructed from a drone pass is often Z-up,
      // and orbiting the wrong axis makes a model impossible to look at.
      up: vec3(camera?.up, [0, 1, 0]),
      rotation: vec3(camera?.rotation, [0, 0, 0]),
      fov: Number.isFinite(Number(camera?.fov)) ? Number(camera.fov) : 50,
    }),
    [camera],
  );

  /*
    The zoom-out limit has to admit the framing the run was published with.

    `maxDistance` was a flat 100. Across the 81 runs that carry a camera the
    median distance from camera to target is 5.0, so it never mattered -- until
    one run at 241.6, whose hand-tuned framing OrbitControls silently clamped to
    100 the moment it loaded. The operator set it, saved it, and the viewer
    quietly overrode it.

    Derived instead, with headroom to pull back from whatever was published.
  */
  const publishedDistance = useMemo(() => {
    const [px, py, pz] = framing.position;
    const [tx, ty, tz] = framing.target;
    return Math.hypot(px - tx, py - ty, pz - tz);
  }, [framing]);
  const maxOrbitDistance = Math.max(100, publishedDistance * 1.5);
  const { position, target, up, fov, rotation } = framing;

  // Nothing published for this site yet. That is the ordinary case for 2 sites
  // in 3, and it is an invitation to contribute rather than a failure.
  if (resolved && !modelPath) {
    /*
      Three different things, which used to be one message.

      A failed request said "nobody has reconstructed this site yet" -- untrue
      for any site whose detail simply did not arrive, and actively wrong for
      one with five published models. And a site with hundreds of photographs
      and no run was told to contribute more photographs, which was the one
      thing that could not help it (internal design notes).
    */
    const failed = fetchFailed;
    const awaiting = !failed && mapStatus === "awaiting_run";
    const title = failed
      ? t("site.viewerFailed")
      : awaiting
        ? t("site.awaitingRunTitle")
        : t("site.noModelTitle");
    const body = failed
      ? t("site.modelFetchFailedBody")
      : awaiting
        ? t("site.awaitingRunBody")
        : t("site.noModelBody");

    return (
      <div className="model-viewer-container model-viewer-empty">
        <p className="model-viewer-empty-title">{title}</p>
        <p className="model-viewer-empty-body">{body}</p>
        {failed && onRetry && (
          <button type="button" className="btn btn-tertiary" onClick={onRetry}>
            {t("site.retry")}
          </button>
        )}
        {/* Contributing helps a site with too few photographs. It does not help
            one whose request failed, nor one that is only waiting for a run. */}
        {onContributeClick && !failed && !awaiting && (
          <button
            type="button"
            className="btn btn-primary"
            onClick={onContributeClick}
          >
            {t("site.contribute")}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="model-viewer-container">
      {isLoading && !error && (
        <div className="loading-overlay">
          {/*
            A determinate bar once there are bytes to count, and the spinner
            before and after -- the download has a denominator, the parse and
            the sort that follow it do not. `aria-valuenow` is omitted in the
            indeterminate phase, which is how a progress bar says "still
            working, no figure" rather than claiming zero.
          */}
          {shownProgress === null ? (
            <div className="spinner" />
          ) : (
            <div
              className="viewer-progress"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={shownProgress}
              aria-label={t("site.viewerLoading")}
            >
              <div
                className="viewer-progress-fill"
                style={{ width: `${shownProgress}%` }}
              />
            </div>
          )}
          <p>
            {shownProgress === null
              ? t("site.viewerLoading")
              : t("site.viewerLoadingPercent", { percent: shownProgress })}
          </p>
        </div>
      )}
      {error && (
        <div className="error-overlay" role="alert">
          <p className="error-message">{t("site.viewerFailed")}</p>
          {/*
            The headline in the reading language, mapped from the raw text.
            The loader's own words stay below, folded: they are English and
            technical, and still the only thing a visitor can quote to us.
          */}
          <p className="error-explanation">{t(viewerErrorKey(error))}</p>
          {/* The debug "Show Test Model" button lived here, which offered a
              visitor a red cube as a remedy for a failed download. */}
          {/*
            The reason, in production as well as in development.

            It was development-only, so every distinct failure -- a 404, a
            texture the transcoder rejected, a lost WebGL context, a model too
            large for the device -- reached a visitor as the same sentence, and
            reached us as nothing at all. Whoever hits it is the only person who
            can say which one it was, and they cannot say it if we do not show
            it. The path stays behind DEV: it is ours, not theirs, and it is in
            the console line either way.
          */}
          <details className="error-detail">
            <summary>{t("viewer.error.details")}</summary>
            <small>{error}</small>
          </details>
          {import.meta.env.DEV ? <small>{modelPath}</small> : null}
        </div>
      )}
      {onContributeClick && (
        <button
          type="button"
          className="model-contribute-btn btn btn-primary"
          onClick={onContributeClick}
        >
          {t("site.contribute")}
        </button>
      )}
      <Canvas
        // `key` remounts the canvas when the framing changes: r3f reads the
        // `camera` prop once, at creation, so without it the second site keeps
        // the first site's camera.
        key={`${position.join()}|${up.join()}|${fov}`}
        camera={{ position, fov, up }}
        gl={{ antialias: camera?.antialiased !== false, alpha: true }}
        /*
          No inline background. It was a hardcoded light gradient here, which
          sat on top of the identical gradient `.model-viewer-container` already
          declares -- so tokenising the stylesheet changed nothing and the
          viewer stayed light in the dark theme. The canvas is created with
          `alpha: true`, so the container's ground shows through it.
        */
        onCreated={handleCanvasReady}
        onKeyDown={handleCanvasKeyDown}
        aria-label={
          siteName ? t("a11y.modelOf", { site: siteName }) : t("a11y.model")
        }
      >
        <ambientLight intensity={0.8} />
        <directionalLight position={[5, 10, 7]} intensity={1.2} />
        <directionalLight position={[-5, -5, -3]} intensity={0.5} />
        <pointLight position={[10, 10, 10]} intensity={0.5} />

        {/* An empty group while the model streams in; the overlay above the
            canvas carries the loading state. A rotating cube reads as content. */}
        {/*
          The boundary is what catches a .glb that 404s or fails to parse, now
          that `GLTFModel` no longer swallows it. `key` resets it per model:
          without that, one failed site would leave the boundary latched and
          every site opened afterwards would render its fallback.

          The fallback is `null` because this subtree is inside a `<Canvas>` --
          returning a `<div>` here throws "Objects are not valid as a React
          child" from the three.js reconciler. The message belongs to the DOM
          overlay above, which `onError` drives.
        */}
        <ErrorBoundary
          key={modelPath}
          fallback={null}
          onError={handleModelError}
        >
          <React.Suspense fallback={null}>
            <Model
              url={modelPath}
              rotation={rotation}
              onLoadComplete={handleModelLoad}
              onError={handleModelError}
              onProgress={handleModelProgress}
            />
          </React.Suspense>
        </ErrorBoundary>

        <OrbitControls
          ref={controlsRef}
          target={target}
          /*
            Auto-rotation is motion the visitor did not ask for, and it never
            stopped -- it fights every drag. `prefers-reduced-motion` is a
            standing request not to be given exactly this (WCAG 2.3.3), so it is
            honoured, and any interaction ends it for the session.
          */
          autoRotate={!prefersReducedMotion()}
          autoRotateSpeed={1}
          enableZoom
          enablePan
          minDistance={0.5}
          maxDistance={maxOrbitDistance}
          /*
            Orbit about the run's own up-axis. Without this the controls use
            three.js's Y-up regardless of what the Canvas camera was given, so a
            Z-up scan tumbles as you drag -- the camera is placed correctly and
            then becomes uncontrollable, which is worse than being placed wrong.
          */
          onUpdate={(controls) => controls.object.up.fromArray(up)}
          makeDefault
        />
      </Canvas>
    </div>
  );
}

/**
 * Ask before a phone downloads a very large model.
 *
 * Published splats run to 288 MB, and a phone tab that downloads one and then
 * expands it into GPU buffers is a tab the OS kills without a message. On a
 * mobile, low-memory or Save-Data device a model over `LARGE_MODEL_BYTES`
 * waits for a tap instead.
 *
 * The size comes from the API's `model_bytes` where the server provides it.
 * Where it does not (an older API), a constrained device asks the server with
 * a `HEAD` request -- one round trip, and only on the devices that need the
 * answer. No size at all means no prompt: refusing to show a model because a
 * header was missing would be the worse failure.
 *
 * A wrapper, not more state in the canvas component: that component arms
 * "nothing arrived" timers the moment it has a path, and a model waiting on a
 * tap must not be reported as a stalled download.
 */
function ModelViewer3D(props) {
  const { modelPath, modelBytes = null, resolved = true } = props;
  const { t } = useTranslation();
  const [constrained] = useState(() => isConstrainedDevice());
  const [probe, setProbe] = useState({ path: null, bytes: null });
  const [confirmedPath, setConfirmedPath] = useState(null);

  const declared =
    typeof modelBytes === "number" && Number.isFinite(modelBytes)
      ? modelBytes
      : null;
  const probeUrl =
    constrained && resolved && declared === null && probe.path !== modelPath
      ? resolveModelUrl(modelPath)
      : null;
  const probing = Boolean(probeUrl);

  useEffect(() => {
    if (!probeUrl) return undefined;
    const url = probeUrl;
    const controller = new AbortController();
    fetch(url, { method: "HEAD", signal: controller.signal, credentials: "same-origin" })
      .then((response) => {
        const length = Number(response.headers?.get?.("content-length"));
        setProbe({
          path: modelPath,
          bytes: response.ok && length > 0 ? length : null,
        });
      })
      .catch((err) => {
        if (err?.name === "AbortError") return;
        setProbe({ path: modelPath, bytes: null });
      });
    return () => controller.abort();
  }, [probeUrl, modelPath]);

  if (probing) {
    return (
      <div className="model-viewer-container">
        <div className="loading-overlay">
          <div className="spinner" />
          <p>{t("site.viewerLoading")}</p>
        </div>
      </div>
    );
  }

  const bytes = declared ?? (probe.path === modelPath ? probe.bytes : null);
  const mustConfirm =
    constrained &&
    Boolean(modelPath) &&
    typeof bytes === "number" &&
    bytes > LARGE_MODEL_BYTES &&
    confirmedPath !== modelPath;

  if (mustConfirm) {
    return (
      <div
        className="model-viewer-container model-viewer-empty model-viewer-confirm"
        role="group"
        aria-labelledby="model-size-confirm-title"
      >
        <p id="model-size-confirm-title" className="model-viewer-empty-title">
          {t("viewer.largeModel.title", { size: toMegabytes(bytes) })}
        </p>
        <p className="model-viewer-empty-body">{t("viewer.largeModel.body")}</p>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => setConfirmedPath(modelPath)}
        >
          {t("viewer.largeModel.load")}
        </button>
        {props.onContributeClick && (
          <button
            type="button"
            className="btn btn-tertiary"
            onClick={props.onContributeClick}
          >
            {t("site.contribute")}
          </button>
        )}
      </div>
    );
  }

  return <ModelViewerCanvas {...props} />;
}

export default ModelViewer3D;
