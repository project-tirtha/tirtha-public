import React from 'react';
import { useTranslation } from 'react-i18next';

import Icon from './Icon';

import { languageFromPath, offeredLanguages, urlForLanguage } from '../utils/deployment';
import '../styles/LanguageSwitcher.css';

/**
 * Switch between English, Odia and Hindi (internal design notes).
 *
 * The translations, the `hreflang` alternates and the language-prefixed routes
 * were all in place; nothing in the UI ever offered the choice, so Odia and
 * Hindi were reachable only by typing the URL. That is most of the reason the
 * two languages looked untranslated -- nobody could get to them.
 *
 * These are links, not buttons calling `i18n.changeLanguage`. The server
 * renders the title, description and canonical URL per language for unfurl
 * crawlers that do not run JavaScript (internal design notes), so a switch has to be
 * a navigation: a client-side toggle would leave the URL, the canonical link
 * and every shared preview still claiming the old language.
 *
 * `hrefLang` on each link says what the destination is in, and `aria-current`
 * says which one you are reading -- otherwise the only difference between the
 * three is a colour, which is not a difference for everyone (WCAG 1.4.1).
 */
function LanguageSwitcher({ className = '', compact = false }) {
  const { t } = useTranslation();
  const current = languageFromPath();
  const offered = offeredLanguages();

  /*
    In the map's control stack, the same shape as every other control there.

    This has now been three things. A segmented pill naming both languages was
    ~137px among 44px squares and made the row look ragged. Replacing it with a
    single square showing only the *other* language was smaller but strange: a
    control whose label is the thing you are not looking at, and which silently
    becomes a different word once clicked.

    The pattern the stack already uses twice -- an icon square that opens a
    small panel, `<details>` for the basemap picker and for the key -- is also
    the ordinary one for language on the web: a globe, and a list you choose
    from. So this is that, sharing their markup and their styling rather than
    inventing a third disclosure. Both languages are named, in their own script,
    and the current one is marked.
  */
  if (compact) {
    return (
      <details className={`map-language-control ${className}`}>
        <summary aria-label={t('lang.label')} title={t('lang.label')}>
          <Icon name="language" />
        </summary>
        <ul>
          {offered.map((language) => (
            <li key={language}>
              <a
                href={urlForLanguage(language)}
                hrefLang={language}
                lang={language}
                aria-current={language === current ? 'true' : undefined}
                className={`map-style-btn ${language === current ? 'active' : ''}`}
              >
                {t(`lang.${language}`)}
              </a>
            </li>
          ))}
        </ul>
      </details>
    );
  }

  return (
    <nav className={`language-switcher ${className}`} aria-label={t('lang.label')}>
      <ul>
        {offered.map((language) => (
          <li key={language}>
            <a
              href={urlForLanguage(language)}
              hrefLang={language}
              lang={language}
              aria-current={language === current ? 'true' : undefined}
              className={language === current ? 'is-current' : undefined}
            >
              {/* The name of each language in that language -- someone looking
                  for Odia is looking for "ଓଡ଼ିଆ", not for "Odia". */}
              {t(`lang.${language}`)}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export default LanguageSwitcher;
