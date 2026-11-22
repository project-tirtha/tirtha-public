import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import commonEn from '../locales/en/common.json';
import sidebarEn from '../locales/en/sidebar.json';
import themeEn from '../locales/en/theme.json';
// The UI chrome in Odia and Hindi. These namespaces existed only in English, so
// a visitor who switched language got the map vocabulary translated and every
// button, panel heading and About entry still in English -- which reads as a
// half-finished translation rather than a language.
import commonOr from '../locales/or/common.json';
import sidebarOr from '../locales/or/sidebar.json';
import themeOr from '../locales/or/theme.json';
import commonHi from '../locales/hi/common.json';
import sidebarHi from '../locales/hi/sidebar.json';
import themeHi from '../locales/hi/theme.json';
// Accessible names and map copy, in all three languages (internal design notes). An
// aria-label left in English is an English-only label for anyone using a screen
// reader in Odia or Hindi, so these are translated like any other string.
import a11yEn from './locales/en.json';
import a11yOr from './locales/or.json';
import a11yHi from './locales/hi.json';

const resources = {
  en: {
    common: commonEn,
    sidebar: sidebarEn,
    theme: themeEn,
    translation: a11yEn
  },
  or: { common: commonOr, sidebar: sidebarOr, theme: themeOr, translation: a11yOr },
  hi: { common: commonHi, sidebar: sidebarHi, theme: themeHi, translation: a11yHi }
};

i18n
  .use(initReactI18next)
  .init({
    resources,
    lng: 'en',
    fallbackLng: 'en',
    defaultNS: 'common',
    // Without this every key in the `translation` namespace -- the accessible
    // names, the map status vocabulary, the page heading -- resolves to itself,
    // so the skip link read "a11y.skipToContent" and a marker announced
    // "Konark Sun Temple, map.status.published" to a screen reader. The
    // components ask for those keys without a namespace prefix, so the lookup
    // has to fall back to where they live.
    fallbackNS: 'translation',
    interpolation: {
      escapeValue: true
    }
  });

export default i18n;
