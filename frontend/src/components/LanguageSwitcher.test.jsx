/**
 * The language control, in both of its shapes.
 *
 * There were no tests for this at all while it changed three times in one day:
 * a segmented pill, then a single square labelled with the language you were
 * *not* reading, then the icon menu the rest of the stack uses. "224 tests
 * pass" said nothing about any of them.
 *
 * What matters here is that it stays a set of links. `LanguageSwitcher` routes
 * by URL because the server renders the title, description and canonical link
 * per language for unfurl crawlers that do not run JavaScript -- a client-side
 * toggle would leave the URL and every shared preview claiming the old
 * language.
 */

import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.mock('../utils/deployment', () => ({
  languageFromPath: () => 'en',
  offeredLanguages: () => ['en', 'or'],
  urlForLanguage: (l) => (l === 'en' ? '/project/tirtha/' : `/project/tirtha/${l}/`),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key) =>
      ({
        'lang.label': 'Language',
        'lang.en': 'English',
        'lang.or': 'ଓଡ଼ିଆ',
      })[key] ?? key,
  }),
}));

const LanguageSwitcher = (await import('./LanguageSwitcher')).default;

describe('the compact control', () => {
  beforeEach(() => render(<LanguageSwitcher compact />));

  it('offers every language, not only the one you are not reading', () => {
    // The shape before this one showed a single square labelled with the other
    // language: the label was the option rather than the state, and it changed
    // word on click.
    expect(screen.getByRole('link', { name: 'English' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'ଓଡ଼ିଆ' })).toBeInTheDocument();
  });

  it('marks the one being read', () => {
    expect(screen.getByRole('link', { name: 'English' })).toHaveAttribute(
      'aria-current',
      'true',
    );
    expect(screen.getByRole('link', { name: 'ଓଡ଼ିଆ' })).not.toHaveAttribute('aria-current');
  });

  it('is a disclosure, so the icon square has an accessible name', () => {
    // Icon-only summaries are unnameable otherwise.
    expect(screen.getByLabelText('Language')).toBeInTheDocument();
  });

  it('keeps them links, with a real href per language', () => {
    expect(screen.getByRole('link', { name: 'ଓଡ଼ିଆ' })).toHaveAttribute(
      'href',
      '/project/tirtha/or/',
    );
  });

  it('says what each link is written in', () => {
    // `hrefLang` and `lang` are what tell a screen reader to switch voice, and
    // a crawler which alternate it has found.
    const odia = screen.getByRole('link', { name: 'ଓଡ଼ିଆ' });
    expect(odia).toHaveAttribute('hreflang', 'or');
    expect(odia).toHaveAttribute('lang', 'or');
  });
});

describe('the full control', () => {
  it('is a labelled nav rather than a disclosure', () => {
    render(<LanguageSwitcher />);
    const nav = screen.getByRole('navigation', { name: 'Language' });
    expect(within(nav).getAllByRole('link')).toHaveLength(2);
  });
});
