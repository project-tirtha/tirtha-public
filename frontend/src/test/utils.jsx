import { render } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import { createInstance } from 'i18next';
import { initReactI18next } from 'react-i18next';

import { ThemeProvider } from '../context/ThemeContext';

import en from '../i18n/locales/en.json';
import or from '../i18n/locales/or.json';
import hi from '../i18n/locales/hi.json';
/*
  The UI chrome, in all three languages -- the same bundles `i18n/config.js`
  registers. There was a fourth import here, `../locales/en/home.json`, and that
  file exists in no language: every test that imported this module died at
  import resolution before its first assertion, which is why there were none.
*/
import commonEn from '../locales/en/common.json';
import sidebarEn from '../locales/en/sidebar.json';
import themeEn from '../locales/en/theme.json';
import commonOr from '../locales/or/common.json';
import sidebarOr from '../locales/or/sidebar.json';
import themeOr from '../locales/or/theme.json';
import commonHi from '../locales/hi/common.json';
import sidebarHi from '../locales/hi/sidebar.json';
import themeHi from '../locales/hi/theme.json';

/**
 * Components render against the **real** locale files, not a stub `t`.
 *
 * A mock that echoes its own key proves only that a component called `t`; it
 * cannot tell a translation from English left inline, which is the defect
 * internal design notes cares about. Loading the shipped resources means a test can
 * render in Odia and assert the Odia string.
 */
export function testI18n(language = 'en') {
  const instance = createInstance();
  instance.use(initReactI18next).init({
    resources: {
      en: { translation: en, common: commonEn, sidebar: sidebarEn, theme: themeEn },
      or: { translation: or, common: commonOr, sidebar: sidebarOr, theme: themeOr },
      hi: { translation: hi, common: commonHi, sidebar: sidebarHi, theme: themeHi },
    },
    lng: language,
    fallbackLng: 'en',
    defaultNS: 'common',
    /*
      These have to be the settings `i18n/config.js` ships, or a test renders
      against an i18next that behaves differently from the one visitors get.
      Without `fallbackNS` every unprefixed key -- `a11y.search`,
      `map.status.published`, `panel.allSites` -- resolves to itself, so an
      assertion on visible text would be asserting the key name back at itself
      and a genuinely missing string would still pass.
    */
    fallbackNS: 'translation',
    interpolation: { escapeValue: true },
  });
  return instance;
}

export const enText = (key, vars) => testI18n('en').t(key, vars ?? {});

/**
 * `ThemeProvider` belongs in the harness rather than in each test's own
 * wrapper: `useTheme` throws outside it, so a component that picks a light or
 * dark asset -- `Sidebar` does, for the logo -- cannot be rendered at all
 * without one, and a per-test wrapper would be the same four lines every time.
 */
export function renderWithProviders(ui, { language = 'en', ...options } = {}) {
  const i18n = testI18n(language);
  return render(ui, {
    wrapper: ({ children }) => (
      <I18nextProvider i18n={i18n}>
        <ThemeProvider>{children}</ThemeProvider>
      </I18nextProvider>
    ),
    ...options,
  });
}
