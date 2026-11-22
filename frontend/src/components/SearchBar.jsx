import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import Icon from './Icon';
import { extractCoordsFromGoogleMapsUrl } from '../utils/geocoding';
import { foldForSearch, siteMatches } from '../utils/search';
import { readStoredJSON, removeStored, writeStoredJSON } from '../utils/storage';
import '../styles/SearchBar.css';

const HISTORY_KEY = 'templeSearchHistory';
const MAX_HISTORY = 5;
const MAX_SUGGESTIONS = 8;

/**
 * The site search, in the sidebar and floating over the map.
 *
 * Both instances share one set of state, owned by `Home`, so typing in either
 * finds the same site. That sharing is the *only* mode: this component used to
 * accept each of `searchQuery`, `isFocused` and `activeIndex` as an optional
 * prop with a `useState` fallback beside it -- six pieces of state and six
 * conditional bindings for a component whose two callers both passed all six.
 * The fallbacks were unreachable, and they were what made the setters unstable
 * enough to need three `eslint-disable`-shaped dependency arrays.
 */
function SearchBar({
  templeList = [],
  onSearchSelect,
  variant = 'default',
  searchQuery,
  setSearchQuery,
  isFocused,
  setIsFocused,
  activeIndex,
  setActiveIndex,
}) {
  const { t } = useTranslation('common');
  const isMapSearch = variant === 'map';
  const containerRef = useRef(null);
  const listboxId = useId();

  const [debouncedQuery, setDebouncedQuery] = useState('');
  /*
    The rotating hint used to be five hardcoded English strings -- three
    distinct, two of them repeated -- and one of the three, "Search Ram Mandir",
    names a site Tirtha does not hold. A placeholder that suggests a search
    returning nothing is worse than no placeholder.
  */
  const [placeholderIndex, setPlaceholderIndex] = useState(0);
  /*
    Read during the initialiser, not in an effect: read in an effect, the first
    render always showed an empty history and the saved one appeared a frame
    later -- a visible flicker on the panel under the search box.
  */
  const [recentSearches, setRecentSearches] = useState(() => {
    const saved = readStoredJSON(HISTORY_KEY, []);
    return Array.isArray(saved) ? saved : [];
  });
  const queryRef = useRef('');

  useEffect(() => {
    queryRef.current = searchQuery;
  }, [searchQuery]);

  // Close the suggestions when the click lands outside this component.
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (containerRef.current && !containerRef.current.contains(event.target)) {
        setIsFocused(false);
        setActiveIndex(-1);
      }
    };
    document.addEventListener('mousedown', handleClickOutside, { passive: true });
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [setIsFocused, setActiveIndex]);

  const placeholderNames = useMemo(
    () => templeList.map((site) => site?.name).filter(Boolean).slice(0, 6),
    [templeList],
  );

  const placeholderText = placeholderNames.length
    ? t('placeholders.searchFor', {
        site: placeholderNames[placeholderIndex % placeholderNames.length],
      })
    : t('placeholders.searchModels');

  useEffect(() => {
    if (placeholderNames.length < 2) return undefined;
    const interval = setInterval(() => {
      // Not while someone is typing: replacing the hint mid-search is movement
      // in the corner of the eye for no reason.
      if (queryRef.current) return;
      setPlaceholderIndex((current) => current + 1);
    }, 2600);
    return () => clearInterval(interval);
  }, [placeholderNames.length]);

  useEffect(() => {
    const timerId = window.setTimeout(() => {
      // Folded the same way as the names: case, accents and Unicode
      // normalisation form all stop mattering (`utils/search`).
      setDebouncedQuery(foldForSearch(searchQuery));
    }, 200);
    return () => window.clearTimeout(timerId);
  }, [searchQuery]);

  /*
    A pasted Google Maps link or a bare "lat, lng" is offered as its own
    suggestion, so the contribute flow can start from a place that is not in the
    archive yet.
  */
  const customLocationSuggestion = useMemo(() => {
    if (!searchQuery.trim()) return null;
    const coords = extractCoordsFromGoogleMapsUrl(searchQuery);
    if (!coords) return null;
    return {
      id: `coords:${coords.lat},${coords.lng}`,
      name: searchQuery,
      location: t('map.coordinateSuggestion'),
      lat: coords.lat,
      lng: coords.lng,
      isCustomLocation: true,
    };
  }, [searchQuery, t]);

  const filteredSuggestions = useMemo(() => {
    if (!debouncedQuery) return [];
    return templeList
      .filter((temple) => siteMatches(temple, debouncedQuery))
      .slice(0, MAX_SUGGESTIONS);
  }, [debouncedQuery, templeList]);

  const allSuggestions = useMemo(() => {
    const list = [...filteredSuggestions];
    if (customLocationSuggestion) list.unshift(customLocationSuggestion);
    return list;
  }, [filteredSuggestions, customLocationSuggestion]);

  /*
    Keep the highlight inside the list as it shrinks. This was
    `setActiveIndex(list.length > 0 ? 0 : -1)` in an effect keyed on the length,
    which reset the highlight to the first row on *every* keystroke -- so
    arrowing down to the third result and typing one more character silently
    moved the selection back to the top, and Enter opened the wrong site.

    **Nothing is highlighted until the visitor arrows to it.** `current < 0`
    used to become `0`, so the first result was activated the instant results
    appeared -- and the highlight wears a 3px ring, because for
    `aria-activedescendant` navigation that ring *is* the focus indicator
    (`SearchBar.css`). The result was a ring appearing on a row nobody had
    navigated to, from typing alone, which is what it looked like: noise.

    Enter is unaffected. `handleSearch` already falls back to
    `allSuggestions[0]` when nothing is active, so typing and pressing Enter
    still opens the top result; the keydown handler only intercepts Enter when
    something *is* active. And `aria-activedescendant` is now unset until the
    visitor actually navigates, which is what it is supposed to mean -- a
    screen reader was previously announcing the first option on every keystroke.
  */
  useEffect(() => {
    setActiveIndex((current) => {
      if (allSuggestions.length === 0) return -1;
      if (current < 0) return -1;
      return Math.min(current, allSuggestions.length - 1);
    });
  }, [allSuggestions.length, setActiveIndex]);

  const handleSuggestionClick = useCallback(
    (temple) => {
      setRecentSearches((previous) => {
        const next = [
          /*
            `id` and `isCustomLocation` are stored alongside the two fields the
            row draws, because clicking the row replays it through this same
            handler and `Home` reads both. A pasted coordinate is stored under
            the pasted text as its name, so it never matches a real site and
            `handleRecentClick` falls back to the stored entry; without the
            flag `Home` takes that for a site and opens the 3D viewer on one
            that does not exist -- an empty canvas, with neither a model nor
            the "nothing published yet" message, which only renders once a
            site's runs have been fetched by `id`.
          */
          {
            id: temple.id,
            name: temple.name,
            location: temple.location,
            lat: temple.lat,
            lng: temple.lng,
            isCustomLocation: temple.isCustomLocation,
          },
          ...previous.filter((entry) => entry.name !== temple.name),
        ].slice(0, MAX_HISTORY);
        // Wrapped: an unwrapped `setItem` here threw in a private window and
        // took the selection down with it, so the click did nothing at all.
        writeStoredJSON(HISTORY_KEY, next);
        return next;
      });
      setSearchQuery('');
      setIsFocused(false);
      onSearchSelect?.(temple);
    },
    [onSearchSelect, setSearchQuery, setIsFocused],
  );

  const handleSearch = (event) => {
    event.preventDefault();
    if (activeIndex >= 0 && activeIndex < allSuggestions.length) {
      handleSuggestionClick(allSuggestions[activeIndex]);
    } else if (allSuggestions.length > 0) {
      handleSuggestionClick(allSuggestions[0]);
    }
  };

  const handleRecentClick = (recentTemple) => {
    /*
      Fall back to the stored entry rather than doing nothing. The history keeps
      a name, and matching it against the current list fails whenever a site has
      since been renamed or hidden -- in which case this used to return silently
      and the row looked broken. The stored coordinate is still good.
    */
    const temple = templeList.find((site) => site.name === recentTemple.name);
    handleSuggestionClick(temple ?? recentTemple);
  };

  const handleKeyDown = (event) => {
    if (event.key === 'Escape') {
      setIsFocused(false);
      setActiveIndex(-1);
      return;
    }
    if (!allSuggestions.length) return;

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveIndex((prev) => Math.min(prev + 1, allSuggestions.length - 1));
      setIsFocused(true);
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex((prev) => Math.max(prev - 1, 0));
      setIsFocused(true);
    }
    if (event.key === 'Enter' && activeIndex >= 0 && activeIndex < allSuggestions.length) {
      event.preventDefault();
      handleSuggestionClick(allSuggestions[activeIndex]);
    }
  };

  const handleClearHistory = () => {
    setRecentSearches([]);
    removeStored(HISTORY_KEY);
  };

  const suggestionsOpen = isFocused && Boolean(searchQuery.trim()) && allSuggestions.length > 0;
  const historyOpen = isFocused && !searchQuery.trim() && recentSearches.length > 0;
  const activeId =
    suggestionsOpen && activeIndex >= 0 ? `${listboxId}-option-${activeIndex}` : undefined;

  return (
    <div
      className={`search-bar-container ${isMapSearch ? 'search-bar-container--map' : ''}`}
      ref={containerRef}
    >
      <form
        onSubmit={handleSearch}
        className={`search-bar ${isMapSearch ? 'search-bar--map' : ''}`}
        autoComplete="off"
        role="search"
      >
        <div className={`search-input-wrapper ${isMapSearch ? 'search-input-wrapper--map' : ''}`}>
          {/*
            A combobox, declared as one. The list was a plain `<div>` of buttons
            with a CSS `.active` class, so a screen reader was told nothing about
            how many results there were, which one was highlighted, or that
            arrowing changed anything (WCAG 4.1.2).
          */}
          <input
            type="text"
            role="combobox"
            aria-expanded={suggestionsOpen}
            aria-controls={suggestionsOpen ? listboxId : undefined}
            aria-activedescendant={activeId}
            aria-autocomplete="list"
            aria-label={t('a11y.search')}
            placeholder={isMapSearch ? '' : placeholderText}
            value={searchQuery}
            onChange={(event) => {
              setSearchQuery(event.target.value);
              setIsFocused(true);
            }}
            onFocus={() => setIsFocused(true)}
            onKeyDown={handleKeyDown}
            className={`search-input ${isMapSearch ? 'search-input--map' : ''}`}
          />
          {isMapSearch && !searchQuery && (
            <div className="search-placeholder" aria-hidden="true">
              {placeholderText}
            </div>
          )}
          <button
            type="submit"
            className="search-input-action"
            title={t('a11y.search')}
            aria-label={t('a11y.search')}
          >
            <Icon name="search" />
          </button>
        </div>
      </form>

      {(suggestionsOpen || historyOpen) && (
        <div className="search-dropdown">
          {suggestionsOpen ? (
            <ul className="search-suggestions" role="listbox" id={listboxId}>
              {allSuggestions.map((temple, index) => (
                <li
                  key={temple.id ?? `${temple.name}:${temple.lat},${temple.lng}`}
                  id={`${listboxId}-option-${index}`}
                  role="option"
                  aria-selected={index === activeIndex}
                  className={`suggestion-item ${index === activeIndex ? 'active' : ''}`}
                  /*
                    `mousedown`, not `click`: the input's blur fires first on a
                    click and closes the dropdown out from under the pointer.
                  */
                  onMouseDown={(event) => {
                    event.preventDefault();
                    handleSuggestionClick(temple);
                  }}
                >
                  <span className="suggestion-icon" aria-hidden="true">
                    {temple.isCustomLocation ? '🧭' : '📍'}
                  </span>
                  <span className="suggestion-text">
                    <span className="suggestion-name">{temple.name}</span>
                    <span className="suggestion-location">{temple.location}</span>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="recent-searches">
              <div className="recent-header">
                <span className="recent-title">{t('map.recentSearches')}</span>
                <button
                  type="button"
                  className="clear-history-btn"
                  onClick={handleClearHistory}
                  title={t('map.clearHistory')}
                  aria-label={t('map.clearHistory')}
                >
                  <Icon name="close" />
                </button>
              </div>
              {recentSearches.map((recentTemple) => (
                <button
                  key={`${recentTemple.name}:${recentTemple.lat},${recentTemple.lng}`}
                  type="button"
                  className="recent-item"
                  onClick={() => handleRecentClick(recentTemple)}
                >
                  <span className="recent-icon" aria-hidden="true">
                    🕐
                  </span>
                  <span className="recent-text">
                    <span className="recent-name">{recentTemple.name}</span>
                    <span className="recent-location">{recentTemple.location}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default React.memo(SearchBar);
