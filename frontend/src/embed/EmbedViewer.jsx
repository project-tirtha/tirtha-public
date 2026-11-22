import React, { Suspense, lazy, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import ErrorBoundary from '../components/ErrorBoundary';
import { fetchRun, isViewableRun } from '../services/sites.service';
import { hasWebGL } from '../utils/webgl';
import '../styles/Embed.css';

const ModelViewer3D = lazy(() => import('../components/ModelViewer3D'));

/**
 * Everything `/embed/<site>/` shows: the model, and nothing else.
 *
 * The page is someone else's, so there is no router, no map, no panel and no
 * theme toggle -- the template draws the credit strip itself, outside `#root`,
 * so attribution survives even if this never runs. Before this existed the
 * embed booted the whole app, the router matched nothing under `/embed/`, and
 * every third-party iframe showed "Page not found".
 *
 * The run is fetched when the template names one, for the published camera
 * and the viewer-readable file URL. The template's own `data-embed-model` is
 * the fallback: an embed should still draw something if the API is down.
 */
function EmbedViewer({ modelUrl = '', siteId = '', runId = '' }) {
  const { t } = useTranslation();
  const [state, setState] = useState(() => ({
    loading: Boolean(runId),
    run: null,
  }));
  const [webgl] = useState(() => hasWebGL());

  useEffect(() => {
    if (!runId) return undefined;
    const controller = new AbortController();
    fetchRun(runId, controller.signal)
      .then((run) => setState({ loading: false, run }))
      .catch((err) => {
        if (err?.name === 'AbortError') return;
        setState({ loading: false, run: null });
      });
    return () => controller.abort();
  }, [runId]);

  const { loading, run } = state;
  const fromApi = run && isViewableRun(run) ? run.file_url : null;
  const modelPath = fromApi || modelUrl || null;

  let body;
  if (loading) {
    body = <p className="embed-message">{t('site.viewerLoading')}</p>;
  } else if (!modelPath) {
    body = <p className="embed-message" role="alert">{t('viewer.embedUnavailable')}</p>;
  } else if (!webgl) {
    body = <p className="embed-message" role="alert">{t('viewer.noWebGL')}</p>;
  } else {
    body = (
      <ErrorBoundary
        key={`${siteId}:${runId}:${modelPath}`}
        fallback={() => (
          <p className="embed-message" role="alert">
            {t('site.viewerFailed')}
          </p>
        )}
      >
        <Suspense fallback={<p className="embed-message">{t('site.viewerLoading')}</p>}>
          <ModelViewer3D
            modelPath={modelPath}
            modelBytes={typeof run?.model_bytes === 'number' ? run.model_bytes : null}
            camera={run?.camera ?? null}
            siteName={run?.site?.name ?? document.title}
          />
        </Suspense>
      </ErrorBoundary>
    );
  }

  return (
    <main className="embed-root" id="main">
      {body}
    </main>
  );
}

export default EmbedViewer;
