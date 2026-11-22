import React, { useCallback, useRef, useState } from 'react';
import Icon from './Icon';
import SearchBar from './SearchBar';
import { useTranslation } from 'react-i18next';
import { useTheme } from '../hooks/useTheme';
import { fetchSiteStats } from '../services/sites.service';
import '../styles/Sidebar.css';
import { deploymentPrefix, isPreRelease } from '../utils/deployment';

import tirthaLogoDark from '../assets/icons/ui/tirtha-logo-dark.webp';
import tirthaLogoLight from '../assets/icons/ui/tirtha-logo-light.webp';
import { markerFor } from '../utils/siteStatus';

/** One object, so a reset is a reference swap rather than a fresh literal. */
const EMPTY_STATS = {
  total_images: 0,
  total_contributors: 0,
  top_contributors: [],
  latest_run: null,
};

/*
  Ranks are numbered, not medalled.

  Three emoji medals against sixteen drawn line icons read as a sticker on an
  archive, and they stopped at three -- the fourth contributor got "#4" beside
  three cartoons. A numeral is the same information in the same voice all the
  way down.
*/

function Sidebar({
  isVisible,
  onMobileClose,
  templeList = [],
  onSearchSelect,
  selectedTemple,
  onCloseTemple,
  onOpenViewer,
  isViewerOpen = false,
  onContributeClick,
  onToggle,
  searchQuery,
  setSearchQuery,
  isSearchFocused,
  setIsSearchFocused,
  searchActiveIndex,
  setSearchActiveIndex,
  // Optional: without it the document links stay plain links to their pages,
  // which is a working state and the one every other consumer of this component
  // gets.
  onDocumentClick,
}) {
  const { t } = useTranslation(['sidebar', 'common']);
  const { isDark } = useTheme();
  const sidebarRef = useRef(null);
  const [sidebarWidth, setSidebarWidth] = useState(380);
  const [isResizing, setIsResizing] = useState(false);
  const draggedWidthRef = useRef(380);

  const [siteStats, setSiteStats] = useState(EMPTY_STATS);
  /*
    `Boolean(selectedSiteId)`, not `false`. The render-phase reset below only
    fires when the selection *changes*, so on a first mount that already has a
    site -- which is every deep link to /site/<id> -- this stayed false while
    the first request was in flight, and the panel asserted "0 photographs,
    0 contributors, no contributions yet" under the name of a site with
    thousands, until the response landed.
  */
  const [isStatsLoading, setIsStatsLoading] = useState(Boolean(selectedTemple?.id));
  const [statsError, setStatsError] = useState('');

  const tirthaLogo = isDark ? tirthaLogoDark : tirthaLogoLight;

  // Drawn on the map, and not. The second group is not an error state -- see
  // the note beside where it is rendered.
  const placedSites = templeList.filter((site) => site.placed !== false);
  const unplacedSites = templeList.filter((site) => site.placed === false);


  /*
    `--sidebar-occupied` is gone, and with it a write to the document root on
    every frame of a resize drag.

    It existed so the map's bottom-left chrome could dodge this panel: the key
    to the pins, and the attribution bar. The key is a square in the map's
    control stack now, and the bar holds still at the full width while this
    panel is shortened by `--map-footer-height` instead. Neither has anything
    left to dodge, so nothing needs to be told how wide this is.
  */

  const handleMouseDown = () => {
    draggedWidthRef.current = sidebarWidth;
    setIsResizing(true);
  };

  const handleMouseUp = useCallback(() => {
    setIsResizing(false);
    setSidebarWidth(draggedWidthRef.current);
  }, []);

  /*
    The width is written onto the element during the drag and into state only
    on release, the way the viewer's handle does it (Home.jsx). A `setState`
    per `mousemove` re-rendered the whole panel, and with no site selected the
    panel is the archive: a row, a pin image and three spans per site, plus a
    freshly encoded marker data URL for each. At a pointer's ~120 events a
    second that is more reconciliation than a frame has room for, and it gets
    worse with every site added.
  */
  const handleMouseMove = useCallback((e) => {
    const newWidth = e.clientX;
    if (newWidth >= 250 && newWidth <= 500) {
      draggedWidthRef.current = newWidth;
      if (sidebarRef.current) sidebarRef.current.style.width = `${newWidth}px`;
    }
  }, []);

  React.useEffect(() => {
    if (isResizing) {
      window.addEventListener('mousemove', handleMouseMove, { passive: true });
      window.addEventListener('mouseup', handleMouseUp);
      return () => {
        window.removeEventListener('mousemove', handleMouseMove);
        window.removeEventListener('mouseup', handleMouseUp);
      };
    }
  }, [handleMouseMove, handleMouseUp, isResizing]);

  /*
    Clear the previous site's figures the moment the selection changes, during
    render. Doing it in the effect left the last site's image count under the
    new site's name for a frame -- and for the whole of the fetch, if it was
    slow, which is worse: numbers that are wrong but plausible.
  */
  const selectedSiteId = selectedTemple?.id ?? null;
  const [statsFor, setStatsFor] = useState(selectedSiteId);
  if (statsFor !== selectedSiteId) {
    setStatsFor(selectedSiteId);
    setSiteStats(EMPTY_STATS);
    setStatsError('');
    setIsStatsLoading(Boolean(selectedSiteId));
  }

  React.useEffect(() => {
    const siteId = selectedTemple?.id;
    if (!siteId) return undefined;

    const controller = new AbortController();

    fetchSiteStats(siteId, controller.signal)
      .then((data) => {
        setSiteStats({
          total_images: Number(data.total_images) || 0,
          total_contributors: Number(data.total_contributors) || 0,
          top_contributors: Array.isArray(data.top_contributors) ? data.top_contributors : [],
          // Carried through the normaliser like the rest: a field the
          // API sends and this drops is a field the panel can never show.
          latest_run: data.latest_run || null,
        });
      })
      .catch((error) => {
        if (error?.name === 'AbortError') return;
        setSiteStats(EMPTY_STATS);
        setStatsError(t('stats.failed'));
        if (import.meta.env.DEV) console.error('Failed to load site stats:', error);
      })
      /*
        Guarded on the signal rather than left bare: `.finally` runs on an
        abort too, and the abort is the *previous* site's. Clearing the flag
        from it drops the panel out of its loading branch while the new site's
        request is still in flight, so the figures the render-phase reset just
        emptied render as a confident "0 photographs, 0 contributors" under the
        new name -- the wrong-but-plausible numbers that reset exists to
        prevent.
      */
      .finally(() => {
        if (!controller.signal.aborted) setIsStatsLoading(false);
      });

    return () => controller.abort();
    // `selectedTemple.siteId` was in this list and does not exist -- the field
    // is `id`. A dependency that is always `undefined` never changes, so it
    // contributed nothing; the effect happened to be correct by way of `id`.
  }, [selectedTemple?.id, t]);

  /*
    A collapsed panel is hidden by a zero width and `opacity: 0`, and neither
    takes anything out of the tab order. Closed, the sidebar still held the
    dismiss button, the language links, one button per site and the four
    document links: some fifty stops where Tab appears to do nothing, each
    focus ring drawn inside a 0px-wide box, all of it read out by a screen
    reader from a panel that is not on screen. `inert` removes them; the
    stylesheet's `visibility: hidden` says the same to a browser without it,
    and is transitioned so the panel still animates out before it goes.
  */
  return (
    <aside
      ref={sidebarRef}
      className={`sidebar ${isVisible ? 'visible' : 'hidden'}${isResizing ? ' is-resizing' : ''}`}
      inert={!isVisible}
      style={isVisible ? { width: `${sidebarWidth}px` } : undefined}
    >
      <>
        <div className="sidebar-header">
          {/* Decorative: the name is right beside it, so announcing the logo
              too would just say it twice. */}
          <img src={tirthaLogo} alt="" className="sidebar-logo" />
          <div className="sidebar-title-container">
            {/*
              Branding, not the document heading. The page's `h1` lives in
              `Home` and is always present; this panel can be collapsed, and a
              heading that disappears with it would leave the page without one.
            */}
            <p className="sidebar-title">{t('common:appName', 'Project Tirtha')}</p>
            {/*
              Only where it is true. On production this is the archive, not a
              preview of one, and a permanent "Beta" reads as a disclaimer
              nobody meant to make.
            */}
            {isPreRelease() && (
              <span className="beta-badge">{t('common:beta', 'Beta')}</span>
            )}
          </div>
          {/*
            The only way to dismiss this panel used to be a button *outside* it,
            which the panel itself covers on a narrow screen -- so on a phone it
            could not be closed at all. `onToggle` and `onMobileClose` were both
            passed in and neither was ever called.
          */}
          <button
            type="button"
            className="sidebar-close-btn"
            onClick={() => (onMobileClose ? onMobileClose() : onToggle?.())}
            aria-label={t('a11y.closeSidebar')}
            title={t('a11y.closeSidebar')}
          >
            <Icon name="close" />
          </button>
        </div>

        {/* Beneath the title rather than buried in the menu: a visitor who
            cannot read the interface has to be able to find this without
            reading the interface. */}
        {/*
          What this is, on the screen a visitor lands on.

          The project had one good sentence describing itself and it was shown
          to search engines and to anyone who found the About panel -- a link in
          the corner of the map. Somebody arriving at a map of pins had no way
          to learn what they were looking at without going hunting.
        */}
        <p className="sidebar-tagline">{t('app.tagline')}</p>

        {/*
          The language control moved to the map's control stack.

          It lived here, below the tagline, which meant choosing a language
          required opening the sidebar -- and a visitor who cannot read the
          interface is exactly the one who cannot find a control buried inside
          it. It is a square in the right-edge cluster now, beside the basemap
          picker and the key, where it is visible without reading anything.
        */}

        <div className="sidebar-content">
          {isVisible && (
            <SearchBar
              templeList={templeList}
              onSearchSelect={onSearchSelect}
              variant="default"
              searchQuery={searchQuery}
              setSearchQuery={setSearchQuery}
              isFocused={isSearchFocused}
              setIsFocused={setIsSearchFocused}
              activeIndex={searchActiveIndex}
              setActiveIndex={setSearchActiveIndex}
            />
          )}

          {selectedTemple ? (
            <div className="sidebar-temple-detail">
              <div className="sidebar-temple-header">
                {/*
                  `onCloseTemple` was passed in by `Home` and aliased to a
                  `handleBack` that nothing rendered, so once a site was open
                  there was no way to leave it from the sidebar -- the only exit
                  was the browser's back button.
                */}
                <button
                  type="button"
                  className="sidebar-temple-back"
                  onClick={() => onCloseTemple?.()}
                  aria-label={t('site.back')}
                  title={t('site.back')}
                >
                  <Icon name="arrow_back" />
                </button>
                {/*
                  The name and the place stack; only the back button sits
                  beside them.

                  All three were one flex row, so the name and the location
                  competed for the same 309px and the name got 152 of it --
                  "Lakhmaneswara Bharateswara Shatrughneswara Temple" clamped to
                  "Lakhma… ra…", which is not a name anybody can read. Stacking
                  gives the name the full width and two lines, which fits every
                  site in the archive.
                */}
                <div className="sidebar-temple-heading">
                  <h2 className="sidebar-temple-name">{selectedTemple.name}</h2>
                  {/* The drawn pin, not the emoji. An emoji renders in the
                      system's own colour and style -- a different weight, a
                      different palette, and on some platforms a cartoon --
                      beside sixteen line icons drawn to match each other. */}
                  <p className="sidebar-temple-location">
                    <Icon name="location_on" aria-hidden="true" />
                    <span>{selectedTemple.location}</span>
                  </p>
                </div>
              </div>

              {/*
                Reopen the model. Closing it no longer leaves the site -- the
                panel keeps its description, coordinates and runs -- so there has
                to be a way back to the thing the visitor came for.
              */}
              {!isViewerOpen && (
                <button
                  type="button"
                  className="btn btn-tertiary sidebar-view-model-btn"
                  onClick={() => onOpenViewer?.()}
                >
                  <Icon name="view_in_ar" />
                  {t('site.viewModel')}
                </button>
              )}

              <button
                type="button"
                className="btn btn-primary sidebar-contribute-btn"
                onClick={() => onContributeClick?.(selectedTemple)}
              >
                {t('contribute.action')}
              </button>

              {(selectedTemple.descriptionHtml || selectedTemple.description) && (
                <div className="sidebar-temple-description">
                  <h4>{t('panel.description')}</h4>
                  {selectedTemple.descriptionHtml ? (
                    /*
                      The descriptions are HTML -- paragraphs, ordered lists,
                      links to Wikipedia and Google Maps. React escapes by
                      default, so this rendered as `</p> <ol> <li><a href=...>`
                      on screen for 27 of the 59 sites.

                      `dangerouslySetInnerHTML` is safe *here specifically*
                      because this is `description_html`, which the API produces
                      by running the stored text through a strict bleach
                      allowlist (`api._description_html`) -- tags, attributes and
                      URL protocols all restricted, links forced to
                      `rel="noopener noreferrer"`. The plain `description` field
                      is still there for anyone who wants the text.
                    */
                    <div
                      className="sidebar-description-body"
                      dangerouslySetInnerHTML={{ __html: selectedTemple.descriptionHtml }}
                    />
                  ) : (
                    <p>{selectedTemple.description}</p>
                  )}
                </div>
              )}

              {selectedTemple.position && (
                <div className="sidebar-coordinates">
                  <h4>{t('panel.coordinates')}</h4>
                  <p>
                    {t('panel.latitude', {
                      value: selectedTemple.position[0]?.toFixed(6),
                    })}
                    <br />
                    {t('panel.longitude', {
                      value: selectedTemple.position[1]?.toFixed(6),
                    })}
                  </p>
                  <a
                    className="sidebar-maps-link"
                    href={`https://www.google.com/maps/search/?api=1&query=${selectedTemple.position[0]},${selectedTemple.position[1]}`}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <Icon name="open_in_new" />{' '}
                    {t('panel.openInMaps')}
                  </a>
                </div>
              )}

              <div className="sidebar-stats-section">
                <h4>{t('stats.contributionStats')}</h4>
                <div className="stats-grid">
                  <div className="stats-card">
                    {/* The emoji is decoration -- a screen reader announcing
                        "camera with flash" before "Photographs" is noise. */}
                    <span className="stats-label">
                      <Icon name="image" aria-hidden="true" />
                      {t('stats.images')}
                    </span>
                    <strong className="stats-value">{siteStats.total_images}</strong>
                  </div>
                  <div className="stats-card">
                    <span className="stats-label">
                      <Icon name="person" aria-hidden="true" />
                      {t('stats.contributors')}
                    </span>
                    <strong className="stats-value">{siteStats.total_contributors}</strong>
                  </div>

                {/*
                  How long the model took to build.

                  Both timestamps have been on every run since 10.4 and in the
                  API payload from the first version, and nothing ever
                  subtracted them -- so the answer to "how long does this take"
                  was already reaching the browser and being shown to nobody.
                  Across 82 runs the median is 22 minutes and the longest is
                  30.4 hours, which is worth telling somebody who has just
                  waited for one.

                  Only when there is a figure: a run still going, or one from
                  before the timestamps were kept, has none, and an empty card
                  is worse than no card.
                */}
                {siteStats.latest_run?.duration_seconds > 0 && (
                  <div className="stats-card">
                    <span className="stats-label">
                      <Icon name="schedule" aria-hidden="true" />
                      {t('stats.buildTime')}
                    </span>
                    <strong className="stats-value">
                      {(() => {
                        const total = Math.round(
                          siteStats.latest_run.duration_seconds / 60,
                        );
                        const hours = Math.floor(total / 60);
                        const minutes = total % 60;
                        // No `Math.max(total, 1)`. Flooring a sub-minute
                        // figure to "1 minute" is what made 11 of the 31 sites
                        // showing this card claim their model built in a
                        // minute; the API withholds those now, so anything
                        // that arrives here is a real duration.
                        return hours > 0
                          ? t('stats.hoursMinutes', { hours, minutes })
                          : t('stats.minutes', { minutes: total });
                      })()}
                    </strong>
                  </div>
                )}
                </div>

                {isStatsLoading ? (
                  <p className="stats-loading">{t('stats.loading')}</p>
                ) : statsError ? (
                  <p className="stats-error" role="alert">{statsError}</p>
                ) : siteStats.top_contributors.length > 0 ? (
                  <ul className="top-contributors-list">
                    {siteStats.top_contributors.map((contributor, index) => (
                      /*
                        Keyed and labelled from what the endpoint returns:
                        `handle` and `images`. It read `contributor.id` and
                        `contributor.uploads`, neither of which is a field on
                        this payload -- so every key was `undefined` and every
                        row read "undefined uploads".
                      */
                      <li key={contributor.handle ?? contributor.name}>
                        <span className="rank-icon" aria-hidden="true">
                          {`#${index + 1}`}
                        </span>
                        <span className="contributor-name">{contributor.name}</span>
                        <span className="contributor-meta">
                          {t('stats.imageCount', { count: contributor.images })}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="no-contributions">{t('stats.noContributions')}</p>
                )}
              </div>
            </div>
          ) : (
            /*
              The archive, listed.

              This branch was `null`. With no site selected -- which is how every
              visit starts -- the panel rendered a search box, a footer, and
              several hundred pixels of nothing between them. Forty sites were in
              memory, drawn as pins on the map, and named nowhere a visitor could
              read them: to find anything you had to already know where it was,
              or guess a name into the search box.

              The pin beside each row is the same `buildMarkerDataUrl` the map
              draws, so the list cannot drift from the map the way the old legend
              had.
            */
            <div className="sidebar-site-list">
              <h3 className="sidebar-site-list-heading">
                {t('panel.allSites', { count: placedSites.length })}
              </h3>
              <ul>
                {placedSites.map((site) => {
                  const { status, src } = markerFor(site);
                  return (
                    <li key={site.id}>
                      <button
                        type="button"
                        className={`sidebar-site-row ${
                          selectedSiteId === site.id ? 'is-current' : ''
                        }`}
                        onClick={() => onSearchSelect?.(site)}
                      >
                        {/*
                          The site, as a picture. A row of names with a 15px pin
                          told you nothing you could recognise -- and the 360x270
                          derivative these use was already being generated and
                          consumed by nothing. The pin stays as the fallback,
                          because it still carries the status.
                        */}
                        {site.thumbnailCard ? (
                          <img
                            className="sidebar-site-thumb"
                            src={site.thumbnailCard}
                            alt=""
                            loading="lazy"
                          />
                        ) : (
                          <img src={src} alt="" width="15" height="25" aria-hidden="true" />
                        )}
                        <span className="sidebar-site-text">
                          <span className="sidebar-site-name">{site.name}</span>
                          <span className="sidebar-site-place">{site.location}</span>
                        </span>
                        {/* The status in words, for a reader who cannot see the
                            pin's colour or shape (WCAG 1.4.1). */}
                        <span className="sr-only">{t(`map.status.${status}`)}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>

              {/*
                The sites nobody has located yet -- 12 of 34 here, 29 of 59 in
                production. They used to be dropped by the service, so they
                appeared in no surface at all and their ARKs resolved to an
                empty map. They are part of the archive; they simply have
                nowhere to be drawn, and the row says so rather than pretending
                a pin exists.
              */}
              {unplacedSites.length > 0 && (
                <>
                  <h3 className="sidebar-site-list-heading">
                    {t('panel.notLocated', { count: unplacedSites.length })}
                  </h3>
                  <ul>
                    {unplacedSites.map((site) => (
                      <li key={site.id}>
                        <button
                          type="button"
                          className="sidebar-site-row sidebar-site-row--unplaced"
                          onClick={() => onSearchSelect?.(site)}
                        >
                          <Icon name="location_off" className="sidebar-site-unplaced-icon" />
                          <span className="sidebar-site-text">
                            <span className="sidebar-site-name">{site.name}</span>
                            <span className="sidebar-site-place">{site.location}</span>
                          </span>
                          <span className="sr-only">{t('panel.notLocatedRow')}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
        </div>

        {/*
          The canonical documents, linked from the interface.

          They were written into `sidebar.json` and rendered by nothing -- the
          Acknowledgment page in particular was reachable from nowhere in the
          app at all, which for a page that credits contributors by name is the
          wrong place to leave it.
        */}
        <footer className="sidebar-footer">
          <nav aria-label={t('sidebar:documentsHeading')}>
            <ul>
              {(t('sidebar:documents', { returnObjects: true }) || []).map((doc) => (
                <li key={doc.id}>
                  {/*
                    Still an `<a>` with a real `href`, and still the canonical
                    URL. Reading it in place is the default, not the only way:
                    the standalone page is what an unfurl describes, what a
                    person cites, and what a middle-click or ctrl-click opens --
                    all of which a dialog cannot be. Turning these into buttons
                    would have taken that away to save one `preventDefault`.

                    Which is why the modifiers are checked before intercepting:
                    a plain left-click opens the dialog, and every other way of
                    activating the link behaves like the link it is.
                  */}
                  <a
                    href={`${deploymentPrefix()}${doc.path}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(event) => {
                      if (
                        !onDocumentClick ||
                        event.defaultPrevented ||
                        event.button !== 0 ||
                        event.metaKey ||
                        event.ctrlKey ||
                        event.shiftKey ||
                        event.altKey
                      ) {
                        return;
                      }
                      event.preventDefault();
                      onDocumentClick(doc.id, doc.title);
                    }}
                  >
                    {doc.title}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
          {/*
            The project's own links, back where they were.

            The Django site's footer carried the paper, the source, the Matrix
            room and a contact address; the React footer carried none of them,
            so the citation for a published, DOI'd piece of work was reachable
            from nowhere in the interface. Icon links with real labels rather
            than an emoji row -- `title` for a pointer, `aria-label` for
            everything else.
          */}
          <nav className="sidebar-links" aria-label={t('sidebar:links.heading')}>
            <ul>
              {[
                { id: 'paper', icon: 'article', href: 'https://arxiv.org/abs/2308.01246' },
                { id: 'code', icon: 'code', href: 'https://github.com/project-tirtha/tirtha-public' },
                { id: 'chat', icon: 'forum', href: 'https://matrix.to/#/#tirtha:matrix.org' },
                { id: 'contact', icon: 'mail', href: 'mailto:project.tirtha@niser.ac.in' },
              ].map((link) => {
                const external = !link.href.startsWith('mailto:');
                return (
                  <li key={link.id}>
                    <a
                      href={link.href}
                      target={external ? '_blank' : undefined}
                      rel={external ? 'noopener noreferrer' : undefined}
                      title={t(`sidebar:links.${link.id}`)}
                      aria-label={t(`sidebar:links.${link.id}`)}
                    >
                      <Icon name={link.icon} aria-hidden="true" />
                    </a>
                  </li>
                );
              })}
            </ul>
          </nav>
          <p className="sidebar-copyright">{t('sidebar:footer.copyright')}</p>
          <p className="sidebar-licence">{t('sidebar:footer.licence')}</p>
        </footer>
      </>
      {/*
        `aria-hidden`: dragging is a pointer-only affordance, and the panel is
        already resizable in the only way that matters here -- it collapses.
        Announcing an unlabelled div that a keyboard cannot operate is noise.
      */}
      <div
        className="sidebar-resize-handle"
        aria-hidden="true"
        onMouseDown={handleMouseDown}
        title={t('a11y.resizeSidebar')}
      />
    </aside>
  );
}

export default React.memo(Sidebar);
