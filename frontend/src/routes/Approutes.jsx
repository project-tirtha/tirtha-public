import React, { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { BrowserRouter, Link, Route, Routes, useParams } from 'react-router-dom';

import ErrorBoundary from '../components/ErrorBoundary';
import Home from '../pages/Home';
import { languageFromPath, routerBasename } from '../utils/deployment';

/**
 * The one screen, whether or not the URL names a site.
 *
 * **Every route renders this same component**, and that is the point. React
 * remounts when the element *type* at a position changes, so having `/` render
 * `<Home />` and `site/:id` render a different wrapper meant the first marker
 * click tore the whole app down and rebuilt it: MapLibre re-initialised, the
 * sites list refetched, and the site detail and the reverse geocode were each
 * requested twice -- once by the dying instance, once by the new one. The map
 * camera, the search box and the viewer width all reset, visibly, on the click
 * that was supposed to open a temple.
 *
 * Site pages are routes rather than a query parameter because the server renders
 * per-site Open Graph tags for exactly these paths (internal design notes): a shared
 * link has to be a real URL for an unfurl crawler, which does not run this code.
 */
function TirthaApp() {
  const { siteId, runId } = useParams();
  return <Home initialSiteId={siteId ?? null} initialRunId={runId ?? null} />;
}

function NotFound() {
  const { t } = useTranslation();
  return (
    <main className="not-found" id="main">
      <h1>{t('error.notFoundTitle')}</h1>
      <p>{t('error.notFoundBody')}</p>
      <Link to="/">{t('site.back')}</Link>
    </main>
  );
}

function AppRoutes() {
  const { i18n } = useTranslation();

  /*
    The URL is the source of truth for language, not the browser's preference
    and not localStorage: `/or/app/` is a link somebody shared *because* it is
    Odia, and the server has already rendered its meta tags in Odia. The client
    disagreeing with that would show an English page under an Odia URL.
  */
  useEffect(() => {
    const language = languageFromPath();
    if (i18n.language !== language) i18n.changeLanguage(language);
  }, [i18n]);

  return (
    <BrowserRouter basename={routerBasename()}>
      {/*
        Outside `<Routes>`, deliberately. A boundary *inside* the route tree
        cannot catch a route element that fails to load -- and this app lazily
        imports the 3D viewer, so a chunk that 404s is a real path to a blank
        page rather than a hypothetical one.
      */}
      <ErrorBoundary>
        <Routes>
          <Route path="/" element={<TirthaApp />} />
          {/*
            Three shapes, matching `urls.py`: the bare id, the id with its slug,
            and a specific run beneath that. The server routes all three; the
            client routed none of them, so every shared site link opened the
            bare map.
          */}
          <Route path="site/:siteId" element={<TirthaApp />} />
          <Route path="site/:siteId/:slug" element={<TirthaApp />} />
          <Route path="site/:siteId/:slug/:runId" element={<TirthaApp />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </ErrorBoundary>
    </BrowserRouter>
  );
}

export default AppRoutes;
