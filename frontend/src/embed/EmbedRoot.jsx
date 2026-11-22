import React, { Suspense, lazy } from 'react';
import { useTranslation } from 'react-i18next';

import ErrorBoundary from '../components/ErrorBoundary';

// Its own chunk: the full app never needs it, and an embed loads it once.
const EmbedViewer = lazy(() => import('./EmbedViewer.jsx'));

/**
 * The embed's mount point, so `main.jsx` can stay free of component code.
 *
 * The boundary covers the chunk failing to arrive, which `<Suspense>` does
 * not; without it a 404'd chunk is a blank iframe on someone else's page.
 */
export default function EmbedRoot(props) {
  const { t } = useTranslation();
  return (
    <ErrorBoundary
      fallback={
        <p className="embed-message" role="alert">
          {t('site.viewerFailed')}
        </p>
      }
    >
      <Suspense fallback={null}>
        <EmbedViewer {...props} />
      </Suspense>
    </ErrorBoundary>
  );
}
