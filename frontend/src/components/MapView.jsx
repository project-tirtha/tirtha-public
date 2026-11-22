import React, { useState, useEffect, useMemo, useRef } from 'react';
import Icon from './Icon';
import LanguageSwitcher from './LanguageSwitcher';
import AccountControl from './AccountControl';
/*
  A namespace import: MapLibre 6 is ESM-only and has no default export, so
  `import maplibregl from 'maplibre-gl'` resolves to undefined there.
*/
import * as maplibregl from 'maplibre-gl';
/*
  MapLibre 6 ships its worker as a separate module file and, left alone, looks
  for it next to `import.meta.url` -- i.e. `assets/maplibre-gl-worker.mjs`
  beside our hashed chunk, which Vite never emits. Every tile would then fail
  to parse and the map would stay blank. `?worker&url` makes Vite bundle the
  worker as its own hashed `.js` asset (a `.js` extension, so nginx and
  Django's static handler serve it with a JavaScript MIME type, which module
  workers require) and hands back its URL under the configured `base`.

  That URL is NOT handed to MapLibre directly -- see `utils/maplibreWorker.js`.
*/
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import { Trans, useTranslation } from 'react-i18next';
import 'maplibre-gl/dist/maplibre-gl.css';
import '../styles/MapView.css';
import SearchBar from './SearchBar';
import { announce } from '../utils/announce';
import { getReadableLocationName, reverseGeocode } from '../utils/geocoding';
import { motionDuration } from '../utils/motion';
import { readStored, writeStored } from '../utils/storage';
import { useTheme } from '../hooks/useTheme';
import { maplibreWorkerBootstrap } from '../utils/maplibreWorker';
import { displayConfig } from '../utils/deployment';
import {
  buildMarkerDataUrl,
  completionStatus,
  markerFor,
  MARKER_COLOURS,
} from '../utils/siteStatus';

/*
  Popups are built from DOM nodes and handed over with `setDOMContent`, never
  `setHTML`. Site names, locations, thumbnail URLs and geocoder results all come
  from the server or a third party, and `setHTML` routes its string through
  MapLibre's `DOM.sanitize()` -- the exact surface of GHSA-jrc7-96c5-q579. With
  `textContent` and property assignment there is no markup to parse, so neither
  a sanitizer bypass nor a missed escape can turn data into script.
*/
function makeNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** The hover card for a site pin: picture (if any), name and place. */
function buildTooltipContent(templeData, animatedPreviews) {
  const card = makeNode('div', 'temple-tooltip');
  const thumb = (tag, src) => {
    const node = makeNode(tag, 'tooltip-thumb');
    node.src = src;
    node.width = 72;
    node.height = 72;
    if (tag === 'img') node.alt = '';
    return node;
  };
  if (animatedPreviews && templeData.thumbnailTurntable) {
    if (templeData.thumbnailTurntableKind === 'image') {
      card.appendChild(thumb('img', templeData.thumbnailTurntable));
    } else {
      const video = thumb('video', templeData.thumbnailTurntable);
      if (templeData.thumbnailPreview) video.poster = templeData.thumbnailPreview;
      video.autoplay = true;
      video.muted = true;
      video.loop = true;
      video.playsInline = true;
      video.preload = 'none';
      // `muted` as a property does not set the attribute, and autoplay
      // policies read the attribute.
      video.setAttribute('muted', '');
      card.appendChild(video);
    }
  } else if (templeData.thumbnailPreview) {
    const img = thumb('img', templeData.thumbnailPreview);
    img.loading = 'lazy';
    card.appendChild(img);
  }
  const text = makeNode('span', 'tooltip-text');
  text.appendChild(makeNode('span', 'tooltip-temple-name', templeData.name || ''));
  text.appendChild(makeNode('span', 'tooltip-location', templeData.location || ''));
  card.appendChild(text);
  return card;
}

/** The label over a selected custom location. */
function buildSelectedLabel(displayName) {
  const node = makeNode('div', null, displayName);
  node.style.fontWeight = '600';
  return node;
}

/**
 * The basemaps, named for what they actually are.
 *
 * Every one of these is an OpenFreeMap style over OpenStreetMap data. Three of
 * them were labelled "Google Roadmap", "Google Satellite" and "Google Hybrid" --
 * so the menu offered satellite imagery and returned a pale vector map, and
 * credited Google for work that is OpenStreetMap's. The attribution line was
 * right underneath saying so.
 *
 * There is no satellite imagery here at all. Adding it means a keyed provider
 * (internal design notes, tile-usage policy), which is a decision rather than a rename.
 *
 * Three, not four. The fourth was `bright`, offered as "Detailed, bright" beside
 * `liberty`'s "Detailed" -- but both are full street styles on the same #f8f4f0
 * background (119 layers and 111), so the pair answered "do I get more of the
 * map?" twice, from the same side of the light/dark axis. They differ only in
 * cartographic lineage, which is exactly the thing the selector below refuses to
 * name.
 *
 * The obvious repair -- make that fourth slot the detailed *dark* map, so both
 * axes are covered -- is not available. OpenFreeMap publishes exactly liberty,
 * bright, positron, dark and fiord, and both of the dark ones are minimal: dark
 * is 47 layers and fiord 48, against positron's 55. There is no detailed dark
 * basemap to point at, so the button is dropped rather than filled with a second
 * copy of an answer already given. Revisit if OpenFreeMap ships one.
 *
 * Ordered minimal -> detailed with the default first, because `Object.keys` here
 * is the button order in the selector.
 *
 * Only `styleUrl` is read. Each entry also carried a `name` ('Streets',
 * 'Bright', 'Light', 'Dark') that nothing rendered and that contradicted the
 * labels that are rendered -- those live in `map.styles.*` in the locale files,
 * and a second set of names no test or screen can see is a trap, not a comment.
 * Each also carried an `attribution` string, four identical copies of one never
 * read: the credit line is markup in the footer below.
 */
// Started from a blob, not the asset URL: see utils/maplibreWorker.js.
maplibregl.setWorkerUrl(maplibreWorkerBootstrap(maplibreWorkerUrl));

const MAP_STYLES = {
  positron: { styleUrl: 'https://tiles.openfreemap.org/styles/positron' },
  dark: { styleUrl: 'https://tiles.openfreemap.org/styles/dark' },
  liberty: { styleUrl: 'https://tiles.openfreemap.org/styles/liberty' }
};

const getMapStyle = (styleKey) => {
  return MAP_STYLES[styleKey].styleUrl;
};



/**
 * A site's pin.
 *
 * A `<button>`, not a `<div>`. It is a control -- clicking it opens the site --
 * and a div with a click handler is unreachable by keyboard, has no accessible
 * name and is invisible to a screen reader (WCAG 2.1.1, 4.1.2). The visitor who
 * cannot use a mouse currently cannot reach a single site on this map.
 *
 * The caller sets the name, because only it knows which site this is.
 */
/**
 * `map.getSource(id)`, but null-safe before the style has loaded.
 *
 * MapLibre's `getSource` is `this.style.getSource(id)`, and `map.style` is null
 * until the style document arrives -- so calling it early throws
 * "Cannot read properties of null (reading 'getSource')" rather than returning
 * undefined. Several call sites assumed the latter.
 *
 * That is a race, not a theoretical one: the style is a cross-origin fetch to a
 * tile CDN, and the sites API is a same-origin request that routinely wins. The
 * throw happened inside a React effect, so the error boundary caught it and the
 * **entire app rendered nothing** -- a blank page whenever the CDN was slower
 * than our own API, and every time it was unreachable.
 */
function sourceOf(map, id) {
  // `try` rather than an `isStyleLoaded()` gate. Both stop the throw, but the
  // gate also reports "no source" for a source that exists and is perfectly
  // usable -- `isStyleLoaded()` goes false again whenever tiles are in flight,
  // which is most of the time on a slow connection.
  try {
    return map?.getSource?.(id) ?? null;
  } catch {
    return null;
  }
}

/**
 * The same treatment for layers, and for the same reason.
 *
 * `map.getLayer(id)` reaches through `map.style`, which is null both before the
 * style has loaded and *after the WebGL context is lost* -- so this threw
 * "Cannot read properties of null (reading 'getLayer')" from inside a marker
 * update, which is inside an effect, which the error boundary catches by
 * blanking the app.
 */
function layerOf(map, id) {
  try {
    return map?.getLayer?.(id) ?? null;
  } catch {
    return null;
  }
}

function createMarkerElement(color, status = null) {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = 'custom-marker';
  el.style.width = '25px';
  el.style.height = '41px';
  el.style.padding = '0';
  el.style.border = 'none';
  // One assignment each. `background` shorthand was set *after*
  // `background-image` and then `background-image` again after that -- three
  // writes where the middle one silently cleared the first.
  el.style.background = `url(${buildMarkerDataUrl(color, status)}) no-repeat`;
  el.style.backgroundSize = 'contain';
  el.style.filter = 'drop-shadow(0 3px 6px rgba(0,0,0,0.35))';
  el.style.cursor = 'pointer';
  return el;
}

function MapView({
  templeList = [],
  onMarkerClick,
  onSearchSelect,
  showMapSearch = true,
  onMapClick,
  isSelectingLocation = false,
  selectedPosition = null,
  onSelectionPositionChange,
  onConfirmLocation,
  onCancelLocationSelection,
  searchTarget,
  selectedTemple,
  onAboutClick,
  onPrivacyClick,
  searchQuery,
  setSearchQuery,
  isSearchFocused,
  setIsSearchFocused,
  searchActiveIndex,
  setSearchActiveIndex,
}) {
  const { t } = useTranslation();
  /*
    `t` held in a ref because the markers are built imperatively, inside effects
    that must not re-run when the language changes -- re-running them tears down
    and rebuilds every marker on the map. The ref keeps the current translator
    available to those effects without becoming a dependency of them.
  */
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);
  const [indiaBoundary, setIndiaBoundary] = useState(null);
  /*
    The basemap is a locator, not the subject (internal design notes).

    This defaulted to `liberty` — the full OpenFreeMap vector style, every road,
    POI and label in the region, in every script. At country zoom that is
    megabytes of tiles and, worse, visually louder than the pins it exists to
    support: the eye lands on "Bangladesh" and the trunk roads before it finds a
    green marker. `positron` is the minimal style, built precisely as a backdrop
    for an overlay. Anyone who wants streets is one click away in the selector,
    which is what the selector is for.

    **Follows the interface theme until you choose a basemap, then stops.**

    It was tied to the theme once, then untied, and the note that replaced it
    said the two are different questions: the theme is about reading text, the
    basemap is about seeing markers, and a dark map makes a pale green pin
    harder to find, not easier. That reasoning is still right, and it is why
    this is not simply re-tied -- but it answered a question nobody asked. The
    complaint is a bright white map filling a window someone has deliberately
    made dark, and "the map has its own control" is only an answer if you know
    the control is there.

    So: unchosen, the basemap follows the theme -- `dark` in dark mode,
    `positron` in light. Chosen, it is pinned and the theme stops moving it,
    because a visitor who picked Detailed and then dimmed the room has not asked
    for a different map. Following is the default because it is the state a
    first visit is in, and a pin is an explicit act.

    The pin lives under a *new* key. `tirtha.mapStyle` cannot be reused: the old
    effect wrote it on every mount, so nearly every returning visitor has a
    value stored there that they never chose, and reading it as a pin would mean
    the theme moved the map for nobody.

    The cost is real and measured: on the dark ground the marker fills fall to
    2.2-3.9:1, so the four statuses are harder to tell apart by hue. They stay
    locatable by their white outline. If that proves too dear, the fix is a
    dark-mode `MARKER_COLOURS` variant rather than reverting this -- the pins
    are the thing to adjust, since they are what the basemap exists to support.
  */
  const { theme } = useTheme();
  const [pinnedStyle, setPinnedStyle] = useState(() => {
    const stored = readStored('tirtha.mapStyle.pinned');
    return stored && MAP_STYLES[stored] ? stored : null;
  });
  const mapStyle = pinnedStyle ?? (theme === 'dark' ? 'dark' : 'positron');

  // Choosing is what pins. Nothing writes here on mount, which is the mistake
  // the old key carries and the reason it is not reused.
  const setMapStyle = (key) => {
    setPinnedStyle(key);
    writeStored('tirtha.mapStyle.pinned', key);
  };

  /*
    Was there a camera in the URL when this page was opened?

    Read once, at mount, and never re-read: MapLibre starts writing the
    fragment as soon as the map exists, so anything asking later always sees
    one and could never tell a restored arrival from a fresh one.
  */
  const hashCameraRef = useRef(
    typeof window !== 'undefined' && /^#map=/.test(window.location.hash),
  );
  const mapContainerRef = useRef(null);
  const mapRef = useRef(null);
  const markersRef = useRef({});

  // Keep latest handlers in refs to avoid closure capturing stale state
  const onMarkerClickRef = useRef(onMarkerClick);
  const onMapClickRef = useRef(onMapClick);
  const isSelectingLocationRef = useRef(isSelectingLocation);
  const onSelectionPositionChangeRef = useRef(onSelectionPositionChange);

  useEffect(() => {
    onMarkerClickRef.current = onMarkerClick;
    onMapClickRef.current = onMapClick;
    isSelectingLocationRef.current = isSelectingLocation;
    onSelectionPositionChangeRef.current = onSelectionPositionChange;
  }, [onMarkerClick, onMapClick, isSelectingLocation, onSelectionPositionChange]);

  const indiaBoundaryRef = useRef(indiaBoundary);

  useEffect(() => {
    indiaBoundaryRef.current = indiaBoundary;
  }, [indiaBoundary]);

  const center = [78.9629, 20.5937]; // LngLat format
  const zoom = 5;

  // Fetch boundary data
  useEffect(() => {
    const controller = new AbortController();

    const fetchIndiaBoundary = async () => {
      try {
        /*
          `import.meta.env.BASE_URL`, not a bare `/India/...`.

          The bundle is served from `/project/tirtha/static/` (Vite's `base`),
          not from the domain root, so the absolute path resolved to
          `https://smlab.niser.ac.in/India/INDIA_STATES.geojson` -- a 404 on
          every real deployment. The state outlines simply never drew, and the
          `catch` reported it only in a dev build, where the path happened to be
          right.
        */
        const response = await fetch(
          `${import.meta.env.BASE_URL}India/INDIA_STATES.geojson`,
          { signal: controller.signal },
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        setIndiaBoundary({ type: 'FeatureCollection', features: data.features });
      } catch (error) {
        if (error?.name === 'AbortError') return;
        if (import.meta.env.DEV) {
          console.error('Could not load India boundary:', error);
        }
        setIndiaBoundary(null);
      }
    };

    fetchIndiaBoundary();

    return () => {
      controller.abort();
    };
  }, []);

  /*
    The sites by id. `updateMarkers` runs from imperative MapLibre callbacks
    that must not re-subscribe when the list changes, so it reads the current
    value through a ref rather than closing over one.
  */
  const templeById = useMemo(
    () => new Map(templeList.map((site) => [site.id, site])),
    [templeList],
  );
  const templeByIdRef = useRef(templeById);
  useEffect(() => {
    templeByIdRef.current = templeById;
  }, [templeById]);

  const templeFeatures = useMemo(() => {
    /*
      Only the sites that have somewhere to be drawn. The service keeps
      unlocated sites in the record now -- the panel lists them and their deep
      links work -- but a site with `lat: null` has no place on a map, and a
      NaN reaching MapLibre is a marker in the Gulf of Guinea.
    */
    return templeList.filter((temple) => temple.placed !== false).map((temple) => {
      // No offset. Co-located sites used to be fanned out on a ~13 m spiral,
      // which silently moved a pin away from where the site actually is -- on a
      // map whose whole purpose is to say where things are. The source clusters
      // (`cluster: true` below), which is the honest way to show several things
      // in one place.
      const status = completionStatus(temple);

      return {
        type: 'Feature',
        geometry: {
          type: 'Point',
          coordinates: [temple.lng, temple.lat],
        },
        /*
          **Only what the map itself needs**, and an id to find the rest by.

          This used to serialise a hand-picked subset of the site into
          `templeData` and `JSON.parse` it back on click. The subset was written
          once and then fell behind the record it was copying: it lost `slug`,
          so `openTempleContribute` got `siteSlug: null` and the upload button
          was permanently disabled for any site reached by clicking its pin --
          which is how almost everyone reaches one. It lost `descriptionHtml`,
          so the 27 sites with markup in their description rendered it as text
          on that path, while the same site opened from a shared link rendered
          correctly.

          Both were invisible in testing that starts from a URL, and both are
          the same bug: a second, lossy copy of a record that already exists.
          The full site is in `templeById`; the feature carries an id.
        */
        properties: {
          id: temple.id,
          completionStatus: status,
        },
      };
    });
  }, [templeList]);

  const templeFeaturesRef = useRef(templeFeatures);

  useEffect(() => {
    templeFeaturesRef.current = templeFeatures;
  }, [templeFeatures]);

  // Create Cluster SVG Marker
  const createClusterHTMLElement = (count, counts) => {
    const size = 40;
    const radius = 16;
    const cx = size / 2;
    const cy = size / 2;

    const getPieSlice = (startAngle, endAngle, color) => {
      const deltaAngle = endAngle - startAngle;
      if (Math.abs(deltaAngle - 360) < 0.01 || Math.abs(deltaAngle) >= 360) {
        return `<circle cx="${cx}" cy="${cy}" r="${radius}" fill="${color}" stroke="white" stroke-width="1"/>`;
      }

      const start = startAngle * (Math.PI / 180);
      const end = endAngle * (Math.PI / 180);
      const x1 = cx + radius * Math.cos(start);
      const y1 = cy + radius * Math.sin(start);
      const x2 = cx + radius * Math.cos(end);
      const y2 = cy + radius * Math.sin(end);
      const largeArc = endAngle - startAngle > 180 ? 1 : 0;
      const path = `M ${cx} ${cy} L ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2} Z`;
      return `<path d="${path}" fill="${color}" stroke="white" stroke-width="1"/>`;
    };

    /*
      One slice per status, in a fixed order, with no remainder.

      The old arithmetic derived the last slice as `100 - complete - partial`,
      so every site whose status this function did not know about was silently
      drawn as "needs images". Summing the counts it actually has means an
      uncounted status makes the ring *incomplete* -- visibly wrong -- rather
      than confidently mislabelled.

      The ring uses `MARKER_COLOURS`, not a palette of its own. It had #27ae60,
      #f39c12 and #3498db -- a different green, amber and blue from the pins it
      summarises and from the legend that explains them. So the key said "green
      means a model is available" and the ring's green was a different green,
      which is the one thing a key must not be wrong about.
    */
    const slices = [
      [counts.published, MARKER_COLOURS.published],
      [counts.in_progress, MARKER_COLOURS.in_progress],
      [counts.awaiting_run, MARKER_COLOURS.awaiting_run],
      [counts.needs_images, MARKER_COLOURS.needs_images],
    ].filter(([n]) => n > 0);

    const total = slices.reduce((sum, [n]) => sum + n, 0);

    let svgPaths = '';
    let currentAngle = -90; // Start at top

    slices.forEach(([n, colour], index) => {
      // The last slice closes the circle exactly, rather than landing a
      // fraction of a degree short and leaving a hairline of background.
      const end = index === slices.length - 1 ? 270 : currentAngle + (n / total) * 360;
      svgPaths += getPieSlice(currentAngle, end, colour);
      currentAngle = end;
    });

    const centerCircle = `<circle cx="${cx}" cy="${cy}" r="10" fill="white" stroke="#e0e0e0" stroke-width="1"/>`;
    const countText = `<text x="${cx}" y="${cy + 0.5}" text-anchor="middle" dominant-baseline="central" alignment-baseline="central" font-size="13" font-weight="bold" fill="#333" font-family="sans-serif">${count}</text>`;

    // A button, for the same reason a site pin is: clicking a cluster zooms
    // the map, so it is a control. The pie is decorative -- the count is
    // rendered as text and the breakdown goes in the accessible name, so the
    // colours add emphasis to information that is already readable without
    // them (WCAG 1.4.1).
    const div = document.createElement('button');
    div.type = 'button';
    div.className = 'custom-cluster-icon-pie';
    div.setAttribute(
      'aria-label',
      /*
        `models` was `count` -- the number of *sites* in the cluster. The label
        reads "12 sites, 8 with models, 12 models total", so it claimed a model
        for every site including the ones with none. The count the ring is built
        from is what it should say.

        Through `tRef`, like the site pin below: clusters are only ever built
        from `updateMarkers`, which the mount-only map effect handed to the
        `move`/`zoom`/`idle` listeners once and for good. A bare `t` here is the
        translator from that first render, so every cluster formed after a
        language switch announced itself in the page's original language.
      */
      /*
        `count`, because that is the name i18next pluralises on -- the heading
        beside this one read "1 sites on the map" for the same reason.

        The third clause is gone. It said "{{models}} models in total" and was
        fed the same number as `published`, so a cluster announced "2 sites, 1
        with models, 1 models in total" -- the same figure twice, once
        ungrammatically. Before that it was fed the *site* count, claiming a
        model for every site including the ones with none.
      */
      tRef.current('map.cluster.label', {
        count,
        published: counts.published,
      }),
    );
    div.innerHTML = `
      <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" style="filter: drop-shadow(0 2px 4px rgba(0,0,0,0.2))">
        ${svgPaths}
        ${centerCircle}
        ${countText}
      </svg>
    `;
    return div;
  };

  /*
    Clusters the visitor has just opened, which the map is still drawing.

    MapLibre keeps the previous tile's features rendered while the new
    clustering is computed, so `queryRenderedFeatures` goes on reporting a
    cluster for a second or two after the zoom has already split it. Removing
    the marker on click is not enough on its own: the next `move` sees the
    cluster still reported and faithfully builds it again. Measured before the
    fix -- click the 11-pin and both "11" and the new "10" sit on the map
    together from about 600ms to 2.5s.

    Cleared on `idle`, which is the event that means "the map has finished
    settling" -- so the suppression lasts exactly as long as the staleness does,
    and a cluster that legitimately survives the zoom comes straight back.
  */
  const openedClustersRef = useRef(new Set());
  /*
    Whether anything has asked for a particular camera position.

    Set by the `searchTarget` effect -- a deep link, an ARK, a marker click, a
    search result. The India boundary's `fitBounds` checks it so a slow 1 MB
    download cannot undo a camera move the visitor actually asked for.
  */
  const cameraClaimedRef = useRef(false);
  /*
    The popup and pending hover timer belonging to each marker, by marker id.

    Both the tooltip and the cluster sheet are standalone popups added to the
    map, and the only thing that removed them was a `mouseleave` on the marker
    element. Removing a hovered element does **not** dispatch `mouseleave`, so
    hovering a cluster and then clicking it left the sheet pinned over the new
    view -- and `.tooltip-thumb` carries `pointer-events: none`, which makes an
    orphaned tooltip impossible to dismiss until the whole map unmounts.

    Held here so the cleanup pass that removes a marker can take its popup and
    its timer with it.
  */
  const markerPopupsRef = useRef({});

  const updateMarkers = (map) => {
    if (!map || !layerOf(map, 'temples-invisible')) return;

    const features = map.queryRenderedFeatures({ layers: ['temples-invisible'] });
    const currentIds = new Set();

    features.forEach((feature) => {
      const coords = feature.geometry.coordinates;
      const [lng, lat] = coords;

      if (feature.properties.cluster) {
        const clusterId = feature.properties.cluster_id;
        const id = `cluster-${clusterId}`;
        // Still being drawn from the old tile; not added to `currentIds`, so
        // the cleanup pass below takes it away rather than keeping it alive.
        if (openedClustersRef.current.has(clusterId)) return;
        currentIds.add(id);

        if (!markersRef.current[id]) {
          const count = feature.properties.point_count;
          const counts = {
            published: feature.properties.completeCount || 0,
            in_progress: feature.properties.partialCount || 0,
            needs_images: feature.properties.incompleteCount || 0,
            awaiting_run: feature.properties.awaitingCount || 0,
          };

          const el = createClusterHTMLElement(count, counts);

          /*
            Clicking a cluster shows everything inside it, in one move.

            Two bugs here, in order. It first passed a callback --
            `getClusterExpansionZoom(id, (err, zoom) => ...)`, the MapLibre 3
            signature -- and since v4 the method returns a Promise and ignores a
            second argument, so nothing happened at all, silently.

            Then, with that fixed, it barely moved: expansion zoom is the level
            at which the cluster *begins* to break up, which for an eleven-pin
            cluster is often one step that yields a ten and a one. The visitor
            clicks, the map lurches, and the cluster is still there.

            So it frames the members instead. `getClusterLeaves` gives every
            site inside; `fitBounds` puts all of them on screen at once, which
            is what the click was always asking for.
          */
          el.addEventListener('click', async () => {
            const source = sourceOf(map, 'temples');
            if (!source) return;

            /*
              Take this pin off the map now, rather than waiting for the next
              `updateMarkers` to decide it is gone.

              MapLibre keeps the previous tile's features rendered while the new
              clustering is computed, so `queryRenderedFeatures` goes on
              reporting this cluster for a second or two after the zoom has
              already split it -- and the cleanup pass, correctly, keeps any
              marker the map still reports. Measured: click the 11-pin and both
              "11" and the new "10" sit on the map together from about 600ms to
              2.5s.

              Removing it here is safe in both directions: if the expansion
              fails below, the cluster is still in the source, so the very next
              `updateMarkers` builds it again.
            */
            openedClustersRef.current.add(clusterId);
            markersRef.current[id]?.remove();
            delete markersRef.current[id];
            // Its hover sheet with it: the pointer is still over the cluster
            // being removed, so no `mouseleave` will ever fire to dismiss it,
            // and it would sit pinned over the zoomed-in view.
            const openSheetPopup = markerPopupsRef.current[id];
            if (openSheetPopup) {
              window.clearTimeout(openSheetPopup.timer);
              openSheetPopup.popup?.remove();
              delete markerPopupsRef.current[id];
            }

            /*
              The button that was just activated is now gone from the document,
              so focus falls to `<body>` -- a keyboard user's next Tab restarts
              from the top of the page, and nothing says the map moved. Focus
              goes to the map, which is the thing that changed, and the change
              is announced.
            */
            map.getContainer?.()?.focus?.();
            announce(tRef.current('map.cluster.opened', { count }));
            try {
              const leaves = await source.getClusterLeaves(clusterId, Infinity, 0);
              const points = leaves
                .map((leaf) => leaf.geometry?.coordinates)
                .filter((c) => Array.isArray(c) && Number.isFinite(c[0]));

              if (points.length < 2) return;

              const lngs = points.map((c) => c[0]);
              const lats = points.map((c) => c[1]);
              const spanLng = Math.max(...lngs) - Math.min(...lngs);
              const spanLat = Math.max(...lats) - Math.min(...lats);

              /*
                Co-located sites. Two temples in one compound share a coordinate
                to five decimal places, so their bounding box is a point and
                `fitBounds` would zoom to the maximum and still show one pin on
                top of another. There is nothing to separate, so this goes just
                past `clusterMaxZoom` -- the cluster dissolves and the pins are
                drawn where they actually are, which is the honest answer even
                though they overlap.
              */
              if (spanLng < 1e-6 && spanLat < 1e-6) {
                map.flyTo({ center: [lng, lat], zoom: 17, duration: motionDuration(500) });
                return;
              }

              map.fitBounds(
                [
                  [Math.min(...lngs), Math.min(...lats)],
                  [Math.max(...lngs), Math.max(...lats)],
                ],
                {
                  // Room for the pin above its point, and for the panel and the
                  // floating controls around the edges.
                  padding: { top: 90, bottom: 60, left: 60, right: 60 },
                  maxZoom: 17,
                  duration: motionDuration(600),
                },
              );
            } catch {
              // The cluster left the source between the click and the answer
              // (a pan, a data refresh). Nothing to frame.
            }
          });

          /*
            What is inside the cluster, as pictures.

            Not the old label -- that read "a group of 14 sites, 9 with a model"
            while the pin already showed 14 and its ring already showed the
            split, so it repeated the thing it covered. This answers the
            question the pin cannot: *which* monuments are under here. Each tile
            opens its own site, so a cluster is no longer a thing you have to
            zoom into before you can act on it.

            Delayed, because a pointer crossing a map full of pins would
            otherwise fire a leaves query for every one it grazed.
          */
          const sheetPopup = new maplibregl.Popup({
            closeButton: false,
            closeOnClick: false,
            offset: 22,
            maxWidth: '320px',
            className: 'cluster-sheet-popup',
            /*
              MapLibre focuses the first focusable element in a popup when it
              opens, and defaults to doing so. Here that is the first tile,
              which then matches `:focus-visible` and wears the 3px ring
              `index.css` gives every focused control -- a blue bar across the
              first thumbnail, appearing on hover, for no reason the visitor
              can see. It came and went because `:focus-visible` only paints
              when the browser judges the interaction keyboard-like.

              That default is for a popup somebody opened. This one opens
              because a pointer passed over a pin, so taking focus is wrong
              twice over: it marks a tile nobody chose, and it moves focus out
              of whatever the visitor was actually using.

              The tiles stay reachable: they are real buttons, so Tab reaches
              them while the sheet is open, and clicking the cluster zooms to
              its contents.
            */
            focusAfterOpen: false,
          });
          let sheetTimer = null;

          const openSheet = async () => {
            const source = sourceOf(map, 'temples');
            if (!source) return;
            let leaves;
            try {
              leaves = await source.getClusterLeaves(clusterId, 12, 0);
            } catch {
              return;
            }
            const sites = leaves
              .map((leaf) => templeByIdRef.current.get(leaf.properties?.id))
              .filter(Boolean);
            if (!sites.length) return;

            const sheet = document.createElement('div');
            sheet.className = 'cluster-sheet';
            const heading = document.createElement('p');
            heading.className = 'cluster-sheet-heading';
            heading.textContent = tRef.current('map.cluster.contains', { count });
            sheet.appendChild(heading);

            const grid = document.createElement('div');
            grid.className = 'cluster-sheet-grid';
            for (const site of sites) {
              const tile = document.createElement('button');
              tile.type = 'button';
              tile.className = 'cluster-sheet-tile';
              tile.setAttribute('aria-label', site.name);
              if (site.thumbnailPreview) {
                const img = document.createElement('img');
                img.src = site.thumbnailPreview;
                img.alt = '';
                img.loading = 'lazy';
                tile.appendChild(img);
              } else {
                const { src } = markerFor(site);
                const pin = document.createElement('img');
                pin.src = src;
                pin.alt = '';
                pin.className = 'cluster-sheet-pin';
                tile.appendChild(pin);
              }
              const label = document.createElement('span');
              label.textContent = site.name;
              tile.appendChild(label);
              tile.addEventListener('click', (event) => {
                event.stopPropagation();
                sheetPopup.remove();
                onMarkerClickRef.current?.(site);
              });
              grid.appendChild(tile);
            }
            sheet.appendChild(grid);

            // The sheet has to stay open while the pointer is inside it, or the
            // tiles are unclickable.
            sheet.addEventListener('mouseleave', () => sheetPopup.remove());

            sheetPopup.setLngLat([lng, lat]).setDOMContent(sheet).addTo(map);
          };

          markerPopupsRef.current[id] = { popup: sheetPopup, timer: null };

          el.addEventListener('mouseenter', () => {
            sheetTimer = window.setTimeout(openSheet, 260);
            markerPopupsRef.current[id] = { popup: sheetPopup, timer: sheetTimer };
          });
          el.addEventListener('mouseleave', () => {
            window.clearTimeout(sheetTimer);
            // Not removed immediately: the pointer has to be able to travel
            // from the pin onto the sheet, and they do not touch.
            sheetTimer = window.setTimeout(() => {
              if (!sheetPopup.getElement()?.matches(':hover')) sheetPopup.remove();
            }, 220);
            markerPopupsRef.current[id] = { popup: sheetPopup, timer: sheetTimer };
          });
          markersRef.current[id] = new maplibregl.Marker({ element: el })
            .setLngLat([lng, lat])
            .addTo(map);
        }
      } else {
        // The whole site, not the subset that used to be copied into the
        // feature. A pin whose site has since left the list is skipped rather
        // than rendered from stale properties.
        const templeData = templeByIdRef.current.get(feature.properties.id);
        if (!templeData) return;
        const id = `temple-${templeData.id}`;
        currentIds.add(id);

        if (!markersRef.current[id]) {
          const status = MARKER_COLOURS[feature.properties.completionStatus]
            ? feature.properties.completionStatus
            : 'needs_images';
          const el = createMarkerElement(MARKER_COLOURS[status], status);
          // The name a screen reader reads. One of three channels carrying the
          // status: this, the pin's glyph, and its colour (WCAG 1.4.1).
          el.setAttribute(
            'aria-label',
            `${templeData.name}, ${tRef.current(`map.status.${status}`)}`,
          );
          el.title = templeData.name;

          el.addEventListener('click', (e) => {
            e.stopPropagation();
            if (onMarkerClickRef.current) {
              onMarkerClickRef.current(templeData);
            }
          });

          const tooltipPopup = new maplibregl.Popup({
            closeButton: false,
            closeOnClick: false,
            offset: [0, -34],
            className: 'custom-tooltip'
          });

          el.addEventListener('mouseenter', () => {
            /*
              Name and place. It used to also render `templeData.gifUrl` — a
              field no site has ever had, left from the Ram Mandir tooltip —
              with an inline `onerror` to hide the resulting broken image. An
              inline handler needs `script-src 'unsafe-inline'`, which this app
              does not allow, so the fallback could not have run either.
            */
            /*
              The picture, where there is one. `thumbnail_preview` is the 160px
              WebP derivative; the full thumbnail is ~420 KB and hovering a map
              covered in pins would pull megabytes of it. No `<img>` at all
              rather than a broken one when the derivative has not been
              generated for this site yet.
            */
            /*
              The turntable, where the model has one: 17 KB of H.264 showing
              what this project actually made, rather than a photograph of the
              temple somebody else took. The still stays as the `poster`, so
              the tile is never empty while the video arrives and degrades to
              the picture if it cannot play at all.

              `preload="none"`: hovering across a map of pins would otherwise
              start a fetch for each one grazed. The browser begins loading
              when the element is shown, which is inside this handler.
            */
            tooltipPopup.setLngLat([lng, lat])
              .setDOMContent(buildTooltipContent(templeData, displayConfig().animatedPreviews))
              .addTo(map);
          });

          markerPopupsRef.current[id] = { popup: tooltipPopup, timer: null };

          el.addEventListener('mouseleave', () => {
            tooltipPopup.remove();
          });

          markersRef.current[id] = new maplibregl.Marker({ element: el })
            .setLngLat([lng, lat])
            .addTo(map);
        }
      }
    });

    Object.keys(markersRef.current).forEach((id) => {
      if (!currentIds.has(id)) {
        markersRef.current[id].remove();
        delete markersRef.current[id];
        // The marker's popup and any pending hover timer go with it. Without
        // this the popup survives its owner, because removing a hovered
        // element dispatches no `mouseleave`.
        const held = markerPopupsRef.current[id];
        if (held) {
          window.clearTimeout(held.timer);
          held.popup?.remove();
          delete markerPopupsRef.current[id];
        }
      }
    });
  };

  const setupCustomLayers = (map) => {
    if (!map) return;

    if (!sourceOf(map, 'temples')) {
      map.addSource('temples', {
        type: 'geojson',
        data: {
          type: 'FeatureCollection',
          features: templeFeaturesRef.current
        },
        cluster: true,
        clusterMaxZoom: 16,
        /*
          A cluster exists only where the pins would actually overlap.

          This was 60px, and a pin is 25px wide -- so two sites 60px apart were
          merged into a cluster while sharing no ink at all, and the visitor was
          shown a "2" where they could perfectly well have been shown two
          temples. The number is the marker's own footprint plus a little, so
          the rule is the honest one: if they do not overlap, they are not a
          cluster.
        */
        clusterRadius: displayConfig().mapClusterRadius,
        clusterProperties: {
          completeCount: [
            '+',
            ['case', ['==', ['get', 'completionStatus'], 'published'], 1, 0],
          ],
          partialCount: [
            '+',
            ['case', ['==', ['get', 'completionStatus'], 'in_progress'], 1, 0],
          ],
          incompleteCount: [
            '+',
            ['case', ['==', ['get', 'completionStatus'], 'needs_images'], 1, 0],
          ],
          /*
            The fourth status has to be counted here too, or it is counted
            nowhere. `awaiting_run` was added to the pins, the legend and the
            API, and this aggregation was left with three -- so an awaiting site
            incremented nothing, and the ring's remainder arithmetic handed it
            to the blue "needs images" slice. A cluster of only awaiting sites
            came out entirely blue.

            That is exactly the miscommunication the fourth status exists to
            stop: those sites hold hundreds of photographs each, and blue asks
            for more.
          */
          awaitingCount: [
            '+',
            ['case', ['==', ['get', 'completionStatus'], 'awaiting_run'], 1, 0],
          ]
        }
      });

      map.addLayer({
        id: 'temples-invisible',
        type: 'circle',
        source: 'temples',
        paint: {
          'circle-opacity': 0,
          'circle-radius': 1
        }
      });
    }

    if (indiaBoundaryRef.current && !sourceOf(map, 'india-boundary')) {
      map.addSource('india-boundary', {
        type: 'geojson',
        data: indiaBoundaryRef.current
      });

      map.addLayer({
        id: 'india-boundary-fill',
        type: 'fill',
        source: 'india-boundary',
        paint: {
          // Barely there: it exists so the polygon is hoverable, not to tint
          // the country.
          'fill-color': '#5b7fa6',
          'fill-opacity': 0.04,
        }
      });

      map.addLayer({
        id: 'india-boundary-line',
        type: 'line',
        source: 'india-boundary',
        paint: {
          /*
            A quiet reference, not the loudest thing on screen.

            #0056b3 at 1.5px and 0.88 opacity drew every state border in a
            saturated blue that competed with the markers — and the markers are
            the information. Thinner, softer, and it thickens as you zoom in,
            where a border actually helps place a site.
          */
          'line-color': '#5b7fa6',
          'line-width': ['interpolate', ['linear'], ['zoom'], 4, 0.6, 8, 1.2],
          'line-opacity': 0.5,
        }
      });

      /*
        No hover label on a state either.

        It followed the cursor around the whole country announcing "Odisha",
        which is a fact the map already states in its own labels and which
        nobody came here to learn. It also sat between the pointer and the pins
        it was drifting over. The boundary stays -- it gives the country a shape
        at country zoom -- but it is scenery, not a control, so it no longer
        reacts.

        (This was styled properly first, against the right MapLibre class names.
        Then the honest question was whether it should exist, and it should not:
        the best version of a label that says nothing is no label.)
      */
    }
  };

  // Map Initialization
  useEffect(() => {
    if (mapRef.current) return;

    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: getMapStyle(mapStyle),
      center: center,
      zoom: zoom,
      minZoom: 3,
      maxZoom: 18,
      attributionControl: false,
      /*
        The camera lives in the URL fragment, the way every map on the web does
        it -- `#map=<zoom>/<lat>/<lng>`, which is OpenStreetMap's shape.

        The route already says *which site*; it could not say *where you are
        looking*, so zooming out and refreshing put you straight back on the
        site. This is the same shortfall `?model=closed` fixed for the viewer,
        one level down.

        The fragment rather than a query parameter, for three reasons that all
        matter here. It never reaches the server, so it cannot fragment the
        per-language canonical and `hreflang` tags the shell renders for
        crawlers (12.1). MapLibre rewrites it continuously during a pan with
        `replaceState`, which through the router would either be a history entry
        per frame or a debounce to maintain. And it is the form people can read.

        Named `map` rather than bare, so the fragment stays available for
        document anchors.
      */
      hash: 'map',
    });

    const nav = new maplibregl.NavigationControl({
      showCompass: false,
      showZoom: true,
    });
    map.addControl(nav, 'bottom-right');

    mapRef.current = map;

    map.on('load', () => {
      setupCustomLayers(map);
    });

    map.on('style.load', () => {
      setupCustomLayers(map);
    });

    map.on('click', (e) => {
      const target = e.originalEvent.target;
      if (target.closest('.custom-marker') || target.closest('.custom-cluster-icon-pie')) {
        return;
      }
      
      const newLatLng = { lat: e.lngLat.lat, lng: e.lngLat.lng };
      if (isSelectingLocationRef.current) {
        if (onSelectionPositionChangeRef.current) {
          onSelectionPositionChangeRef.current(newLatLng);
        }
      } else if (onMapClickRef.current) {
        onMapClickRef.current(newLatLng);
      }
    });

    const triggerMarkerUpdate = () => {
      updateMarkers(map);
    };

    map.on('move', triggerMarkerUpdate);
    map.on('zoom', triggerMarkerUpdate);
    map.on('resize', triggerMarkerUpdate);
    map.on('idle', triggerMarkerUpdate);
    /*
      Registered *after* the update above, so on `idle` the markers are rebuilt
      while the suppression still holds and only then is it lifted. The other
      order clears first, the same pass rebuilds the stale cluster from the tile
      that is still reporting it, and the fix does nothing at all.
    */
    map.on('idle', () => {
      openedClustersRef.current.clear();
    });
    map.on('sourcedata', (e) => {
      if (e.sourceId === 'temples' && e.isSourceLoaded) {
        triggerMarkerUpdate();
      }
    });

    return () => {
      // Pending hover timers first: one that fires after the map is gone would
      // call `getClusterLeaves` on a removed source.
      Object.values(markerPopupsRef.current).forEach((held) => {
        window.clearTimeout(held.timer);
        held.popup?.remove();
      });
      markerPopupsRef.current = {};
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
    /*
      Mount-only, deliberately. `center` and `mapStyle` are read here as the
      map's *initial* camera and style; re-running this would tear down the
      MapLibre instance and rebuild it on every pan. Both are kept in step
      afterwards by their own effects -- `map.setStyle` below for the style, and
      `flyTo` for the camera -- and `updateMarkers` is reached through the event
      handlers registered above, which close over the current render.
    */
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Update source data when temple features change
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return undefined;

    const apply = () => {
      const source = sourceOf(map, 'temples');
      if (!source) return false;
      source.setData({ type: 'FeatureCollection', features: templeFeatures });
      return true;
    };

    if (apply()) return undefined;

    /*
      The sites arrived before the map had a `temples` source to put them in.

      Retried on `styledata`, which fires repeatedly, **not** `style.load`,
      which fires once. Gating on `style.load` was worse than the crash it
      replaced: if the style had already loaded by the time the sites arrived --
      which is the common case, the API being same-origin and the basemap being
      a CDN away -- the one-shot never fired again and the features were never
      applied at all. The map stayed empty, silently, with no error and a
      correctly populated site list beside it.
    */
    const retry = () => {
      if (apply()) {
        map.off('styledata', retry);
        map.off('load', retry);
      }
    };
    map.on('styledata', retry);
    map.on('load', retry);
    return () => {
      map.off('styledata', retry);
      map.off('load', retry);
    };
  }, [templeFeatures]);

  /*
    Change the basemap when the visitor picks a different one.

    Skipped on mount. The map is *constructed* with `getMapStyle(mapStyle)`
    above, and this effect then ran immediately and set the identical style
    again -- which aborts the style request already in flight and issues a
    second one. Every cold load fetched the style document twice, and the map
    rendered blank for as long as the abort-and-retry took. Visible in the
    network log as `ERR_ABORTED .../styles/positron` on every visit.
  */
  const appliedStyleRef = useRef(mapStyle);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || appliedStyleRef.current === mapStyle) return;
    appliedStyleRef.current = mapStyle;
    map.setStyle(getMapStyle(mapStyle));
  }, [mapStyle]);

  /*
    Put the map back after the browser takes the WebGL context away.

    This is not hypothetical: a GPU reset, a driver update, switching graphics
    cards, or simply too many WebGL contexts on one machine all revoke it, and
    the page gets a `webglcontextlost` event with no say in the matter. MapLibre
    responds by dropping its style, which aborts whatever it was fetching -- so
    the map goes blank and stays blank, and every subsequent marker update reads
    a null style.

    Until now this recovered by accident: the style effect above re-set an
    identical style on mount, which happened to rebuild the map after a loss
    during startup. Making that effect correct removed the accident, so the
    recovery is explicit here instead -- and it now also covers a loss that
    happens *after* startup, which the accident never did.
  */
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return undefined;

    const restore = () => {
      // A fresh style rebuilds the sources and layers; the data effects are
      // listening on `styledata` and re-bind themselves once it lands.
      map.setStyle(getMapStyle(appliedStyleRef.current));
    };
    map.on('webglcontextrestored', restore);
    return () => map.off('webglcontextrestored', restore);
  }, []);

  /*
    Fly to a search result or a shared link's site.

    Keyed on the request object, not on the three numbers inside it. Those
    numbers were pulled into their own variables so the dependency array held
    plain identifiers rather than subscript expressions, which nothing can check
    statically -- but it also made re-picking a site a no-op: `selectSite` hands
    over a fresh `searchTarget` with the same coordinates, all three deps
    compare equal, and React skips the effect. Choose a site, pan away, choose
    it again and the panel opened while the camera stayed over wherever you had
    scrolled to. Reading the coordinates inside the effect keeps the dependency
    a plain identifier and makes every selection a request to fly.
  */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !searchTarget) return;
    const [lat, lng] = searchTarget.position ?? [];
    if (lat === undefined || lng === undefined) return;
    /*
      A camera in the URL outranks the site's own framing, once.

      Without this the hash is decorative: MapLibre restores the saved camera on
      load, and then this effect -- which runs on every arrival at a site URL,
      because the route selects the site and selecting flies -- immediately
      takes it away again. That is the bug the hash is meant to fix, wearing a
      different hat.

      Once only, and then cleared. `default_zoom` and friends on the Site row
      are the framing for a *first* arrival, and a later deliberate choice of
      this same site should still fly.
    */
    if (hashCameraRef.current) {
      hashCameraRef.current = false;
      cameraClaimedRef.current = true;
      return;
    }
    // Somebody has said where the camera should be, so the India fit below
    // must not take it back.
    cameraClaimedRef.current = true;
    map.flyTo({
      center: [lng, lat],
      zoom: searchTarget.zoom ?? 12,
      // `index.css` collapses CSS transitions for a visitor who asks for less
      // motion; a scripted camera move is invisible to that rule, and this is
      // the biggest movement on the site.
      duration: motionDuration(900),
      essential: true,
    });
  }, [searchTarget]);

  // Handle India Boundary load & fit bounds
  const getGeoJSONBounds = (geojson) => {
    let minLng = Infinity;
    let minLat = Infinity;
    let maxLng = -Infinity;
    let maxLat = -Infinity;

    const traverse = (coords) => {
      if (typeof coords[0] === 'number') {
        const [lng, lat] = coords;
        if (lng < minLng) minLng = lng;
        if (lat < minLat) minLat = lat;
        if (lng > maxLng) maxLng = lng;
        if (lat > maxLat) maxLat = lat;
      } else {
        coords.forEach(traverse);
      }
    };

    if (geojson.features) {
      geojson.features.forEach((f) => traverse(f.geometry.coordinates));
    } else if (geojson.geometry) {
      traverse(geojson.geometry.coordinates);
    }

    return minLng === Infinity ? null : [[minLng, minLat], [maxLng, maxLat]];
  };

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !indiaBoundary) return;

    const updateBoundary = () => {
      const source = sourceOf(map, 'india-boundary');
      if (source) {
        source.setData(indiaBoundary);
      } else {
        setupCustomLayers(map);
      }

      /*
        Only frame India if nothing has claimed the camera.

        This `fitBounds` ran unconditionally when the 1 MB boundary resolved.
        The boundary waits on the cross-origin style; the sites list is
        same-origin and arrives first -- so a deep link had already flown to its
        temple, and this pulled the map back out to the whole country. Every
        ARK resolves through that route, which makes it the failure mode for
        the one URL this project promises to keep working.
      */
      if (cameraClaimedRef.current) return;
      const bounds = getGeoJSONBounds(indiaBoundary);
      if (bounds) {
        map.fitBounds(bounds, { padding: 30 });
      }
    };

    /*
      Same one-shot trap as the sites above, and this one also owns `fitBounds`
      -- so when it missed, the map never framed India either and sat at its
      default centre with everything off-screen. `styledata` fires repeatedly;
      `style.load` fires once and may already be past.
    */
    const drawn = () => {
      if (!sourceOf(map, 'temples') && !layerOf(map, 'india-boundary-fill')) {
        // The style is not ready to take layers yet.
        return false;
      }
      updateBoundary();
      return true;
    };

    if (map.isStyleLoaded() && drawn()) return undefined;

    const retry = () => {
      if (drawn()) {
        map.off('styledata', retry);
        map.off('load', retry);
      }
    };
    map.on('styledata', retry);
    map.on('load', retry);
    return () => {
      map.off('styledata', retry);
      map.off('load', retry);
    };
  }, [indiaBoundary]);

  // Handle Drag-and-Drop Selection Location Pin
  const selectionMarkerRef = useRef(null);
  // Pulled out for the same reason as the fly-to target: a dependency array of
  // optional-chained member expressions cannot be statically checked.
  const selectionLat = selectedPosition?.lat;
  const selectionLng = selectedPosition?.lng;

  /*
    Where the pin is on screen, so the panel can sit beside it.

    It used to be pinned to the top-centre of the map, which is the one place
    guaranteed not to be near the thing it describes: on a tall screen the pin
    could be six hundred pixels below the sentence telling you to drag it.
    Reprojected on every camera change, because the pin moves under a pan.
  */
  const [pinPoint, setPinPoint] = useState(null);

  /*
    Drop the pin where the visitor is looking, not at the centre of India.

    `Home` seeded it from `DEFAULT_POSITION` (20.5937, 78.9629). Zoom into
    Odisha and press "Request site" and the pin is placed a thousand kilometres
    away, off-screen -- and because the panel below is positioned from
    `map.project()`, it followed the pin out of the viewport (measured:
    `left: -39436px`). The whole site-request flow was unreachable for anyone
    who had looked at a site first, which is everyone.

    MapView is the only thing that knows where the map is, so it seeds it.
  */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isSelectingLocation || selectionLat !== undefined) return;
    const centre = map.getCenter();
    onSelectionPositionChangeRef.current?.({ lat: centre.lat, lng: centre.lng });
  }, [isSelectingLocation, selectionLat]);

  useEffect(() => {
    const map = mapRef.current;
    // No clearing on the way out: the panel is only rendered while selecting, so
    // a stale point is never read, and clearing here is a cascading render.
    if (!map || !isSelectingLocation || selectionLat === undefined) return undefined;
    const project = () => {
      const { x, y } = map.project([selectionLng, selectionLat]);
      /*
        Clamped to the viewport. A pin can be dragged or panned off-screen, and
        an unclamped projection puts the Confirm and Cancel buttons somewhere
        no pointer can reach -- which is a trap, not a layout bug: the mode has
        no other exit.
      */
      const { clientWidth: w, clientHeight: h } = map.getContainer();
      const margin = 24;
      setPinPoint({
        x: Math.min(Math.max(x, margin), Math.max(margin, w - margin)),
        y: Math.min(Math.max(y, 100), Math.max(100, h - margin)),
      });
    };
    project();
    map.on('move', project);
    map.on('resize', project);
    return () => {
      map.off('move', project);
      map.off('resize', project);
    };
  }, [isSelectingLocation, selectionLat, selectionLng]);

  /*
    A place name for the coordinate, because a coordinate is not an answer to
    "where am I putting this". The panel showed "Selected: 20.244517,
    85.852822" and nothing else -- true, and no help at all in deciding whether
    the pin is on the temple or in the car park next to it.
  */
  /*
    Stored against the coordinate it describes, rather than cleared when the pin
    moves. Clearing would be a second render, and between the two the panel
    would show the *previous* pin's place name under the new pin's coordinates
    -- briefly, confidently, and wrongly.
  */
  const [pinPlace, setPinPlace] = useState(null);
  const pinKey = selectionLat === undefined ? null : `${selectionLat},${selectionLng}`;

  useEffect(() => {
    if (!isSelectingLocation || selectionLat === undefined) return undefined;
    let active = true;
    // Debounced: dragging the pin fires continuously, and Nominatim asks for at
    // most one call a second.
    const timer = window.setTimeout(async () => {
      const data = await reverseGeocode(selectionLat, selectionLng);
      if (!active || !data) return;
      setPinPlace({ key: `${selectionLat},${selectionLng}`, name: getReadableLocationName(data) });
    }, 600);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [isSelectingLocation, selectionLat, selectionLng]);

  const placeName = pinPlace && pinPlace.key === pinKey ? pinPlace.name : '';

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    if (isSelectingLocation && selectionLat !== undefined && selectionLng !== undefined) {
      if (!selectionMarkerRef.current) {
        /*
          A `<button>`, and it has to be.

          This pin is how someone says *where* the site they are requesting is,
          and it was a `<div>` that could only be moved by dragging with a
          mouse. Not focusable, no accessible name, no keyboard equivalent: the
          request flow simply could not be completed without a pointer
          (WCAG 2.1.1, 4.1.2).

          The arrow keys are that equivalent. They move the pin by a step that
          scales with the zoom -- a fixed step in degrees is either useless at
          city zoom or unusable at country zoom -- and Shift takes ten steps at
          once, so crossing a district does not take two hundred presses.
        */
        const el = document.createElement('button');
        el.type = 'button';
        el.className = 'custom-marker selection-marker';
        el.setAttribute('aria-label', tRef.current('a11y.selectionPin'));
        el.style.width = '25px';
        el.style.height = '41px';
        el.style.padding = '0';
        el.style.border = 'none';
        el.style.background = `url(${buildMarkerDataUrl('#e53935')}) no-repeat`;
        el.style.backgroundSize = 'contain';
        el.style.filter = 'drop-shadow(0 3px 6px rgba(0,0,0,0.35))';
        el.style.cursor = 'grab';

        selectionMarkerRef.current = new maplibregl.Marker({
          element: el,
          draggable: true
        })
          .setLngLat([selectionLng, selectionLat])
          .addTo(map);

        const move = (dLat, dLng) => {
          const current = selectionMarkerRef.current?.getLngLat();
          if (!current) return;
          const next = { lat: current.lat + dLat, lng: current.lng + dLng };
          selectionMarkerRef.current.setLngLat([next.lng, next.lat]);
          onSelectionPositionChangeRef.current?.(next);
        };

        el.addEventListener('keydown', (event) => {
          // Degrees per press, scaled to what a pixel is worth at this zoom.
          const step = (event.shiftKey ? 10 : 1) * (360 / 2 ** (map.getZoom() + 9));
          const moves = {
            ArrowUp: [step, 0],
            ArrowDown: [-step, 0],
            ArrowLeft: [0, -step],
            ArrowRight: [0, step],
          };
          const delta = moves[event.key];
          if (!delta) return;
          // Otherwise the map pans underneath the pin at the same time, and the
          // pin appears not to move at all.
          event.preventDefault();
          event.stopPropagation();
          move(delta[0], delta[1]);
        });

        selectionMarkerRef.current.on('dragend', () => {
          const lngLat = selectionMarkerRef.current.getLngLat();
          if (onSelectionPositionChangeRef.current) {
            onSelectionPositionChangeRef.current({ lat: lngLat.lat, lng: lngLat.lng });
          }
        });
      } else {
        selectionMarkerRef.current.setLngLat([selectionLng, selectionLat]);
      }
    } else {
      if (selectionMarkerRef.current) {
        selectionMarkerRef.current.remove();
        selectionMarkerRef.current = null;
      }
    }
  }, [isSelectingLocation, selectionLat, selectionLng]);

  // Handle selected custom temple pin & permanent tooltip
  const selectedCustomMarkerRef = useRef(null);
  const selectedCustomPopupRef = useRef(null);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    if (selectedTemple && selectedTemple.isCustomLocation && selectedTemple.position) {
      const [lat, lng] = selectedTemple.position;

      if (!selectedCustomMarkerRef.current) {
        /*
          Decoration, unlike the pin above: it marks which site is already
          selected, and everything it conveys is in the panel beside it as text.
          `aria-hidden` rather than a control, so a screen reader is not made to
          announce an unlabelled graphic that does nothing.
        */
        const el = document.createElement('div');
        el.setAttribute('aria-hidden', 'true');
        el.className = 'custom-marker selected-site-highlight';
        el.style.width = '25px';
        el.style.height = '41px';
        el.style.backgroundImage = `url(${buildMarkerDataUrl('#e53935')})`;
        el.style.backgroundSize = 'contain';
        el.style.backgroundRepeat = 'no-repeat';
        el.style.filter = 'drop-shadow(0 3px 6px rgba(0,0,0,0.35))';

        selectedCustomMarkerRef.current = new maplibregl.Marker({ element: el })
          .setLngLat([lng, lat])
          .addTo(map);

        const displayName = selectedTemple.name.length > 30 ? 'Selected Location' : selectedTemple.name;
        selectedCustomPopupRef.current = new maplibregl.Popup({
          closeButton: false,
          closeOnClick: false,
          offset: [0, -34],
          className: 'custom-tooltip'
        })
          .setLngLat([lng, lat])
          .setDOMContent(buildSelectedLabel(displayName))
          .addTo(map);
      } else {
        selectedCustomMarkerRef.current.setLngLat([lng, lat]);
        selectedCustomPopupRef.current.setLngLat([lng, lat]);
        const displayName = selectedTemple.name.length > 30 ? 'Selected Location' : selectedTemple.name;
        selectedCustomPopupRef.current.setDOMContent(buildSelectedLabel(displayName));
      }
    } else {
      if (selectedCustomMarkerRef.current) {
        selectedCustomMarkerRef.current.remove();
        selectedCustomMarkerRef.current = null;
      }
      if (selectedCustomPopupRef.current) {
        selectedCustomPopupRef.current.remove();
        selectedCustomPopupRef.current = null;
      }
    }
  }, [selectedTemple, selectedTemple?.isCustomLocation, selectedTemple?.position]);

  return (
    <div className="map-view-container">
      {/*
        The map is a graphic; the site list in the sidebar is its accessible
        equivalent, which is why this is labelled rather than hidden. The name
        is translated -- an aria-label left in English is an English-only label
        for anyone reading in Odia or Hindi (internal design notes).

        `id="main"` because this is where the skip link lands, and
        `tabIndex={-1}` so it can receive that focus without joining the tab
        order itself.
      */}
      <div
        ref={mapContainerRef}
        className="map-instance"
        id="main"
        role="region"
        aria-label={t('a11y.mapRegion')}
        tabIndex={-1}
      />

      {showMapSearch && (
        <div className="map-search-overlay">
          <SearchBar
            templeList={templeList}
            onSearchSelect={onSearchSelect}
            variant="map"
            searchQuery={searchQuery}
            setSearchQuery={setSearchQuery}
            isFocused={isSearchFocused}
            setIsFocused={setIsSearchFocused}
            activeIndex={searchActiveIndex}
            setActiveIndex={setSearchActiveIndex}
          />
        </div>
      )}

      <div className="map-custom-footer">
        <div className="map-custom-attribution">
          <a href="https://maplibre.org/" target="_blank" rel="noopener noreferrer">MapLibre</a>
          <span> | </span>
          <a href="https://openfreemap.org" target="_blank" rel="noopener noreferrer">OpenFreeMap</a>
          <span> © </span>
          <a href="https://www.openmaptiles.org/" target="_blank" rel="noopener noreferrer">OpenMapTiles</a>
          <span> {t('map.dataFrom')} </span>
          <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>
        </div>
        {/*
          `Trans`, so only "smlab" is the link.

          The whole phrase was one anchor first, to dodge word order: Hindi and
          Odia put the lab's name at the *start* of the sentence, so a fix that
          wrapped "the last word" would link the wrong word in two of three
          languages. `Trans` substitutes into each string's own `<lab>`, so the
          name is linked wherever that language puts it.

          The placeholder is `<lab>`, not `<link>`. `link` is a *void* HTML
          element, so the parser inside `Trans` reads `<link>smlab</link>` as
          self-closing and throws the text away: the anchor rendered, correctly
          linked, and completely empty. Any void-element name -- `link`, `br`,
          `img`, `meta` -- is the same trap.
        */}
        <div className="map-custom-credit">
          <Trans
            i18nKey="footer.developedBy"
            components={{
              lab: (
                <a
                  href="https://www.niser.ac.in/~smishra/"
                  target="_blank"
                  rel="noopener noreferrer"
                />
              ),
            }}
          />
        </div>
        {/*
          `nav`, because these are how the site's own pages are reached and
          there was no other route to them. Also translated: two English words
          in the corner of an otherwise Odia page.
        */}
        <nav className="map-custom-links" aria-label={t('nav.about')}>
          <button type="button" onClick={onAboutClick}>
            {t('nav.about')}
          </button>
          <span className="separator" aria-hidden="true">
            |
          </span>
          <button type="button" onClick={onPrivacyClick}>
            {t('request.privacy')}
          </button>
        </nav>
      </div>

      {/* Map style selector */}
      {/*
        The key to the pins. The strings existed in all three languages and were
        rendered by nothing, so the map's three states were conveyed by hue
        alone -- and the three hues are 1.0, 1.34 and 1.33:1 against each other,
        which is to say indistinguishable to a visitor with deuteranopia or
        protanopia (WCAG 1.4.1).

        A `<details>` because it is reference, not chrome: open it when you need
        it, and it costs nothing on a phone when you do not. The swatches use
        the same `buildMarkerDataUrl` as the map, so the key cannot drift from
        what is drawn.
      */}

      {/*
        How much basemap to draw.

        This was a row of four buttons at the bottom of the map container, and
        the container is the full height of the viewport -- so the row sat at
        y=900 on a 900px screen, entirely below the fold. Nobody could reach it,
        which is the same as it not existing. Its icons were `map`,
        `navigation`, `satellite` and `layers`, none of which are in the icon
        set, so it would have rendered as four empty buttons anyway.

        A `<details>` like the legend, for one pattern rather than two, anchored
        where it can be seen. The names say what the choice actually is --
        detail, not cartographic branding -- because "do I get more of the map?"
        is the question being asked.
      */}
      {/*
        One cluster, at the right edge, under the theme toggle.

        These were scattered: the key to the pins sat bottom-left over the site
        list, the basemap picker top-right, the language buttons inside the
        sidebar, and there was no way back to the starting view at all. Four
        controls that all do the same kind of thing -- change what the map shows
        you -- in four places.

        A column rather than a row because a row of five 44px controls is 252px,
        which on a 360px phone is most of the width and collides with the
        sidebar toggle in the other corner. A right-edge stack is what every map
        on the web does, and it grows downwards without running out of room.
      */}
      <div className="map-control-stack">
      <button
        type="button"
        className="map-stack-btn"
        onClick={() => {
          const map = mapRef.current;
          if (!map) return;
          // The view the map opens on. `flyTo` rather than `jumpTo` so it is
          // legible as "you were taken back" instead of a hard cut, and
          // `motionDuration` so it respects `prefers-reduced-motion`.
          map.flyTo({
            center,
            zoom,
            bearing: 0,
            pitch: 0,
            duration: motionDuration(600),
          });
        }}
        aria-label={t('map.home')}
        title={t('map.home')}
      >
        <Icon name="home" />
      </button>

      <details className="map-style-control">
        {/* Icon only: it is a 44px square in the stack now, and the current
            choice is named inside the panel and by the accessible name here. */}
        <summary
          aria-label={`${t('map.styles.title')}: ${t(`map.styles.${mapStyle}`)}`}
          title={t(`map.styles.${mapStyle}`)}
        >
          <Icon name="layers" />
        </summary>
        <ul>
          {Object.keys(MAP_STYLES).map((key) => (
            <li key={key}>
              <button
                type="button"
                className={`map-style-btn ${mapStyle === key ? 'active' : ''}`}
                onClick={() => setMapStyle(key)}
                // The current choice, said rather than only coloured.
                aria-pressed={mapStyle === key}
              >
                {t(`map.styles.${key}`)}
              </button>
            </li>
          ))}
        </ul>
      </details>

      {/*
        The key to the pins, collapsed to one control.

        It was an always-visible `<details>` in the bottom-left corner, sitting
        over the sidebar's site list -- and its focus ring, three pixels outside
        a full-width summary, drew a blue bar straight across "Models available".
        As a square in the stack it is the same size as everything else here and
        overlaps nothing.
      */}
      <details className="map-legend">
        <summary aria-label={t('map.legend.title')} title={t('map.legend.title')}>
          <Icon name="help" />
        </summary>
        <div className="map-legend-panel">
          <p className="map-legend-heading">{t('map.legend.title')}</p>
          <ul>
            {Object.entries(MARKER_COLOURS).map(([status, colour]) => (
              <li key={status}>
                <img src={buildMarkerDataUrl(colour, status)} alt="" width="17" height="28" />
                <span>{t(`map.legend.${status}`)}</span>
              </li>
            ))}
            <li className="map-legend-note">{t('map.legend.cluster')}</li>
          </ul>
        </div>
      </details>

      <LanguageSwitcher compact />

      {/*
        Signing in, without starting a contribution first.

        The only sign-in control was inside the contribute dialog, so the only
        way to sign in -- or to find out you already were, or that your account
        is still awaiting approval -- was to begin an upload.
      */}
      <AccountControl />
      </div>

      {isSelectingLocation && selectedPosition && (
        <div
          className="location-picker-overlay"
          style={
            pinPoint
              ? { left: `${pinPoint.x}px`, top: `${pinPoint.y}px`, transform: 'translate(-50%, -100%)' }
              : undefined
          }
        >
          <p className="location-picker-help">{t('map.dropPinHelp')}</p>
          {placeName && <p className="location-picker-place">{placeName}</p>}
          <p className="location-picker-coordinates">
            {t('map.selected')} {selectedPosition.lat.toFixed(5)}, {selectedPosition.lng.toFixed(5)}
          </p>
          <div className="location-picker-buttons">
            <button type="button" className="btn btn-primary" onClick={onConfirmLocation}>{t('map.confirmLocation')}</button>
            <button type="button" className="btn btn-tertiary" onClick={onCancelLocationSelection}>{t('map.cancel')}</button>
          </div>
        </div>
      )}
    </div>
  );
}

export default React.memo(MapView);
