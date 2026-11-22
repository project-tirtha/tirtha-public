/**
 * The keys the app asks for actually resolve (internal design notes).
 *
 * There were already tests asserting these keys exist in the JSON files. They
 * passed while every one of them rendered as its own key: the strings live in
 * the `translation` namespace, the components ask for them without a namespace
 * prefix, and `defaultNS` was `common` with no `fallbackNS`. So the skip link
 * read "a11y.skipToContent" and a marker announced "Konark Sun Temple,
 * map.status.published" to a screen reader.
 *
 * Asserting a key is present in a file is not the same as asserting the running
 * app can reach it. This does the second thing.
 */
import { describe, expect, it } from 'vitest';

import i18n from './config';

/** Every key the components look up without a namespace prefix. */
const KEYS = [
  'a11y.skipToContent',
  'a11y.mapRegion',
  'a11y.closeDialog',
  'a11y.closeSidebar',
  'a11y.openSidebar',
  'app.name',
  'map.status.published',
  'map.status.in_progress',
  'map.status.needs_images',
  'site.viewerLoading',
  'site.viewerFailed',
  'footer.developedBy',
];

describe('translation lookup', () => {
  it('resolves every key the UI asks for, rather than echoing it', () => {
    for (const key of KEYS) {
      expect(i18n.t(key), `"${key}" did not resolve`).not.toBe(key);
    }
  });

  it('returns text, not an object, for each of them', () => {
    for (const key of KEYS) {
      expect(typeof i18n.t(key), `"${key}" is not a string`).toBe('string');
    }
  });

  /*
    The cluster label is not in `KEYS` above and cannot be: it is a counted
    string, so a bare `t('map.cluster.label')` correctly returns the key. It is
    also invisible to the source scanner at the bottom of this file, because the
    call site reads `tRef.current('map.cluster.label')` -- `\bt\(` does not
    match `current(`. So it is checked here, by hand, in every language.
  */
  it('interpolates the cluster label instead of leaving the placeholders', () => {
    const label = i18n.t('map.cluster.label', { count: 12, published: 8 });
    expect(label).toContain('12');
    expect(label).toContain('8');
    expect(label).not.toContain('{{');
  });

  it('has a singular form, so a pair of sites is not "1 sites"', () => {
    const one = i18n.t('map.cluster.label', { count: 1, published: 1 });
    expect(one).toContain('1');
    expect(one).not.toContain('1 sites');
  });

  it('resolves in every language, counted', async () => {
    for (const language of ['en', 'or', 'hi']) {
      await i18n.changeLanguage(language);
      for (const count of [1, 12]) {
        const label = i18n.t('map.cluster.label', { count, published: 1 });
        expect(label, `${language} at ${count}`).not.toBe('map.cluster.label');
        expect(typeof label).toBe('string');
        expect(label).not.toContain('{{');
      }
    }
    await i18n.changeLanguage('en');
  });

  it('still finds the UI namespaces it was already using', () => {
    // `fallbackNS` must not shadow the namespaced strings the front end ships.
    expect(i18n.t('sidebar:menuItems', { returnObjects: true })).toBeTruthy();
  });

  it('has Odia and Hindi loaded, not merely present on disk', async () => {
    for (const language of ['or', 'hi']) {
      await i18n.changeLanguage(language);
      const label = i18n.t('a11y.mapRegion');
      expect(label, `${language} falls back to English`).not.toBe(
        'Map of heritage sites',
      );
      expect(label).not.toBe('a11y.mapRegion');
    }
    await i18n.changeLanguage('en');
  });
});

/**
 * The UI chrome, in all three languages.
 *
 * `common`, `sidebar` and `theme` were registered for English only. i18next
 * falls back silently, so switching to Odia gave a page with the map vocabulary
 * translated and every button, panel heading and About entry still in English.
 * Nothing failed; it just quietly was not translated.
 */
describe('language coverage', () => {
  const NAMESPACES = ['common', 'sidebar', 'theme'];

  it.each(['or', 'hi'])('has the UI namespaces loaded for %s', async (language) => {
    await i18n.changeLanguage(language);
    for (const namespace of NAMESPACES) {
      expect(
        i18n.hasResourceBundle(language, namespace),
        `${language} is missing the "${namespace}" namespace`,
      ).toBe(true);
    }
    await i18n.changeLanguage('en');
  });

  it.each(['or', 'hi'])('translates the buttons rather than falling back for %s', async (language) => {
    await i18n.changeLanguage(language);
    expect(i18n.t('common:buttons.contribute')).not.toBe('Contribute');
    expect(i18n.t('theme:toggle')).not.toBe('Toggle Theme');
    await i18n.changeLanguage('en');
  });

  it('finds the About entry by id, not by its English title', async () => {
    /*
      `menuItems.find(item => item.title === 'What is Project Tirtha?')` cannot
      match a translated title, so the About panel opened empty in Odia and
      Hindi -- the strings were there and the lookup could not reach them.
    */
    for (const language of ['en', 'or', 'hi']) {
      await i18n.changeLanguage(language);
      const items = i18n.t('sidebar:menuItems', { returnObjects: true });
      expect(Array.isArray(items), `${language} has no menuItems`).toBe(true);
      const about = items.find((item) => item.id === 'about');
      expect(about?.content, `${language} has no About content`).toBeTruthy();
    }
    await i18n.changeLanguage('en');
  });

  it('carries no legal prose, in any language', async () => {
    // The documents live at their own URLs and are included from one template.
    // A paraphrase in a locale file is a second document nobody wrote.
    for (const language of ['en', 'or', 'hi']) {
      await i18n.changeLanguage(language);
      const items = i18n.t('sidebar:menuItems', { returnObjects: true });
      const ids = items.map((item) => item.id);
      expect(ids).not.toContain('terms');
      expect(ids).not.toContain('privacy');
    }
    await i18n.changeLanguage('en');
  });
});

/**
 * Every key the source asks for exists, in every language.
 *
 * The list above is hand-written and therefore always behind the code. This
 * reads the components instead: a `t('...')` added without a matching string
 * renders as its own key on the page -- "contribute.camera" where a label
 * should be -- and nothing else catches that.
 *
 * It is also what keeps Odia and Hindi honest. i18next falls back to English
 * silently, so a key added to `en.json` alone looks perfectly fine in
 * development and is simply untranslated for everyone else.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');

function sourceFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...sourceFiles(full));
    } else if (/\.jsx?$/.test(entry) && !entry.includes('.test.')) {
      out.push(full);
    }
  }
  return out;
}

/** Literal `t('key')` calls. Dynamic keys cannot be checked this way. */
function keysUsed() {
  const keys = new Set();
  for (const file of sourceFiles(SRC)) {
    const text = readFileSync(file, 'utf8');
    for (const [, key] of text.matchAll(/\bt\(\s*'([a-zA-Z][\w.:]*)'/g)) {
      keys.add(key);
    }
  }
  return [...keys].sort();
}

/*
  Two shapes a plain `t(key)` cannot check.

  A counted string lives under `key_one`/`key_other` and resolves only when a
  `count` is supplied; a list (the About panel, the checklist, the document
  links) resolves to an array under `returnObjects`. Both come back as the bare
  key from a naive lookup, so they are asked for the way the components ask.
*/
function resolves(key, language) {
  /*
    `getFixedT`, not `t({ lng })`. The `lng` option is not honoured for an
    `returnObjects` lookup, so comparing a list against English that way returns
    the *current* language both times -- which reported every translated list as
    an untranslated one.
  */
  const fixed = i18n.getFixedT(language);
  /*
    Lists first. Asking for one *without* `returnObjects` does not return the
    key -- it returns the diagnostic "key '...' returned an object instead of
    string", which is neither the key nor a translation, and is identical in
    every language. Checking that branch first reported every translated list
    as an untranslated one.
  */
  const list = fixed(key, { returnObjects: true });
  if (Array.isArray(list) || (list && typeof list === 'object')) return list;
  const counted = fixed(key, { count: 2 });
  if (counted !== key) return counted;
  return key;
}

describe('every literal translation key resolves', () => {
  const KEYS_IN_SOURCE = keysUsed();

  it('finds a meaningful number of keys, so a broken scan does not pass silently', () => {
    expect(KEYS_IN_SOURCE.length).toBeGreaterThan(40);
  });

  it.each(['en', 'or', 'hi'])('resolves all of them in %s', async (language) => {
    await i18n.changeLanguage(language);
    const missing = KEYS_IN_SOURCE.filter((key) => resolves(key, language) === key);
    await i18n.changeLanguage('en');
    expect(missing, `unresolved in ${language}: ${missing.join(', ')}`).toEqual([]);
  });

  it.each(['or', 'hi'])('has its own string for each of them in %s', async (language) => {
    /*
      Not merely resolvable -- actually translated. A key present only in
      `en.json` resolves in Odia too, because i18next falls back, and looks
      identical to a finished translation from inside the code.
    */
    await i18n.changeLanguage(language);
    const untranslated = KEYS_IN_SOURCE.filter((key) => {
      const translated = resolves(key, language);
      const english = resolves(key, 'en');
      if (typeof translated !== 'string' || typeof english !== 'string') {
        // Lists are compared by their JSON, so a list left in English is caught
        // the same way a string is.
        return JSON.stringify(translated) === JSON.stringify(english);
      }
      // Proper nouns and numerals are legitimately the same in every language.
      return translated === english && /[A-Za-z]{4}/.test(english);
    });
    await i18n.changeLanguage('en');
    expect(
      untranslated,
      `${language} falls back to English for: ${untranslated.join(', ')}`,
    ).toEqual([]);
  });
});
