import React, { useState, useEffect, Suspense, lazy, useRef, useCallback } from 'react';
import Icon from '../components/Icon';
import { useTranslation } from 'react-i18next';
import Sidebar from '../components/Sidebar';
import MapView from '../components/MapView';
import RequestSiteModal from '../components/RequestSiteModal';
import InfoModal from '../components/InfoModal';
import RunPicker from '../components/RunPicker';
import { fetchDocument } from '../services/documents.service';
import { useLocation, useNavigate } from 'react-router-dom';

import { fetchSite, fetchSites, resolveRun, viewableRuns } from '../services/sites.service';
import { reverseGeocode, getReadableLocationName } from '../utils/geocoding';
import '../styles/Home.css';

import ErrorBoundary from '../components/ErrorBoundary';
import MapUnavailable from '../components/MapUnavailable';
import { hasWebGL } from '../utils/webgl';
import { readStored, writeStored } from '../utils/storage';
import { useModalBehaviour } from '../hooks/useModalBehaviour';

const ModelViewer3D = lazy(() => import('../components/ModelViewer3D'));
/*
  The same boundary as the viewer. `axios`, `browser-image-compression` and
  `exifr` are run by this dialog and by nothing else in `src/`, and importing
  it statically put all three in front of every visitor who opens the map and
  never clicks Contribute -- along with the module-eval of
  `imageValidation.js`, which registers HEIF brands at page load. Measured:
  eager payload fell from 1,624,075 to 1,427,126 raw (460,486 to 391,979
  gzipped, -15%) and the entry chunk more than halved, 349.6 kB to 157.3 kB.
  It is the defect the `manualChunks` comment in `vite.config.js` records for
  three.js, one component over.
*/
const ContributeModal = lazy(() => import('../components/ContributeModal'));

const DEFAULT_CONTRIBUTE_TARGET = { title: 'Tirtha', siteName: null };

/**
 * What the selected temple records about its runs, given the run list and the
 * id the URL asked for. One function, because the same fields are written from
 * two places -- after the fetch, and again when only the URL's run changes --
 * and a field set in one and forgotten in the other is a picker that says one
 * version while the canvas draws another.
 *
 * `requestedRunId` is "the run id the URL carries that this state answers to",
 * which is how the route effect knows whether a URL change still needs work.
 * It is `null` on a `missing` fallback, even though an id was asked for: the
 * caller is about to rewrite the URL without that id, and storing the withdrawn
 * id here would make the effect see the bare URL disagree with state, resolve
 * again, and wipe the notice the redirect was meant to keep on screen.
 */
function runFields(runs, runId) {
  const { run, reason } = resolveRun(runs, runId);
  return {
    reason,
    fields: {
      runs,
      viewableRuns: viewableRuns(runs),
      runId: run?.id ?? null,
      requestedRunId: reason === 'missing' ? null : (runId ?? null),
      runNotice: reason,
      modelPath: run?.file_url ?? null,
      modelKind: run?.kind ?? null,
      /*
        The published file's size and format, where the API reports them.
        Optional on purpose: an API that predates the fields leaves them
        undefined, and the viewer then simply does not know the size.
      */
      modelBytes: typeof run?.model_bytes === 'number' ? run.model_bytes : null,
      modelFormat: run?.model_format ?? null,
      camera: run?.camera ?? null,
    },
  };
}

/**
 * `initialSiteId` comes from the route (`/app/site/<id>/...`). Django renders
 * per-site Open Graph tags for that path, so a shared link is a real URL an
 * unfurl crawler can read (internal design notes) -- but the client router had only
 * `/`, so opening one landed on the bare map and the site named in the URL was
 * never selected.
 */
function Home({ initialSiteId = null, initialRunId = null }) {
  // `home` was in this list and never read. Its file held seven invented
  // temples with invented descriptions -- none from the API -- so it is gone.
  const { t } = useTranslation(['sidebar', 'common']);
  const navigate = useNavigate();
  const location = useLocation();
  /*
    The URL owns the selection, and now it owns the model too.

    Closing the viewer kept the site -- deliberately, they are different
    questions -- but left the URL saying `/site/<id>/<slug>/`, which is the only
    thing a fresh arrival can read. So a refresh, a language switch or a shared
    link all ran the route effect below, which called `selectSite`, which opens
    the viewer. You closed the model and it came straight back.

    `?model=closed` is the missing half of that sentence. It is in the URL
    rather than in `sessionStorage` because the URL is already where this app
    keeps "which site", because it survives the full page load a language switch
    performs, and because it makes the closed state shareable like the open one.
  */
  const modelClosed = new URLSearchParams(location.search).get('model') === 'closed';
  /**
   * The panel starts open on a wide screen and closed on a narrow one, and
   * remembers what the visitor last chose.
   *
   * At 380 px it covers most of a phone, hiding the map -- which is the thing
   * the visitor came for. Defaulting it open there meant every mobile visit
   * began by dismissing it.
   *
   * Storage goes through `utils/storage`, which cannot throw -- see the note
   * there for why that is not merely defensive.
   */
  const [sidebarVisible, setSidebarVisible] = useState(() => {
    const stored = readStored('tirtha.sidebar');
    if (stored !== null) return stored === 'open';
    return !window.matchMedia('(max-width: 768px)').matches;
  });

  useEffect(() => {
    writeStored('tirtha.sidebar', sidebarVisible ? 'open' : 'closed');
  }, [sidebarVisible]);
  const [isViewerOpen, setIsViewerOpen] = useState(false);
  /*
    Asked once. Without WebGL neither the map nor the viewer can draw, and the
    page used to find that out by MapLibre throwing into the page-level error
    boundary -- which replaced the list and Contribute as well. `mapFailed`
    covers the other route there: WebGL present, but the map still failing to
    start (a blocklisted driver, a lost context at init).
  */
  const [webglAvailable] = useState(() => hasWebGL());
  const [mapFailed, setMapFailed] = useState(false);
  const mapUnavailable = !webglAvailable || mapFailed;
  const [selectedTemple, setSelectedTemple] = useState(null);
  const [isContributeOpen, setIsContributeOpen] = useState(false);
  /*
    True from the first time the dialog is opened, and never false again. The
    mount is gated on this rather than on `isContributeOpen` because the modal
    clears its form on the open->closed transition *during render*, and that is
    where it revokes the preview object URLs of the files it was holding.
    Unmounting on close would skip it and leak them for the life of the page.
  */
  const [isContributeMounted, setIsContributeMounted] = useState(false);
  const [contributeTarget, setContributeTarget] = useState(DEFAULT_CONTRIBUTE_TARGET);
  const [isRequestSiteOpen, setIsRequestSiteOpen] = useState(false);
  const [isSelectingLocation, setIsSelectingLocation] = useState(false);
  const [selectedPosition, setSelectedPosition] = useState(undefined);
  const [mapCoordinates, setMapCoordinates] = useState(null);
  const [searchTarget, setSearchTarget] = useState(null);
  const [siteList, setSiteList] = useState([]);
  const [isSitesLoading, setIsSitesLoading] = useState(true);
  const [sitesFailed, setSitesFailed] = useState(false);
  const [infoModalOpen, setInfoModalOpen] = useState(false);
  const [infoModalType, setInfoModalType] = useState(null); // 'about' or 'privacy'
  /*
    A canonical document being read in place, rather than on its page.

    Separate from `infoModalType` because the two are different things wearing
    one dialog: `menuItems` carries the app's own translated prose, and this
    carries the Terms, the Privacy Policy and the Acknowledgment as the server
    renders them. Keeping them apart is what stops the second becoming a
    paraphrase of the first, which is the failure `shell.document` was written
    to end.
  */
  const [documentModal, setDocumentModal] = useState(null);
  const viewerRef = useRef(null);
  const [isViewerFullscreen, setIsViewerFullscreen] = useState(false);
  
  // Shared/Lifted Search Bar State
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearchFocused, setIsSearchFocused] = useState(false);
  const [searchActiveIndex, setSearchActiveIndex] = useState(-1);


  const showSidebar = sidebarVisible;

  /*
    Looked up by `id`, not by matching the English title.

    `menuItems.find(item => item.title === 'What is Project Tirtha?')` cannot
    match once the title is translated, so in Odia and Hindi the About panel
    would open empty -- the strings were there and the lookup could not reach
    them. A stable id is not a translated string.
  */
  const menuItems = t('sidebar:menuItems', { returnObjects: true });
  const activeModalItem = Array.isArray(menuItems)
    ? menuItems.find((item) => item.id === infoModalType)
    : null;

  useEffect(() => {
    const controller = new AbortController();
    const loadSites = async () => {
      setIsSitesLoading(true);
      setSitesFailed(false);
      try {
        const sites = await fetchSites(controller.signal);
        setSiteList(sites);
      } catch (error) {
        if (error?.name === 'AbortError') return;
        setSiteList([]);
        setSitesFailed(true);
        if (import.meta.env.DEV) console.error('Failed to fetch sites:', error);
      } finally {
        if (!controller.signal.aborted) setIsSitesLoading(false);
      }
    };
    loadSites();
    return () => controller.abort();
    /*
      No `t` in the dependencies: what is remembered here is *that* the fetch
      failed, and the sentence saying so is looked up where it is rendered, so a
      language change still restates it in the new language without touching
      this effect.

      Depending on `t` re-ran the whole thing. `AppRoutes` sets the language
      from the URL in an effect after mount -- parent effects run after child
      ones -- so on `/or/` and `/hi/` this had already started loading when
      `changeLanguage` handed out a new `t`: the cleanup aborted the sites
      request mid-flight and an identical second one went out, on every page
      load in two of the three languages.
    */
  }, []);


  /**
   * Fetch the site's runs and hand the viewer a model it can draw.
   *
   * The list endpoint carries no runs, so `modelPath` was `undefined` for every
   * site and the viewer fell through to its debug cube -- a spinning red box
   * shown for all 59 sites, with "Path: undefined" over it.
   *
   * The newest run is not always the right one: a run whose artifact this
   * viewer cannot read is worse than an older one it can, and every run stays
   * listed with its own ARK regardless.
   */
  /*
    A run id that names nothing is taken out of the URL. Of the runs that have
    been hidden since publication, 8 still hold ARKs, so their ids are out there
    in links; a visitor following one gets the newest model with a notice, and
    must not then copy an address that names the withdrawn run onward. `replace`
    because Back should return to wherever the link was followed from, not to
    the dead address.

    An `unviewable` id stays. That *is* the run the link named -- Processing
    still, or in a format this build cannot draw -- and the URL is telling the
    truth; the notice explains why the canvas shows something else.
  */
  const dropRunFromUrl = (temple) => {
    navigate(
      {
        pathname: `/site/${temple.id}/${encodeURIComponent(temple.slug)}`,
        // Kept, or a visitor arriving at a withdrawn run with `?model=closed`
        // would have the flag stripped and the next refresh reopen the viewer.
        search: location.search,
      },
      { replace: true },
    );
  };

  const resolveTempleModel = async (temple, runId = null) => {
    if (!temple?.id) return;
    try {
      const detail = await fetchSite(temple.id);
      /*
        A URL naming a specific run means that run: `/app/site/<id>/<slug>/<runId>/`
        is the permalink the ARK resolves to, and it must not quietly show a
        newer one. `resolveRun` says when it had to anyway, and why.
      */
      const { reason, fields } = runFields(detail.runs || [], runId);
      setSelectedTemple((current) => {
        if (!current || current.id !== temple.id) return current;
        return {
          ...current,
          ...fields,
          // Distinguishes "nothing published yet" from "still loading", which
          // the viewer needs in order to say the right thing.
          modelResolved: true,
          modelFetchFailed: false,
        };
      });
      if (reason === 'missing') dropRunFromUrl(temple);
    } catch {
      /*
        A failed request is not an absent model.

        This set `modelResolved: true` with no path, which is exactly the shape
        that renders "Nobody has reconstructed this site yet" -- so a dropped
        connection told the visitor a documented site had never been worked on,
        and a site with five published models said it had none. The flag lets
        the viewer say which of the two happened, and offer a retry for the one
        that can be retried.
      */
      setSelectedTemple((current) =>
        current && current.id === temple.id
          ? {
              ...current,
              modelPath: null,
              modelResolved: true,
              modelFetchFailed: true,
              // Recorded on failure as well, or the route effect below reads a
              // URL that names a run against state that never answered it, and
              // re-resolves on every render for as long as the server is down.
              requestedRunId: runId ?? null,
            }
          : current,
      );
    }
  };

  /*
    Switch version without a request. The run list came with the site, so a
    change to the URL's run segment -- from the picker, or from Back -- is a
    choice among runs already in hand; refetching would charge a round trip for
    information the page has.

    Nothing to choose from after a failed fetch, so nothing is done: the retry
    button re-runs `resolveTempleModel` with the URL's run, which is the right
    answer to "I asked for a version and the list never arrived".
  */
  const showRun = (temple, runId) => {
    if (!temple?.modelResolved || temple.modelFetchFailed) return;
    const { reason, fields } = runFields(temple.runs || [], runId);
    setSelectedTemple((current) =>
      current && current.id === temple.id ? { ...current, ...fields } : current,
    );
    if (reason === 'missing') dropRunFromUrl(temple);
  };

  /*
    A push, not a replace: each version is somewhere Back can return to, the
    same way it returns to the previous site. The slug is always there --
    `verbose_id` is generated for every Site -- so this is the form the server
    renders Open Graph tags for, and the form an ARK resolves to.
  */
  const handleSelectRun = (runId) => {
    if (!selectedTemple?.id) return;
    navigate(
      `/site/${selectedTemple.id}/${encodeURIComponent(selectedTemple.slug)}/${encodeURIComponent(runId)}`,
    );
  };


  const resolveTempleLocation = async (temple) => {
    const lat = temple?.position?.[0] ?? temple?.lat;
    const lng = temple?.position?.[1] ?? temple?.lng;

    if (!Number.isFinite(Number(lat)) || !Number.isFinite(Number(lng))) return;

    try {
      const geodata = await reverseGeocode(Number(lat), Number(lng));
      const readableLocation = getReadableLocationName(geodata, {
        lat: Number(lat),
        lng: Number(lng),
      });

      if (!readableLocation) return;

      setSelectedTemple((current) => {
        if (!current || current.id !== temple.id) return current;

        return {
          ...current,
          location: readableLocation,
          resolvedLocationName: readableLocation,
        };
      });
    } catch (error) {
      if (import.meta.env.DEV) {
        console.error('Failed to resolve marker location:', error);
      }
    }
  };


  /**
   * Open a site: panel, map camera, viewer, and the URL that names it.
   *
   * The route effect, the marker click and the search result each used to run
   * their own subset of this in their own order, and the differences were the
   * bugs. The marker path navigated and the search path did not — so with
   * `/site/A` open, picking B from search set `selectedTemple` to B while
   * `initialSiteId` stayed A, the route effect noticed the mismatch and put A
   * straight back. You searched for Konark and landed on Puri.
   */
  const selectSite = (
    temple,
    { navigateTo = true, runId = null, zoom = 12, openViewer = true } = {},
  ) => {
    if (!temple) return;

    /*
      An unlocated site opens its panel and moves no camera. There is nowhere to
      fly to, and `[null, null]` reaching `flyTo` is a NaN centre.
    */
    const placed = temple.placed !== false && Number.isFinite(temple.lat);
    setSelectedTemple({ ...temple, position: placed ? [temple.lat, temple.lng] : null });
    if (placed) setSearchTarget({ position: [temple.lat, temple.lng], zoom });
    // Assigned, not forced true: arriving at a site whose URL says the model is
    // closed has to be able to leave it closed.
    // Without WebGL the overlay could only say so over the panel that holds
    // the details; the panel is what is useful, so it stays uncovered.
    setIsViewerOpen(openViewer && webglAvailable);
    resolveTempleLocation(temple);
    resolveTempleModel(temple, runId);

    /*
      The URL follows the selection, so copying the address bar shares the
      temple rather than the map, and Back closes the panel instead of leaving
      the app. With the slug when there is one: `site/<id>/<verbose_id>/` is
      the form the server renders per-site Open Graph tags for.
    */
    if (navigateTo && temple.id) {
      navigate(
        temple.slug
          ? `/site/${temple.id}/${encodeURIComponent(temple.slug)}`
          : `/site/${temple.id}`,
      );
    }
  };

  /*
    Open the site the URL names. Waits for the list because the marker, the
    panel and the map camera all key off the same normalised record -- selecting
    a half-built one from the detail endpoint alone would leave the map showing
    the whole of India while the panel described a temple in Puri.

    If the id is not in the list the route is a dead link (a deleted site, a
    typo, a hidden one): the map is the honest thing to show, and the URL is
    corrected so the bad id is not carried into a share or a bookmark.
  */
  useEffect(() => {
    /*
      The inverse branch, which did not exist: no id in the URL means no
      selection. Without it nothing ever *cleared* the selection in response to
      the route, so every close path had to clear it by hand -- and the one that
      did raced this effect.
    */
    if (!initialSiteId) {
      /*
        A pasted coordinate is not a site page, so the URL never names it.

        `SearchBar` offers a Google Maps link or a bare "lat, lng" as its own
        suggestion (`isCustomLocation`), so the contribute flow can start from
        somewhere not yet in the archive. Selecting one sets `selectedTemple`
        without navigating -- and this branch then nulled it on the very next
        commit, because the URL has no site in it. The red pin appeared and
        vanished. From a site page it was worse: the effect below re-selected
        the old temple and reopened the 3D viewer over the request dialog.
      */
      if (selectedTemple?.isCustomLocation) return;
      if (selectedTemple) {
        // Same legitimate case as the `selectSite` call below: reconciling
        // React state with the URL, which is outside it.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setSelectedTemple(null);
        setIsViewerOpen(false);
        setIsViewerFullscreen(false);
      }
      return;
    }
    if (siteList.length === 0) return;
    if (selectedTemple?.id === initialSiteId) {
      /*
        Same site, different run. This used to return here unconditionally, so
        changing only the run segment of the URL re-resolved nothing: the picker
        navigated, the address bar changed, and the canvas kept drawing the old
        version.

        Compared against `requestedRunId`, not `runId`. `runId` is the run that
        was *resolved*, and after a fallback it differs from what the URL asked
        for -- so a comparison on it would find a mismatch, resolve again, fall
        back again, and loop for as long as the URL named an unviewable run.
        `requestedRunId` is the question this state already answered; when it
        matches the URL there is nothing new to ask.

        Not before `modelResolved`: until the fetch lands there is no list to
        choose from, and the fetch was started with the URL's run anyway.
      */
      if (
        selectedTemple.modelResolved &&
        (selectedTemple.requestedRunId ?? null) !== (initialRunId ?? null)
      ) {
        showRun(selectedTemple, initialRunId);
      }
      return;
    }

    const site = siteList.find((candidate) => candidate.id === initialSiteId);
    if (!site) {
      navigate('/', { replace: true });
      return;
    }

    /*
      This is the legitimate use of an effect: reconciling React state with two
      things outside it -- the URL and a completed fetch. `setState` here is
      what the rule warns about, and it is the point. The alternative, selecting
      inside `loadSites`, would not re-run when the route changes under a
      client-side navigation.

      `navigateTo: false`: we are *responding* to the URL, so navigating to it
      again would be a redundant history entry for every deep link.
    */
    selectSite(site, {
      navigateTo: false,
      runId: initialRunId,
      openViewer: !modelClosed,
    });
    // `selectSite` and `showRun` are stable for the life of the component;
    // listing them would re-run this on every render. `modelResolved` and
    // `requestedRunId` are listed because a run change that arrives while the
    // fetch is in flight has to be picked up when the fetch lands.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    initialSiteId,
    initialRunId,
    siteList,
    selectedTemple?.id,
    selectedTemple?.modelResolved,
    selectedTemple?.requestedRunId,
    modelClosed,
    navigate,
  ]);

  const handleMarkerClick = (temple) => selectSite(temple);


  /*
    Close the model, keep the site.

    These were one action while the viewer was a column, because the column and
    the selection appeared together. Over the map they are different questions:
    "I have seen the model" is not "I am done with this site", and the panel
    still has its description, coordinates and runs to read. The panel's back
    arrow is what leaves the site.
  */
  /*
    Read a canonical document without leaving the map.

    The title is taken from the link that was clicked so the dialog has a
    heading from the first frame; the server sends its own, which replaces it
    once the body arrives. Without that the dialog opens captionless and the
    heading appears a beat later, which reads as a glitch.
  */
  const openDocument = useCallback(
    async (id, fallbackTitle = '') => {
      setInfoModalType(null);
      setDocumentModal({ id, title: fallbackTitle, html: '', loading: true, error: null });
      setInfoModalOpen(true);
      try {
        const doc = await fetchDocument(id);
        setDocumentModal({
          id,
          title: doc.title || fallbackTitle,
          html: doc.html || '',
          loading: false,
          error: null,
        });
      } catch {
        // The page still exists and still works, so say so rather than only
        // reporting a failure.
        setDocumentModal({
          id,
          title: fallbackTitle,
          html: '',
          loading: false,
          error: t('error.documentUnavailable'),
        });
      }
    },
    [t],
  );

  const handleCloseViewer = () => {
    setIsViewerOpen(false);
    setIsViewerFullscreen(false);
    /*
      Record it in the URL, so the next arrival at this address knows. `replace`
      because opening and closing a model is not somewhere to go Back to.

      Only on a site page: a custom location has no site URL to annotate, and
      writing a query onto `/` would put `?model=closed` on the map itself.
    */
    if (initialSiteId) {
      navigate({ pathname: location.pathname, search: '?model=closed' }, { replace: true });
    }
  };

  // The inverse. Without this the flag would survive reopening, and the next
  // refresh would shut a model the visitor had deliberately opened again.
  const handleOpenViewer = () => {
    setIsViewerOpen(true);
    if (modelClosed) {
      navigate({ pathname: location.pathname, search: '' }, { replace: true });
    }
  };

  /*
    Leaving a site is a *navigation*, and nothing else.

    This used to clear the state and navigate in the same tick. The route effect
    then ran once with `selectedTemple === null` while `initialSiteId` still
    held the old id, its `selectedTemple?.id !== initialSiteId` guard passed,
    and it called `selectSite` again -- which opens the viewer. So the back
    arrow re-opened the model over a list with no rows, and the only way out was
    a page reload. Browser Back failed the same way for the same reason.

    The URL owns the selection now: this navigates, and the effect below does
    the rest. `replace` because a visitor who opens five sites should not have
    to press Back six times to leave the page they arrived from.
  */
  const handleDeselectSite = () => {
    navigate('/', { replace: true });
  };
  /*
    The same trap, restore and Escape the other three dialogs use. Written out a
    fourth time it would be a fourth thing to keep correct; the hook's own
    comment makes that point, and this is the dialog it was not yet applied to.
  */
  useModalBehaviour(isViewerOpen && Boolean(selectedTemple), handleCloseViewer, viewerRef);

  const handleSearchSelect = (temple) => {
    if (!temple) return;

    if (temple.isCustomLocation) {
      // A pasted coordinate, not a site: this opens the request form rather
      // than a viewer, and there is no URL to navigate to.
      setSelectedTemple({ ...temple, position: [temple.lat, temple.lng], fromSearch: true });
      setSelectedPosition({ lat: temple.lat, lng: temple.lng });
      setMapCoordinates({ lat: temple.lat, lng: temple.lng });
      setIsSelectingLocation(false);
      setIsRequestSiteOpen(true);
      return;
    }

    selectSite(temple);
  };

  const openTempleContribute = (temple) => {
    setContributeTarget({
      title: temple?.name || 'Tirtha',
      siteName: temple?.name || null,
      // The upload endpoints identify a site by `verbose_id` (`mesh_vid`),
      // which the API returns as `slug` -- not by the ShortUUID `id`.
      siteSlug: temple?.slug || null,
    });
    setIsContributeMounted(true);
    setIsContributeOpen(true);
  };

  const resetRequestSelection = () => {
    setIsSelectingLocation(false);
    setSelectedPosition(undefined);
  };

  /*
    No seed here. `DEFAULT_POSITION` is the centre of India, so starting the
    picker after zooming anywhere put the pin a thousand kilometres off-screen
    along with the panel that carries Confirm and Cancel. `MapView` seeds it
    from the map's own centre, because it is the only thing that knows where the
    map is; a coordinate the visitor has already chosen still wins.
  */
  const handleStartLocationSelection = () => {
    setSelectedPosition(mapCoordinates ?? undefined);
    setIsSelectingLocation(true);
    setIsRequestSiteOpen(false);
  };

  const handleConfirmLocation = () => {
    /*
      No fallback. This read `selectedPosition || DEFAULT_POSITION`, so
      confirming with nothing chosen silently submitted the centre of India as
      the site's location -- a wrong coordinate recorded confidently, which is
      worse than no coordinate. The button is only reachable while a pin exists;
      if that ever stops being true, do nothing rather than invent a place.
    */
    if (!selectedPosition) return;
    setMapCoordinates(selectedPosition);
    setIsSelectingLocation(false);
    setIsRequestSiteOpen(true);
  };

  const handleCloseRequestSite = () => {
    setIsRequestSiteOpen(false);
    resetRequestSelection();
    setMapCoordinates(null);
  };

  const handleCancelLocationSelection = () => {
    resetRequestSelection();
  };

  const mapFallback = (
    <MapUnavailable
      sites={siteList}
      onSelect={(site) => {
        // The details are in the panel, so a phone (where it starts closed)
        // has to be shown it.
        setSidebarVisible(true);
        selectSite(site);
      }}
      onContribute={openTempleContribute}
    />
  );

  return (
    <div className="home-container">
      {/*
        The page needs a top-level heading (WCAG 1.3.1 / 2.4.6) and a `main`
        landmark for the skip link to land on. The map is the page, so the
        heading is visually hidden rather than drawn -- it exists for the
        document outline and for anyone navigating by headings, which is a
        common way to move around a page without seeing it.
      */}
      <h1 className="sr-only">{t('app.name')}</h1>

      <button
        type="button"
        className={`sidebar-toggle-btn ${sidebarVisible ? 'sidebar-visible' : 'sidebar-hidden'}`}
        onClick={() => setSidebarVisible((prev) => !prev)}
        aria-label={sidebarVisible ? t('a11y.closeSidebar') : t('a11y.openSidebar')}
        title={sidebarVisible ? t('a11y.closeSidebar') : t('a11y.openSidebar')}
      >
        <Icon name="menu" />
      </button>

      <Sidebar
        isVisible={showSidebar}
        onMobileClose={() => setSidebarVisible(false)}
        templeList={siteList}
        onSearchSelect={handleSearchSelect}
        selectedTemple={selectedTemple}
        onCloseTemple={handleDeselectSite}
        onOpenViewer={handleOpenViewer}
        isViewerOpen={isViewerOpen}
        onContributeClick={openTempleContribute}
        onToggle={() => setSidebarVisible((prev) => !prev)}
        searchQuery={searchQuery}
        setSearchQuery={setSearchQuery}
        isSearchFocused={isSearchFocused}
        setIsSearchFocused={setIsSearchFocused}
        searchActiveIndex={searchActiveIndex}
        setSearchActiveIndex={setSearchActiveIndex}
        onDocumentClick={openDocument}
      />

      {/*
        The model, over the map rather than beside it (internal design notes).

        It used to fill a resizable column between the panel and the map,
        defaulting to 420px -- so the thing this project exists to produce was
        shown in the narrowest region of the screen, and the map it covered was
        squeezed rather than freed. A 3D model is not a sidebar's worth of
        content.

        Now: a large centred surface with the map dimmed behind it, dismissed
        with Escape or a click outside, using the same `useModalBehaviour` the
        other dialogs use so the focus trap and the focus restore are not a
        fourth hand-written copy. The site's *text* -- description, coordinates,
        runs, contributors -- stays in the panel, which is where reading
        belongs; the model gets the room.

        Closing it keeps the site selected, so the panel still reads and the
        button below reopens the model. Only the panel's back arrow deselects.
      */}
      {isViewerOpen && selectedTemple && (
        <div
          className="viewer-overlay"
          role="presentation"
          onClick={(event) => {
            if (event.target === event.currentTarget) handleCloseViewer();
          }}
        >
          <div
            ref={viewerRef}
            className={`viewer-panel ${isViewerFullscreen ? 'viewer-fullscreen' : ''}`}
            role="dialog"
            aria-modal="true"
            aria-label={t('a11y.modelOf', { site: selectedTemple.name })}
          >
            <div className="viewer-panel-header">
              {/*
                A two-tab strip with one real tab. The second, "Street View",
                rendered "coming soon..." for every site -- there is no
                street-view source in the pipeline, no field for one on `Run`,
                and no unit in internal design notes that adds one. A tab that can never
                be selected usefully is a promise the platform does not keep, so
                the strip is now the site's name.
              */}
              <h2 className="viewer-title">{selectedTemple.name}</h2>
              {/*
                Which version. In the header rather than the panel because it
                changes what the canvas draws, and a control should sit with
                the thing it controls. The wrapper is the header's slot: how
                much of the header the picker may take is the header's decision
                (`Home.css`), not the picker's. `RunPicker` renders nothing for
                the 38 single-run sites, and the slot is hidden when empty so it
                does not cost those sites a flex gap.
              */}
              <div className="viewer-run-picker">
                <RunPicker
                  runs={selectedTemple.viewableRuns ?? []}
                  selectedRunId={selectedTemple.runId ?? null}
                  onSelect={handleSelectRun}
                  notice={selectedTemple.runNotice ?? null}
                />
              </div>
              <div className="viewer-header-actions">
                <button
                  type="button"
                  className="viewer-action-btn"
                  onClick={() => setIsViewerFullscreen((prev) => !prev)}
                  aria-label={t(isViewerFullscreen ? 'a11y.exitFullscreen' : 'a11y.fullscreen')}
                  title={t(isViewerFullscreen ? 'a11y.exitFullscreen' : 'a11y.fullscreen')}
                >
                  <Icon name={isViewerFullscreen ? 'fullscreen_exit' : 'fullscreen'} />
                </button>
                <button
                  type="button"
                  className="viewer-close-btn"
                  onClick={handleCloseViewer}
                  aria-label={t('a11y.closeViewer')}
                >
                  <Icon name="close" />
                </button>
              </div>
            </div>
            <div className="viewer-model-wrapper">
              {/*
                Scoped to the viewer, not the page. `<Suspense>` covers the
                chunk arriving; only a boundary covers it failing to. Keeping it
                local means a viewer that cannot load leaves the rest of the page
                -- the site's details, its runs, the map behind it -- usable,
                instead of blanking everything.
              */}
              {/*
                Keyed by site and run: a boundary that caught one model's
                failure otherwise stays latched, and every site opened after it
                showed the same failure over its own working model.
              */}
              {!webglAvailable && selectedTemple.modelPath ? (
                <div className="viewer-loading" role="alert">
                  {t('viewer.noWebGL')}
                </div>
              ) : (
              <ErrorBoundary
                key={`${selectedTemple.id ?? ''}:${selectedTemple.runId ?? ''}`}
                fallback={() => (
                  <div className="viewer-loading" role="alert">
                    {t('site.viewerFailed')}
                  </div>
                )}
              >
                <Suspense
                  fallback={<div className="viewer-loading">{t('site.viewerLoading')}</div>}
                >
                  <ModelViewer3D
                    modelPath={selectedTemple.modelPath}
                    modelBytes={selectedTemple.modelBytes ?? null}
                    camera={selectedTemple.camera}
                    siteName={selectedTemple.name}
                    resolved={Boolean(selectedTemple.modelResolved)}
                    fetchFailed={Boolean(selectedTemple.modelFetchFailed)}
                    mapStatus={selectedTemple.mapStatus}
                    onRetry={() => resolveTempleModel(selectedTemple, initialRunId)}
                    onContributeClick={() => openTempleContribute(selectedTemple)}
                  />
                </Suspense>
              </ErrorBoundary>
              )}
            </div>
          </div>
        </div>
      )}


      {/* Col 3: Map */}
      <div className="main-content">
        <div className="content-area">
          <div className="hero-section">
            {/*
              The map has its own boundary. It used to share the page-level one,
              so a map that could not start took the list and Contribute down
              with it. The fallback is the same list-only view a browser without
              WebGL gets up front.
            */}
            {mapUnavailable ? (
              mapFallback
            ) : (
              <ErrorBoundary fallback={mapFallback} onError={() => setMapFailed(true)}>
                <MapView
                  templeList={siteList}
                  onMarkerClick={handleMarkerClick}
                  onSearchSelect={handleSearchSelect}
                  showMapSearch={!sidebarVisible}
                  onMapClick={setMapCoordinates}
                  isSelectingLocation={isSelectingLocation}
                  selectedPosition={selectedPosition}
                  onSelectionPositionChange={setSelectedPosition}
                  onConfirmLocation={handleConfirmLocation}
                  onCancelLocationSelection={handleCancelLocationSelection}
                  searchTarget={searchTarget}
                  selectedTemple={selectedTemple}
                  onAboutClick={() => {
                    setInfoModalType('about');
                    setInfoModalOpen(true);
                  }}
                  /*
                    The same dialog About uses, reading the same document.

                    This opened `/privacy/` in a new tab, which made the two links
                    in one footer behave differently: About stayed on the page and
                    Privacy left it. Reported as exactly that.

                    The reason it was a link is still honoured. The Privacy Policy
                    used to be a paraphrase in `locales/en/sidebar.json` -- shorter
                    than the real one, differing from it, English only -- while the
                    site-request form asked people to consent to it. That is what
                    `openDocument` fixes rather than reintroduces: it fetches the
                    canonical document through `api.document`, the same template
                    `/privacy/` renders, so there is still one copy on disk. The
                    standalone page stays for deep links and for crawlers.
                  */
                  onPrivacyClick={() => {
                    openDocument('privacy', t('request.privacy'));
                  }}
                  searchQuery={searchQuery}
                  setSearchQuery={setSearchQuery}
                  isSearchFocused={isSearchFocused}
                  setIsSearchFocused={setIsSearchFocused}
                  searchActiveIndex={searchActiveIndex}
                  setSearchActiveIndex={setSearchActiveIndex}
                />
              </ErrorBoundary>
            )}

            {isSitesLoading && (
              <div className="map-status-overlay map-status-overlay--loading" role="status" aria-live="polite">
                {t('map.loadingSites')}
              </div>
            )}

            {!isSitesLoading && sitesFailed && (
              <div className="map-status-overlay map-status-overlay--error" role="alert">
                {t('map.sitesFailed')}
              </div>
            )}

            {/* Requesting a site starts by dropping a pin, which needs the map. */}
            {!mapUnavailable && (
              <button
                className="btn btn-tertiary request-bottom-right"
                onClick={handleStartLocationSelection}
              >
                {t('common:buttons.requestSite')}
              </button>
            )}
          </div>
        </div>
      </div>

      {isContributeMounted && (
        /*
          `<Suspense>` covers the chunk arriving, the boundary covers it failing
          to -- a chunk that 404s is otherwise a Contribute button that does
          nothing at all, with the error only in the console. No fallback while
          it loads: it is one request to the origin that served the page, and a
          spinner that appears and disappears within a frame reads as a glitch.

          No retry button on it. `ErrorBoundary`'s default panel offers "Try
          again", which clears its own state -- and that cannot recover a lazy
          chunk: React caches the rejected import, so once the payload is
          rejected the loader is never called again and the button re-renders
          the same failure forever. A control that provably cannot do what it
          says is worse than no control, so this renders the message alone and
          the visitor reloads, which does refetch.
        */
        <ErrorBoundary
          fallback={
            <div role="alert" className="error-boundary contribute-load-failed">
              <p>{t('error.title')}</p>
            </div>
          }
        >
          <Suspense fallback={null}>
            <ContributeModal
              isOpen={isContributeOpen}
              onClose={() => setIsContributeOpen(false)}
              siteName={contributeTarget.siteName}
              siteSlug={contributeTarget.siteSlug}
            />
          </Suspense>
        </ErrorBoundary>
      )}

      <RequestSiteModal
        isOpen={isRequestSiteOpen}
        onClose={handleCloseRequestSite}
        mapCoordinates={mapCoordinates}
      />

      <InfoModal
        isOpen={infoModalOpen}
        onClose={() => {
          setInfoModalOpen(false);
          setInfoModalType(null);
          setDocumentModal(null);
        }}
        title={documentModal ? documentModal.title : activeModalItem?.title || ''}
        // Emptied for a document, so the prose branch cannot render underneath
        // the markup branch.
        content={documentModal ? '' : activeModalItem?.content || ''}
        html={documentModal?.html}
        isLoading={documentModal?.loading}
        error={documentModal?.error}
      />
    </div>
  );
}

export default Home;
