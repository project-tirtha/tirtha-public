/**
 * The map's camera and its cluster labels, over a fake MapLibre.
 *
 * Both things tested here are closure bugs rather than rendering bugs: the map
 * is driven imperatively from effects and from handlers registered once at
 * mount, so what a test has to check is *when* those run and *what they had in
 * scope when they did*. A fake `Map` that records `flyTo` and hands back its
 * own listeners is enough for that, and it keeps WebGL out of jsdom.
 */
import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import i18n from '../i18n/config';
import { MARKER_COLOURS } from '../utils/siteStatus';
import { ThemeProvider } from '../context/ThemeContext';

/*
  `MapView` reads the interface theme now -- the basemap follows it until a
  visitor pins a style -- and `useTheme` throws outside a provider, by design.
  In the app the provider is always above this component; these renders were the
  only place it was not, so they get it through `wrapper` rather than by
  wrapping each element. `rerender` from a `wrapper`ed `render` keeps the
  wrapper, so the multi-line rerender below needs no change.
*/
const renderMap = (ui) => render(ui, { wrapper: ThemeProvider });

/** Every fake map built during a test, in construction order. */
const maps = [];
/** What `queryRenderedFeatures` should answer with next. */
let renderedFeatures = [];
/** What `getClusterLeaves` should answer with next. */
let clusterLeaves = [];
/** The elements handed to `new maplibregl.Marker`, in creation order. */
const markerElements = [];
/** Popups on which `remove()` was called. */
const popupsRemoved = [];
/** The options every popup was constructed with, in creation order. */
const popupOptions = [];

vi.mock('maplibre-gl', () => {
  class FakeMap {
    constructor(options) {
      this.options = options;
      this.handlers = {};
      this.flyTo = vi.fn();
      this.fitBounds = vi.fn();
      maps.push(this);
    }
    on(event, handler) {
      (this.handlers[event] ||= []).push(handler);
      return this;
    }
    off() {
      return this;
    }
    fire(event, payload) {
      (this.handlers[event] || []).forEach((handler) => handler(payload));
    }
    addControl() {}
    // The real map hands back its container; the cluster handler focuses it
    // after removing the button that was activated.
    getContainer() {
      return { focus: () => {}, clientWidth: 800, clientHeight: 600 };
    }
    addSource() {}
    addLayer() {}
    setStyle() {}
    remove() {}
    isStyleLoaded() {
      return true;
    }
    getSource() {
      /*
        A source that answers the way MapLibre 5 does: `getClusterExpansionZoom`
        returns a Promise and takes no callback. Returning null here would let
        the cluster-click test pass without ever reaching the call.
      */
      return {
        setData: () => {},
        getClusterExpansionZoom: (clusterId) => Promise.resolve(9 + clusterId),
        // The sites inside the cluster, which is what the click frames.
        getClusterLeaves: () => Promise.resolve(clusterLeaves),
      };
    }
    getLayer(id) {
      return { id };
    }
    queryRenderedFeatures() {
      return renderedFeatures;
    }
  }

  class FakeMarker {
    constructor({ element }) {
      markerElements.push(element);
    }
    setLngLat() {
      return this;
    }
    addTo() {
      return this;
    }
    remove() {}
  }

  class FakePopup {
    constructor(options) {
      this.options = options || {};
      popupOptions.push(this.options);
    }
    setLngLat() {
      return this;
    }
    setHTML() {
      return this;
    }
    setDOMContent() {
      return this;
    }
    getElement() {
      return null;
    }
    addTo() {
      return this;
    }
    // Recorded, because "the popup was removed" is the whole assertion for a
    // popup that used to outlive its marker.
    remove() {
      popupsRemoved.push(this);
    }
  }

  // Named exports: MapLibre 6 is ESM-only with no default export, and
  // MapView takes it as `import * as maplibregl`.
  return {
    Map: FakeMap,
    Marker: FakeMarker,
    Popup: FakePopup,
    NavigationControl: class {},
    setWorkerUrl: () => {},
  };
});

const cluster = (clusterId, count) => ({
  geometry: { coordinates: [85.8, 19.9] },
  properties: {
    cluster: true,
    cluster_id: clusterId,
    point_count: count,
    completeCount: 3,
    partialCount: 1,
    incompleteCount: count - 4,
  },
});

let MapView;

beforeEach(async () => {
  maps.length = 0;
  markerElements.length = 0;
  popupsRemoved.length = 0;
  popupOptions.length = 0;
  renderedFeatures = [];
  // The boundary file is not served in a test run; answering the fetch keeps
  // the component's own error path out of the way of what is being tested.
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      json: async () => ({ type: 'FeatureCollection', features: [] }),
    })),
  );
  ({ default: MapView } = await import('./MapView'));
});

afterEach(async () => {
  vi.unstubAllGlobals();
  await act(async () => {
    await i18n.changeLanguage('en');
  });
});

describe('the camera', () => {
  it('flies again when the same site is picked a second time', () => {
    const konark = { position: [19.8876, 86.0945], zoom: 12 };
    const { rerender } = renderMap(<MapView searchTarget={konark} showMapSearch={false} />);
    const map = maps[0];
    expect(map.flyTo).toHaveBeenCalledTimes(1);

    /*
      A fresh object carrying the same coordinates -- exactly what
      `Home.selectSite` produces when a visitor picks a site they have already
      visited. The camera has been moved since, so this has to fly again.
    */
    rerender(
      <MapView searchTarget={{ position: [19.8876, 86.0945], zoom: 12 }} showMapSearch={false} />,
    );
    expect(map.flyTo).toHaveBeenCalledTimes(2);
  });
});

describe('a cluster built after the language changes', () => {
  it('is announced in the language the page is now in', async () => {
    renderMap(<MapView showMapSearch={false} />);
    const map = maps[0];

    renderedFeatures = [cluster(1, 12)];
    act(() => map.fire('move'));
    const english = markerElements[0].getAttribute('aria-label');
    expect(english).toBe(
      i18n.t('map.cluster.label', { count: 12, published: 3 }),
    );

    await act(async () => {
      await i18n.changeLanguage('or');
    });

    // A cluster the map has not drawn before: the cached ones are not rebuilt,
    // so only a new one can show which translator the handler is holding.
    renderedFeatures = [cluster(2, 12)];
    act(() => map.fire('move'));
    const odia = markerElements[1].getAttribute('aria-label');
    expect(odia).toBe(
      i18n.t('map.cluster.label', { count: 12, published: 3 }),
    );
    expect(odia).not.toBe(english);
  });
});

/**
 * Clicking a cluster shows everything inside it, in one move.
 *
 * Two bugs lived here in sequence. The handler first called
 * `getClusterExpansionZoom(id, callback)` -- the MapLibre 3 signature -- and
 * since v4 the method returns a Promise and ignores a second argument, so
 * clicking a cluster did nothing at all, silently.
 *
 * With that fixed it barely moved, because expansion zoom is the level at which
 * a cluster *begins* to break up: for eleven pins that is often one step
 * yielding a ten and a one. The map lurched and the cluster was still there.
 *
 * It frames the members now, which is what the click was always asking for.
 */
describe('clicking a cluster', () => {
  const cluster = {
    geometry: { coordinates: [85.8, 20.2] },
    properties: {
      cluster: true,
      cluster_id: 3,
      point_count: 12,
      completeCount: 8,
      partialCount: 2,
      incompleteCount: 2,
    },
  };

  const leafAt = (lng, lat) => ({ geometry: { type: 'Point', coordinates: [lng, lat] } });

  const clickTheCluster = async () => {
    await act(async () => {
      renderMap(<MapView showMapSearch={false} />);
    });
    await act(async () => {
      maps[0].fire('idle');
    });
    const el = markerElements.find((e) => e.className.includes('custom-cluster-icon-pie'));
    expect(el, 'no cluster marker was created').toBeTruthy();
    await act(async () => {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  it('frames every site inside it rather than stepping one zoom level', async () => {
    renderedFeatures = [cluster];
    clusterLeaves = [leafAt(85.7, 20.1), leafAt(85.9, 20.4), leafAt(85.8, 20.2)];

    await clickTheCluster();

    expect(maps[0].fitBounds).toHaveBeenCalled();
    const [bounds, options] = maps[0].fitBounds.mock.calls.at(-1);
    // The bounding box of all three leaves, not a step from the cluster's point.
    expect(bounds).toEqual([
      [85.7, 20.1],
      [85.9, 20.4],
    ]);
    expect(options.maxZoom).toBe(17);
  });

  it('dissolves rather than framing a point when the sites share a coordinate', async () => {
    /*
      Two temples in one compound. Their bounding box is a point, and
      `fitBounds` on it would zoom to the maximum and still draw one pin on top
      of another. There is nothing to separate, so the cluster is dissolved and
      the pins are drawn where they actually are.
    */
    renderedFeatures = [cluster];
    clusterLeaves = [leafAt(85.8, 20.2), leafAt(85.8, 20.2)];

    await clickTheCluster();

    expect(maps[0].fitBounds).not.toHaveBeenCalled();
    expect(maps[0].flyTo).toHaveBeenCalled();
    expect(maps[0].flyTo.mock.calls.at(-1)[0].zoom).toBe(17);
  });
});

/**
 * The ring summarises every status, not the three it used to know.
 *
 * `awaiting_run` was added to the pins, the legend, the API and the backend
 * a11y tests, and the cluster aggregation was left counting three. An awaiting
 * site therefore incremented nothing, and the ring derived its last slice as
 * `100 - complete - partial` -- so those sites were handed to the blue "needs
 * images" wedge, and a cluster made only of them came out entirely blue.
 *
 * Blue asks for photographs. Those four sites hold 278 to 541 each.
 */
describe('the cluster ring', () => {
  const ringFor = (counts) => {
    renderMap(<MapView showMapSearch={false} />);
    renderedFeatures = [
      {
        geometry: { coordinates: [85.8, 19.9] },
        properties: {
          cluster: true,
          cluster_id: 90,
          point_count: Object.values(counts).reduce((a, b) => a + b, 0),
          completeCount: counts.published ?? 0,
          partialCount: counts.in_progress ?? 0,
          incompleteCount: counts.needs_images ?? 0,
          awaitingCount: counts.awaiting_run ?? 0,
        },
      },
    ];
    act(() => maps[0].fire('move'));
    const el = markerElements.find((e) => e.className.includes('custom-cluster-icon-pie'));
    expect(el, 'no cluster marker was created').toBeTruthy();
    return el.innerHTML;
  };

  it('draws a cluster of only awaiting sites in the awaiting colour', () => {
    const ring = ringFor({ awaiting_run: 4 });
    expect(ring).toContain(MARKER_COLOURS.awaiting_run);
    expect(
      ring,
      'awaiting sites were drawn as "needs images", which asks for photographs they already have',
    ).not.toContain(MARKER_COLOURS.needs_images);
  });

  it('gives awaiting sites their own wedge in a mixed cluster', () => {
    const ring = ringFor({ published: 3, awaiting_run: 1 });
    expect(ring).toContain(MARKER_COLOURS.published);
    expect(ring).toContain(MARKER_COLOURS.awaiting_run);
    expect(ring).not.toContain(MARKER_COLOURS.needs_images);
  });

  it('still shows blue when sites genuinely need images', () => {
    // The opposite error would be just as bad: the wedge has to survive.
    expect(ringFor({ needs_images: 2 })).toContain(MARKER_COLOURS.needs_images);
  });

  it('counts only the sites with models as having models', () => {
    renderMap(<MapView showMapSearch={false} />);
    renderedFeatures = [
      {
        geometry: { coordinates: [85.8, 19.9] },
        properties: {
          cluster: true,
          cluster_id: 91,
          point_count: 10,
          completeCount: 6,
          partialCount: 0,
          incompleteCount: 0,
          awaitingCount: 4,
        },
      },
    ];
    act(() => maps[0].fire('move'));
    const el = markerElements.find((e) => e.className.includes('custom-cluster-icon-pie'));
    const label = el.getAttribute('aria-label');
    expect(label).toContain('10');
    expect(label).toContain('6');
  });
});

/**
 * The India boundary must not take back a camera someone asked for.
 *
 * `fitBounds` on the boundary ran unconditionally when the 1 MB GeoJSON
 * resolved. That file waits on the cross-origin style while the sites list is
 * same-origin, so a deep link had already flown to its temple and this pulled
 * the map back out to the whole country. Every ARK resolves through that route.
 */
describe('the camera and the India boundary', () => {
  const BOUNDARY = {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: { type: 'Polygon', coordinates: [[[68, 8], [97, 8], [97, 37], [68, 37], [68, 8]]] },
        properties: {},
      },
    ],
  };

  const withBoundary = () =>
    vi.fn(async () => ({ ok: true, json: async () => BOUNDARY }));

  it('frames India when nothing has claimed the camera', async () => {
    vi.stubGlobal('fetch', withBoundary());
    renderMap(<MapView showMapSearch={false} />);
    const map = maps[0];
    await act(async () => {
      await Promise.resolve();
      map.fire('styledata');
    });
    expect(map.fitBounds).toHaveBeenCalled();
  });

  it('leaves a deep-linked site alone', async () => {
    vi.stubGlobal('fetch', withBoundary());
    renderMap(
      <MapView searchTarget={{ position: [19.8876, 86.0945], zoom: 14 }} showMapSearch={false} />,
    );
    const map = maps[0];
    expect(map.flyTo).toHaveBeenCalledTimes(1);

    await act(async () => {
      await Promise.resolve();
      map.fire('styledata');
    });

    expect(
      map.fitBounds,
      'the boundary pulled the map back out to the whole of India',
    ).not.toHaveBeenCalled();
  });
});

/**
 * A popup belongs to its marker and dies with it.
 *
 * Both the tooltip and the cluster sheet are standalone popups; the only thing
 * removing them was a `mouseleave` on the marker element, and removing a
 * hovered element dispatches no `mouseleave`. Hover a cluster, click it, and
 * the sheet stayed pinned over the new view.
 */
describe('popups outliving their markers', () => {
  it('removes a marker\'s popup when the marker goes', () => {
    renderMap(<MapView showMapSearch={false} />);
    const map = maps[0];

    renderedFeatures = [cluster(70, 6)];
    act(() => map.fire('move'));
    const before = markerElements.length;
    expect(before, 'no marker was created').toBeGreaterThan(0);

    // The next pass reports nothing, so the cleanup removes the marker.
    renderedFeatures = [];
    act(() => map.fire('move'));

    // The popup created alongside it must have been removed too. Popups record
    // their own removal on the fake.
    expect(popupsRemoved.length, 'the popup outlived its marker').toBeGreaterThan(0);
  });
});

/*
  The basemap follows the interface theme until a visitor pins one.

  Reported as "clicking the dark mode should shift the map to minimal dark". It
  did not, deliberately: the basemap had been tied to the theme once and untied
  again, because a dark map makes a pale green pin harder to find. That trade is
  real, but it answered a question nobody asked -- the complaint is a bright
  white map filling a window someone has made dark.

  Following, not overriding: an explicit choice is a pin, and the theme stops
  moving the map once one exists. The last test is the one that matters, because
  a naive "theme sets the style" would pass the first two and silently discard
  the visitor's choice.
*/
describe('the basemap and the theme', () => {
  const styleOf = () => maps[0].options.style;

  afterEach(() => {
    window.localStorage.clear();
  });

  it('is the dark basemap when the interface is dark', async () => {
    window.localStorage.setItem('theme', 'dark');
    await act(async () => {
      renderMap(<MapView showMapSearch={false} />);
    });
    expect(styleOf()).toContain('/styles/dark');
  });

  it('is the minimal light basemap when the interface is light', async () => {
    window.localStorage.setItem('theme', 'light');
    await act(async () => {
      renderMap(<MapView showMapSearch={false} />);
    });
    expect(styleOf()).toContain('/styles/positron');
  });

  it('stops following once a style has been pinned', async () => {
    // The visitor picked Detailed and *then* dimmed the room. They did not ask
    // for a different map.
    window.localStorage.setItem('theme', 'dark');
    window.localStorage.setItem('tirtha.mapStyle.pinned', 'liberty');
    await act(async () => {
      renderMap(<MapView showMapSearch={false} />);
    });
    expect(styleOf()).toContain('/styles/liberty');
  });

  it('ignores the old key, which was written without being chosen', async () => {
    // `tirtha.mapStyle` was written on every mount by the previous effect, so
    // almost every returning visitor has a value there they never picked.
    // Reading it as a pin would mean the theme moved the map for nobody.
    window.localStorage.setItem('theme', 'dark');
    window.localStorage.setItem('tirtha.mapStyle', 'positron');
    await act(async () => {
      renderMap(<MapView showMapSearch={false} />);
    });
    expect(styleOf()).toContain('/styles/dark');
  });
});

/*
  Only the lab's name is the link, in every language.
 
  It began as one anchor around the whole phrase, to sidestep word order. Hindi
  and Odia put "smlab" at the *start* of the sentence, so any fix that linked
  "the last word" -- or that hardcoded an English split -- would link the wrong
  words in two languages out of three. `Trans` substitutes into each string's
  own `<link>`, which is why this is asserted per language rather than once.
*/
describe('the footer credit', () => {
  const credit = () => document.querySelector('.map-custom-credit');

  it('links the name and nothing else', async () => {
    await act(async () => {
      renderMap(<MapView showMapSearch={false} />);
    });
    const anchor = credit().querySelector('a');
    expect(anchor.textContent).toBe('smlab');
    expect(anchor).toHaveAttribute('href', 'https://www.niser.ac.in/~smishra/');
    // The sentence around it is present, and is not part of the link.
    expect(credit().textContent).toMatch(/Developed and maintained by/);
    expect(anchor.textContent).not.toMatch(/Developed/);
  });

  it.each(['or', 'hi'])('links the name in %s too, wherever it falls', async (language) => {
    await act(async () => {
      await i18n.changeLanguage(language);
    });
    await act(async () => {
      renderMap(<MapView showMapSearch={false} />);
    });
    const anchor = credit().querySelector('a');
    expect(anchor, `no credit link rendered in ${language}`).toBeTruthy();
    expect(anchor.textContent).toBe('smlab');
    // The sentence is translated, so it is longer than the link alone.
    expect(credit().textContent.length).toBeGreaterThan('smlab'.length);
  });
});

/*
  The camera belongs in the URL, and a camera in the URL wins.

  Reported as: zoom out on a site page, refresh, and you are zoomed back in.
  The route said which site and nothing said where you were looking, so every
  arrival re-framed the site -- the same shortfall `?model=closed` fixed for
  the viewer, one level down.

  The second test is the one that matters. Restoring the camera and then flying
  away from it is the bug wearing a different hat, and it is what a hash added
  without the precedence guard actually does.
*/
describe('the camera in the URL', () => {
  const konark = { position: [19.8876, 86.0945], zoom: 12 };

  afterEach(() => {
    window.location.hash = '';
  });

  it('is handed to MapLibre, namespaced so anchors still work', async () => {
    await act(async () => {
      renderMap(<MapView showMapSearch={false} />);
    });
    expect(maps[0].options.hash).toBe('map');
  });

  it('outranks the site framing on arrival', async () => {
    window.location.hash = '#map=11/19.9/86.1';
    await act(async () => {
      renderMap(<MapView searchTarget={konark} showMapSearch={false} />);
    });
    expect(
      maps[0].flyTo,
      'the restored camera was flown away from',
    ).not.toHaveBeenCalled();
  });

  it('still frames the site when the URL carries no camera', async () => {
    // The control. Suppressing every flight would satisfy the test above.
    await act(async () => {
      renderMap(<MapView searchTarget={konark} showMapSearch={false} />);
    });
    expect(maps[0].flyTo).toHaveBeenCalled();
  });

  it('flies again for a later deliberate choice of the same site', async () => {
    // The precedence is spent once. `default_zoom` frames a *first* arrival;
    // picking the site again afterwards is a request to go there.
    window.location.hash = '#map=11/19.9/86.1';
    const { rerender } = renderMap(
      <MapView searchTarget={konark} showMapSearch={false} />,
    );
    await act(async () => {});
    expect(maps[0].flyTo).not.toHaveBeenCalled();

    await act(async () => {
      rerender(
        <MapView
          searchTarget={{ position: [19.8876, 86.0945], zoom: 12 }}
          showMapSearch={false}
        />,
      );
    });
    expect(maps[0].flyTo).toHaveBeenCalled();
  });
});

/*
  A popup that opens on hover must not take focus.

  MapLibre focuses a popup's first focusable element on open, and defaults to
  it. For the cluster sheet that is the first tile, which then wears the 3px
  ring `index.css` gives every focused control -- reported as a blue bar across
  the first thumbnail. It came and went because `:focus-visible` only paints
  when the browser judges the interaction keyboard-like.
*/
describe('the cluster sheet', () => {
  it('does not steal focus when a pointer opens it', async () => {
    renderMap(<MapView showMapSearch={false} />);
    const map = maps[0];

    // The sheet's popup is built while the cluster marker is, so the map has
    // to have drawn a cluster before there is anything to assert about.
    renderedFeatures = [cluster(1, 12)];
    await act(async () => {
      map.fire('move');
    });

    const sheet = popupOptions.find((o) => o.className === 'cluster-sheet-popup');
    expect(sheet, 'no cluster sheet popup was constructed').toBeTruthy();
    expect(sheet.focusAfterOpen).toBe(false);
  });
});

describe('the MapLibre worker bootstrap', () => {
  /*
    Production is cross-origin isolated (COOP same-origin + COEP
    credentialless) and nginx sends no COEP on `/static/`, so a worker started
    from the asset URL itself is refused and the map draws nothing. The worker
    must start from a same-origin blob that imports the asset.
  */
  it('starts the worker from a blob that imports the absolute worker URL', async () => {
    const { maplibreWorkerBootstrap } = await import('../utils/maplibreWorker');
    const blobs = [];
    vi.stubGlobal('URL', Object.assign(class extends URL {}, {
      createObjectURL: (b) => { blobs.push(b); return 'blob:https://example.org/x'; },
    }));
    const url = maplibreWorkerBootstrap(
      '/project/tirtha/static/assets/maplibre-gl-worker-abc.js',
      'https://example.org/project/tirtha/',
    );
    expect(url).toBe('blob:https://example.org/x');
    expect(blobs[0].type).toBe('text/javascript');
    expect(await blobs[0].text()).toBe(
      'import "https://example.org/project/tirtha/static/assets/maplibre-gl-worker-abc.js";\n',
    );
  });
});
