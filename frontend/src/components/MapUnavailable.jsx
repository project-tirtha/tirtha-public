import React, { useId, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { foldForSearch, siteMatches } from '../utils/search';
import '../styles/MapUnavailable.css';

/**
 * The page without a map.
 *
 * Shown when the browser has no WebGL, or when MapLibre fails to start. Before
 * this the map threw during mount, the page-level boundary caught it, and the
 * whole page -- list, search, Contribute -- became one "Something went wrong"
 * sentence. A visitor who cannot draw a map can still find a temple by name,
 * read about it and contribute photographs of it; none of that needs WebGL.
 *
 * Selecting a site goes through the same `onSelect` a marker click does, so the
 * panel shows its details and the URL follows exactly as it would with a map.
 */
function MapUnavailable({ sites = [], onSelect, onContribute }) {
  const { t } = useTranslation(['sidebar', 'common']);
  const [query, setQuery] = useState('');
  const headingId = useId();
  const searchId = useId();

  const folded = foldForSearch(query);
  const shown = useMemo(
    () => (folded ? sites.filter((site) => siteMatches(site, folded)) : sites),
    [sites, folded],
  );

  return (
    <section className="map-unavailable" aria-labelledby={headingId}>
      <div className="map-unavailable-notice" role="note">
        <h2 id={headingId}>{t('map.unavailableTitle')}</h2>
        <p>{t('map.unavailableBody')}</p>
      </div>

      <label htmlFor={searchId} className="map-unavailable-label">
        {t('map.siteListHeading')}
      </label>
      <input
        id={searchId}
        type="search"
        className="map-unavailable-search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={t('a11y.search')}
        autoComplete="off"
      />

      <ul className="map-unavailable-list">
        {shown.map((site) => (
          <li key={site.id} className="map-unavailable-item">
            <button
              type="button"
              className="map-unavailable-site"
              onClick={() => onSelect?.(site)}
            >
              <span className="map-unavailable-name">{site.name}</span>
              {site.location && (
                <span className="map-unavailable-place">{site.location}</span>
              )}
            </button>
            {onContribute && (
              <button
                type="button"
                className="btn btn-tertiary map-unavailable-contribute"
                onClick={() => onContribute(site)}
                aria-label={`${t('site.contribute')}: ${site.name}`}
              >
                {t('site.contribute')}
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

export default MapUnavailable;
