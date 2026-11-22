/**
 * The site list is the map's largest response, and it was fetched twice on
 * every Odia and Hindi page load: `AppRoutes` sets the language from the URL in
 * an effect after this component has already started loading, and the new `t`
 * that arrives must not restart the fetch.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation, useParams } from 'react-router-dom';

// The map, the panel and the two dialogs are not what is under test here, and
// MapLibre needs a WebGL context jsdom does not have.
vi.mock('../components/MapView', () => ({ default: () => <div /> }));
vi.mock('../components/Sidebar', () => ({ default: () => <div /> }));
vi.mock('../components/RequestSiteModal', () => ({ default: () => null }));
vi.mock('../components/InfoModal', () => ({ default: () => null }));
// jsdom has no WebGL, and without it the page shows its list-only view. These
// tests are about the map-and-viewer page, so the probe says yes; the no-WebGL
// path has its own tests in `MapUnavailable.test.jsx`.
vi.mock('../utils/webgl', () => ({ hasWebGL: () => true }));
/*
  Only the two network calls are mocked. `resolveRun`, `viewableRuns` and
  `firstViewableRun` are the real ones: the run tests below are about what the
  page does with their answers -- which URL it writes, which model it hands the
  viewer -- and a stub that returned whatever a test wished for would let the
  page and the service disagree about which run is "the latest" without either
  test noticing.
*/
vi.mock('../services/sites.service', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    fetchSites: vi.fn(),
    fetchSite: vi.fn(),
  };
});
/*
  The viewer, wrapped rather than replaced. jsdom has no WebGL and no
  `ResizeObserver`, so the real component cannot mount its `<Canvas>` -- it
  throws into the error boundary, React caches the failure for the lazy chunk,
  and every later test in the file asserts against "The model could not be
  loaded". The canvas is drawn in every state but one, including the frame
  before the fetch lands, so the real component is rendered only for the state
  it is kept for here: resolved with nothing to draw, which is what the "failed
  fetch is not an absent model" test below exercises. Everywhere else the probe
  stands in and records the props `Home` chose, which is what the run tests are
  about.
*/
vi.mock('../components/ModelViewer3D', async (importOriginal) => {
  const actual = await importOriginal();
  const Real = actual.default;
  return {
    default: (props) =>
      props.resolved && !props.modelPath ? (
        <Real {...props} />
      ) : (
        <div
          data-testid="viewer-probe"
          data-model-path={props.modelPath ?? ''}
          data-camera={JSON.stringify(props.camera ?? null)}
        />
      ),
  };
});

import i18n from '../i18n/config';
import { fetchSite, fetchSites } from '../services/sites.service';
import Home from './Home';

// jsdom ships no `matchMedia`, and the sidebar asks it whether this is a phone
// before its first render.
window.matchMedia = () => ({ matches: false });

const renderHome = () =>
  render(
    <MemoryRouter>
      <Home />
    </MemoryRouter>,
  );

describe('Home', () => {
  beforeEach(() => {
    fetchSites.mockReset();
  });

  it('fetches the site list once when the language arrives after mount', async () => {
    fetchSites.mockResolvedValue([]);

    renderHome();
    await act(async () => {
      await i18n.changeLanguage('or');
    });
    await act(async () => {
      await i18n.changeLanguage('en');
    });

    expect(fetchSites).toHaveBeenCalledTimes(1);
  });

  it('restates the failure in the new language without fetching again', async () => {
    // What the effect remembers is *that* it failed; the sentence saying so is
    // looked up where it is rendered, which is what makes the first assertion
    // above safe to make.
    fetchSites.mockRejectedValue(new Error('offline'));
    // The effect logs the failure in development, which vitest counts as.
    vi.spyOn(console, 'error').mockImplementation(() => {});

    await act(async () => {
      renderHome();
    });
    expect(screen.getByRole('alert')).toHaveTextContent(
      'The sites could not be loaded. Please try again.',
    );

    await act(async () => {
      await i18n.changeLanguage('or');
    });
    expect(screen.getByRole('alert')).toHaveTextContent(
      i18n.getFixedT('or')('map.sitesFailed'),
    );
    expect(fetchSites).toHaveBeenCalledTimes(1);

    await act(async () => {
      await i18n.changeLanguage('en');
    });
  });
});

/**
 * The model opens over the map, not beside it (internal design notes).
 *
 * It used to fill a resizable column between the panel and the map, 420px by
 * default -- the thing this project exists to produce, shown in the narrowest
 * region of the screen. As a dialog it gets the room, and it owes what a dialog
 * owes: a role, a modal flag, and Escape.
 *
 * Closing it is deliberately *not* leaving the site. That was one action while
 * the viewer was a column, because the column and the selection appeared
 * together; over the map they are different questions, and the panel still has
 * a description and coordinates to read.
 */
describe('the model viewer', () => {
  const SITE = {
    id: 'kon',
    name: 'Konark Sun Temple',
    slug: 'India__Odisha__Puri__Konark',
    location: 'Puri, Odisha, India',
    lat: 19.8876,
    lng: 86.0945,
    mapStatus: 'published',
  };

  beforeEach(() => {
    fetchSites.mockReset();
    fetchSite.mockReset();
    fetchSites.mockResolvedValue([SITE]);
    fetchSite.mockResolvedValue({ ...SITE, runs: [] });
  });

  const openSite = async () => {
    await act(async () => {
      render(
        <MemoryRouter>
          <Home initialSiteId="kon" />
        </MemoryRouter>,
      );
    });
  };

  it('opens over the map as a dialog rather than in a column', async () => {
    await openSite();

    const panel = document.querySelector('.viewer-panel');
    expect(panel, 'the viewer did not open for a deep link').toBeTruthy();
    expect(panel).toHaveAttribute('role', 'dialog');
    expect(panel).toHaveAttribute('aria-modal', 'true');
    expect(panel.getAttribute('aria-label')).toMatch(/Konark Sun Temple/);
    // The column and its resize handle are gone, not merely restyled.
    expect(document.querySelector('.viewer-column')).toBeNull();
    expect(document.querySelector('.viewer-resize-handle')).toBeNull();
  });

  it('closes on Escape', async () => {
    await openSite();
    expect(document.querySelector('.viewer-overlay')).toBeTruthy();

    await act(async () => {
      fireEvent.keyDown(window, { key: 'Escape' });
    });

    expect(document.querySelector('.viewer-overlay')).toBeNull();
  });

  it('closes on a click outside the panel, and not on one inside it', async () => {
    await openSite();

    await act(async () => {
      fireEvent.click(document.querySelector('.viewer-panel'));
    });
    expect(
      document.querySelector('.viewer-overlay'),
      'a click on the model itself closed the viewer',
    ).toBeTruthy();

    await act(async () => {
      fireEvent.click(document.querySelector('.viewer-overlay'));
    });
    expect(document.querySelector('.viewer-overlay')).toBeNull();
  });
});

/**
 * A failed request is not an absent model (internal design notes).
 *
 * `resolveTempleModel`'s catch set `modelResolved: true` with no path, which is
 * exactly the shape that renders "Nobody has reconstructed this site yet". So a
 * dropped connection told the visitor a documented site had never been worked
 * on -- and for a site with five published models, said it had none.
 */
describe('when the site detail cannot be fetched', () => {
  const SITE = {
    id: 'kon',
    name: 'Konark Sun Temple',
    slug: 'India__Odisha__Puri__Konark',
    location: 'Puri, Odisha, India',
    lat: 19.8876,
    lng: 86.0945,
    mapStatus: 'published',
    placed: true,
  };

  beforeEach(() => {
    fetchSites.mockReset();
    fetchSite.mockReset();
    fetchSites.mockResolvedValue([SITE]);
  });

  it('says the request failed rather than that the site has no model', async () => {
    fetchSite.mockRejectedValue(new Error('offline'));

    await act(async () => {
      render(
        <MemoryRouter>
          <Home initialSiteId="kon" />
        </MemoryRouter>,
      );
    });
    // The lazy viewer chunk has to arrive before it can say anything. A single
    // microtask was enough until the module grew; `waitFor` is what actually
    // expresses "until it is there".
    await waitFor(() => {
      expect(
        document.querySelector('.model-viewer-empty'),
        'the viewer did not mount, so this asserts nothing',
      ).toBeTruthy();
    });

    const panel = document.querySelector('.model-viewer-empty');
    expect(panel.textContent).not.toMatch(/Nobody has reconstructed/i);
    expect(panel.textContent).toMatch(/could not be loaded|connection/i);
  });
});

/**
 * Closing the model and then refreshing put it straight back.
 *
 * `handleCloseViewer` keeps the site on purpose -- "I have seen the model" is
 * not "I am done with this site" -- but the only thing a fresh arrival can read
 * is the URL, and the URL said `/site/<id>/<slug>/`, which the route effect
 * treats as "open this site", and opening a site opens its viewer. So a
 * refresh, a shared link and a language switch all reopened a model the visitor
 * had deliberately shut. Reported twice: once as the refresh, once as "clicking
 * Odia opens the 3D viewer".
 *
 * `?model=closed` is the half of the sentence the URL was missing.
 */
describe('the closed model survives an arrival', () => {
  const SITE = {
    id: 'kon',
    slug: 'konark',
    name: 'Konark Sun Temple',
    lat: 19.8876,
    lng: 86.0945,
    mapStatus: 'published',
  };

  beforeEach(() => {
    fetchSites.mockReset();
    fetchSite.mockReset();
    fetchSites.mockResolvedValue([SITE]);
    fetchSite.mockResolvedValue({ ...SITE, runs: [] });
  });

  const arriveAt = async (entry) => {
    await act(async () => {
      render(
        <MemoryRouter initialEntries={[entry]}>
          <Home initialSiteId="kon" />
        </MemoryRouter>,
      );
    });
  };

  it('leaves the viewer shut when the URL says it is shut', async () => {
    await arriveAt('/site/kon/konark?model=closed');
    expect(
      document.querySelector('.viewer-panel'),
      'the model reopened on an arrival that said it was closed',
    ).toBeNull();
  });

  it('still selects the site, rather than showing a bare map', async () => {
    // The distinction that matters: closing the model must not deselect the
    // site. Without this, "leave the viewer shut" would also pass for a fix
    // that simply ignored the route.
    await arriveAt('/site/kon/konark?model=closed');
    await waitFor(() => expect(fetchSite).toHaveBeenCalled());
  });

  it('still opens for a plain site URL', async () => {
    // The control. An implementation that never opened the viewer would satisfy
    // both assertions above.
    await arriveAt('/site/kon/konark');
    expect(
      document.querySelector('.viewer-panel'),
      'a plain deep link stopped opening the model',
    ).toBeTruthy();
  });
});

/**
 * Which reconstruction the viewer shows, and how the URL names it.
 *
 * 21 sites carry more than one run and the viewer always opened the newest, so
 * every earlier version was published, ARK-minted and unreachable. The run id
 * is the third URL segment (`/site/<id>/<slug>/<runId>`), and these tests hold
 * the URL to account for it: a link to a run shows *that* run; a link to a run
 * that has since been hidden is corrected so the dead id is not shared onward;
 * a link to a run that exists but cannot be drawn is left alone, because it is
 * the run they asked for.
 *
 * Rendered through a route rather than with fixed props, because most of what
 * is under test is the page reacting to a URL it changed itself.
 */
describe('choosing a run', () => {
  const SITE = {
    id: 'kon',
    slug: 'konark',
    name: 'Konark Sun Temple',
    lat: 19.8876,
    lng: 86.0945,
    mapStatus: 'published',
  };
  const RUN_GS = {
    id: 'run-gs-2026',
    kind: 'GS',
    status: 'Manual',
    file_url: 'https://media.example/kon/2026.splat',
    ended_at: '2026-03-02T09:00:00Z',
    camera: { position: [0, 2, 8], target: [0, 0, 0], up: [0, 1, 0], fov: 50 },
  };
  const RUN_AV = {
    id: 'run-av-2025',
    kind: 'aV',
    status: 'Archived',
    file_url: 'https://media.example/kon/2025.glb',
    ended_at: '2025-07-14T06:30:00Z',
    camera: { position: [3, 1, 5], target: [0, 1, 0], up: [0, 1, 0], fov: 40 },
  };
  // Still on the queue: in the list, in the panel, and with nothing to draw.
  const RUN_PROCESSING = {
    id: 'run-processing',
    kind: 'GS',
    status: 'Processing',
    file_url: null,
    ended_at: null,
    camera: null,
  };

  beforeEach(() => {
    fetchSites.mockReset();
    fetchSite.mockReset();
    fetchSites.mockResolvedValue([SITE]);
    fetchSite.mockResolvedValue({ ...SITE, runs: [RUN_GS, RUN_AV, RUN_PROCESSING] });
  });

  // What `Approutes` does: the props come from the URL, so a navigation the
  // page performs is one it then has to answer to.
  function RoutedHome() {
    const { siteId, runId } = useParams();
    return <Home initialSiteId={siteId ?? null} initialRunId={runId ?? null} />;
  }
  // A `<span>`, not `<output>`: the latter has an implicit `status` role and
  // would be found by the queries below that look for the picker's notice.
  function LocationProbe() {
    const location = useLocation();
    return <span data-testid="location">{location.pathname + location.search}</span>;
  }

  const arriveAt = async (entry) => {
    await act(async () => {
      render(
        <MemoryRouter initialEntries={[entry]}>
          <Routes>
            <Route path="/" element={<RoutedHome />} />
            <Route path="/site/:siteId" element={<RoutedHome />} />
            <Route path="/site/:siteId/:slug" element={<RoutedHome />} />
            <Route path="/site/:siteId/:slug/:runId" element={<RoutedHome />} />
          </Routes>
          <LocationProbe />
        </MemoryRouter>,
      );
    });
  };

  // The probe is mounted before the fetch lands, with no path yet; what these
  // tests ask is which model *arrived*.
  const shownModel = async () => {
    const probe = screen.getByTestId('viewer-probe');
    await waitFor(() => expect(probe.getAttribute('data-model-path')).not.toBe(''));
    return {
      path: probe.getAttribute('data-model-path'),
      camera: JSON.parse(probe.getAttribute('data-camera')),
    };
  };
  const currentUrl = () => screen.getByTestId('location').textContent;

  it('offers the picker when the site has two viewable runs, and lists only those', async () => {
    await arriveAt('/site/kon/konark');

    const picker = await screen.findByRole('combobox');
    const options = picker.querySelectorAll('option');
    expect(options).toHaveLength(2);
    // The Processing run has no file and is not a version anyone can view.
    expect([...options].map((option) => option.value)).not.toContain(RUN_PROCESSING.id);
  });

  it('shows no picker for a site with a single viewable run', async () => {
    fetchSite.mockResolvedValue({ ...SITE, runs: [RUN_GS, RUN_PROCESSING] });
    await arriveAt('/site/kon/konark');
    await shownModel();

    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('hands the viewer the run the URL names, not the newest', async () => {
    await arriveAt(`/site/kon/konark/${RUN_AV.id}`);

    const model = await shownModel();
    expect(model.path).toBe(RUN_AV.file_url);
    expect(screen.getByRole('combobox')).toHaveValue(RUN_AV.id);
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('falls back from an id that is not among the runs, says so, and corrects the URL', async () => {
    await arriveAt('/site/kon/konark/run-withdrawn');

    const model = await shownModel();
    expect(model.path).toBe(RUN_GS.file_url);
    expect(screen.getByRole('status')).toHaveTextContent(i18n.t('site.runNotFound'));
    // A hidden run's id must not travel in a shared link -- 8 hidden runs still
    // hold ARKs, and a bookmark of this address must resolve tomorrow too.
    expect(currentUrl()).toBe('/site/kon/konark');
  });

  it('falls back from a run with no file, says so, and leaves the URL alone', async () => {
    await arriveAt(`/site/kon/konark/${RUN_PROCESSING.id}`);

    const model = await shownModel();
    expect(model.path).toBe(RUN_GS.file_url);
    expect(screen.getByRole('status')).toHaveTextContent(i18n.t('site.runUnsupported'));
    // It *is* the run they asked for; the viewer just cannot draw it yet.
    expect(currentUrl()).toBe(`/site/kon/konark/${RUN_PROCESSING.id}`);
  });

  it('switches version through the URL without fetching the site again', async () => {
    await arriveAt('/site/kon/konark');
    expect((await shownModel()).path).toBe(RUN_GS.file_url);

    await act(async () => {
      fireEvent.change(screen.getByRole('combobox'), { target: { value: RUN_AV.id } });
    });

    expect(currentUrl()).toBe(`/site/kon/konark/${RUN_AV.id}`);
    await waitFor(() => expect(screen.getByTestId('viewer-probe')).toHaveAttribute('data-model-path', RUN_AV.file_url));
    // The run list arrived with the site; changing version is a choice among
    // what is already here, not a reason to ask the server again.
    expect(fetchSite).toHaveBeenCalledTimes(1);
  });

  it('changes the model format and its camera together when switching GS to aV', async () => {
    await arriveAt(`/site/kon/konark/${RUN_GS.id}`);
    const before = await shownModel();
    expect(before.path).toMatch(/\.splat$/);
    expect(before.camera).toEqual(RUN_GS.camera);

    await act(async () => {
      fireEvent.change(screen.getByRole('combobox'), { target: { value: RUN_AV.id } });
    });

    await waitFor(() => expect(screen.getByTestId('viewer-probe')).toHaveAttribute('data-model-path', RUN_AV.file_url));
    const after = await shownModel();
    expect(after.path).toMatch(/\.glb$/);
    // Every run frames its own camera; a mesh shown through the splat's camera
    // is a temple seen from inside its own floor.
    expect(after.camera).toEqual(RUN_AV.camera);
  });
});
