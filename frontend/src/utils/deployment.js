/**
 * Where this bundle is mounted, and in which language, read from the live URL.
 *
 * Django serves the shell under a path prefix that differs per deployment --
 * `/project/tirtha/` in production, `/project/tirthademo/` on the demo,
 * `/beta/tirtha/` on beta (internal design notes) -- and *then*, for Odia and Hindi,
 * under a language segment as well: `/project/tirthademo/or/app/`.
 *
 * The router built its basename as `<prefix>/app` and stopped there. At
 * `/project/tirthademo/or/app/site/qovMSNwwBKG3Ui2z/` that basename does not
 * match the path, so `BrowserRouter` matched nothing and the page rendered
 * empty -- a reported "404" that was really a blank app served with a 200.
 *
 * These live outside the router module so both it and the language switcher can
 * use them without the module exporting non-components.
 */

/**
 * The languages the shell **serves** under a path prefix (internal design notes).
 *
 * Not the same list as the one the switcher offers. A `/hi/` URL that somebody
 * has already shared or bookmarked must keep resolving, and the server still
 * renders its meta tags, so Hindi stays here even while it is not offered.
 */
export const LANGUAGES = ['en', 'or', 'hi'];

/**
 * The languages the switcher **offers**.
 *
 * Hindi is withheld for now at the maintainer's request: every string in it is
 * my unreviewed machine draft, and offering a language nobody has read is a
 * worse promise than not offering it. The strings stay in the repository and
 * the routes stay live, so restoring it is one entry in this array.
 *
 * The intended direction is not "add Hindi back" but "offer the language of the
 * place you are looking at" -- Odia in Odisha, Kannada in Karnataka, Marathi in
 * Maharashtra -- derived from the site's own administrative fields, which the
 * API already publishes. That is a larger piece of work and is recorded in
 * internal design notes.
 */
export function offeredLanguages() {
  // From `config/display.json`, so withholding or restoring a language is an
  // edit and a refresh rather than a code change. Filtered against LANGUAGES:
  // the switcher must not offer a language the server does not serve.
  return displayConfig().offeredLanguages.filter((code) => LANGUAGES.includes(code));
}

/**
 * Where this bundle is mounted, with no trailing slash.
 *
 * One definition. This was exported from here *and* from `session.js`, and
 * implemented a third time privately inside `apiConfig.js` -- three readers of
 * one meta tag, of which only `apiConfig`'s guarded against `document` being
 * undefined. The guard is kept here.
 */
export function deploymentPrefix() {
  if (typeof document === 'undefined') return '';
  return document.querySelector('meta[name="tirtha-prefix"]')?.content?.replace(/\/$/, '') ?? '';
}

/**
 * What the site is configured to show, from `config/display.json`.
 *
 * The server renders it into a meta tag; this reads it once and falls back to
 * the shipped answer for anything missing or unparseable. A deployment serving
 * an older shell, or a test rendering a component in isolation, gets the
 * defaults rather than a crash.
 */
const DISPLAY_DEFAULTS = {
  animatedPreviews: true,
  showBetaBadge: null,
  offeredLanguages: ['en', 'or'],
  mapClusterRadius: 28,
};

let displayCache = null;

export function displayConfig() {
  if (displayCache) return displayCache;
  if (typeof document === 'undefined') return DISPLAY_DEFAULTS;
  let parsed;
  try {
    const raw = document.querySelector('meta[name="tirtha-display"]')?.content;
    parsed = raw ? JSON.parse(raw) : {};
  } catch {
    // A malformed attribute is a deployment mistake, not a reason to show a
    // blank page.
    parsed = {};
  }
  displayCache = { ...DISPLAY_DEFAULTS, ...parsed };
  return displayCache;
}

/** Tests render components against different configurations. */
export function resetDisplayConfig() {
  displayCache = null;
}

/**
 * Whether this deployment is a preview rather than the real site.
 *
 * The "Beta" badge was unconditional, so production would have carried it too
 * -- telling every visitor the archive they are looking at is provisional when
 * it is the thing itself.
 *
 * Read from the deployment prefix, which the server already renders into the
 * page: `/project/tirthademo/` and `/beta/tirtha/` are previews,
 * `/project/tirtha/` is the site.
 */
export function isPreRelease() {
  // An explicit true/false in the config wins; `null` means work it out.
  const configured = displayConfig().showBetaBadge;
  if (typeof configured === 'boolean') return configured;
  const prefix = deploymentPrefix();
  return /demo|beta|staging/i.test(prefix);
}

function firstSegment(pathname) {
  const prefix = deploymentPrefix();
  const rest = pathname.startsWith(prefix) ? pathname.slice(prefix.length) : pathname;
  return rest.split('/').filter(Boolean)[0];
}

/**
 * The router's basename.
 *
 * The app is served at the deployment root -- `/project/tirtha/` -- not under
 * `/app/`, which is where it lived while the Django templates still owned the
 * root. `/app/` still resolves; the server 301s it here.
 *
 * English is served without a language segment, so it must not get one here
 * either: `<prefix>/en/` is not a URL this deployment answers.
 */
export function routerBasename(pathname = window.location.pathname) {
  const segment = firstSegment(pathname);
  const prefix = deploymentPrefix();
  return LANGUAGES.includes(segment) && segment !== 'en'
    ? `${prefix}/${segment}`
    : prefix;
}

/** The language the URL is asking for, which is what the app must display. */
export function languageFromPath(pathname = window.location.pathname) {
  const segment = firstSegment(pathname);
  return LANGUAGES.includes(segment) ? segment : 'en';
}

/**
 * The same page in another language.
 *
 * A language switch is a navigation, not a state change: the server renders the
 * meta tags and the `hreflang` alternates per language (internal design notes), so a
 * switch that only called `i18n.changeLanguage` would leave the URL, the
 * canonical link and every unfurl claiming the previous language.
 */
export function urlForLanguage(
  language,
  pathname = window.location.pathname,
  search = window.location.search,
) {
  const prefix = deploymentPrefix();
  const current = languageFromPath(pathname);
  const base = current === 'en' ? prefix : `${prefix}/${current}`;
  const rest = pathname.startsWith(base) ? pathname.slice(base.length) : '';
  const target = language === 'en' ? prefix : `${prefix}/${language}`;
  // The query string comes along. It carries `?model=closed`, which is how the
  // URL says the viewer is shut (see `Home`); dropping it here meant switching
  // language on a site page reopened the 3D model you had just closed -- the
  // switcher looked like it was opening the viewer, when it was only failing to
  // carry the state across the navigation.
  return `${target}${rest}${search}` || '/';
}
