/**
 * The basename, which is where the reported Odia 404 actually lived.
 *
 * `https://smlab.niser.ac.in/project/tirthademo/or/app/site/qovMSNwwBKG3Ui2z/`
 * was reported as a 404. Django served it fine -- the route exists, and the
 * shell rendered with Odia meta tags and a 200. The client router computed its
 * basename as `/project/tirthademo/app`, which does not match that path, so
 * `BrowserRouter` matched nothing and the page was blank. A 200 that renders
 * nothing is indistinguishable from a 404 to the person looking at it, and much
 * harder to find.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  deploymentPrefix,
  displayConfig,
  isPreRelease,
  languageFromPath,
  offeredLanguages,
  resetDisplayConfig,
  routerBasename,
  urlForLanguage,
} from './deployment';

describe('deployment paths', () => {
  beforeEach(() => {
    document.head.innerHTML = '<meta name="tirtha-prefix" content="/project/tirthademo/">';
  });

  afterEach(() => {
    document.head.innerHTML = '';
  });

  describe('routerBasename', () => {
    it('includes the language segment, which is what the 404 was', () => {
      expect(routerBasename('/project/tirthademo/or/site/abc/')).toBe(
        '/project/tirthademo/or',
      );
    });

    it('leaves English without a segment, because the server serves it that way', () => {
      // `/project/tirthademo/en/` is not a URL this deployment answers.
      expect(routerBasename('/project/tirthademo/')).toBe('/project/tirthademo');
    });

    it('is not fooled by a path segment that merely looks like a language', () => {
      // A slug that happens to read as a language code must not become one.
      expect(routerBasename('/project/tirthademo/site/hi/')).toBe(
        '/project/tirthademo',
      );
    });

    it('follows the prefix, so one build serves production, beta and the demo', () => {
      document.head.innerHTML = '<meta name="tirtha-prefix" content="/beta/tirtha/">';
      expect(routerBasename('/beta/tirtha/hi/')).toBe('/beta/tirtha/hi');
    });
  });

  describe('languageFromPath', () => {
    it('reads the language the URL asks for', () => {
      expect(languageFromPath('/project/tirthademo/or/')).toBe('or');
      expect(languageFromPath('/project/tirthademo/hi/')).toBe('hi');
    });

    it('defaults to English when there is no segment', () => {
      expect(languageFromPath('/project/tirthademo/')).toBe('en');
    });
  });

  describe('urlForLanguage', () => {
    it('keeps you on the same site when you switch language', () => {
      // The whole point: a language switch that dropped you back on the map
      // would be worse than no switcher.
      expect(urlForLanguage('or', '/project/tirthademo/site/abc/konark/')).toBe(
        '/project/tirthademo/or/site/abc/konark/',
      );
    });

    it('switches back to English without leaving an /en/ segment behind', () => {
      expect(urlForLanguage('en', '/project/tirthademo/hi/site/abc/')).toBe(
        '/project/tirthademo/site/abc/',
      );
    });

    it('moves between two non-English languages', () => {
      expect(urlForLanguage('hi', '/project/tirthademo/or/')).toBe(
        '/project/tirthademo/hi/',
      );
    });

    it('carries the query string, which is where the closed model lives', () => {
      // Reported as "clicking Odia opens the 3D viewer". The switcher was never
      // opening anything: it rebuilt the URL from the pathname alone, so
      // `?model=closed` did not survive the navigation and the page it landed
      // on read a bare site URL -- which means "open the model".
      expect(
        urlForLanguage('or', '/project/tirthademo/site/abc/konark/', '?model=closed'),
      ).toBe('/project/tirthademo/or/site/abc/konark/?model=closed');
    });

    it('adds no stray ? when there is no query string', () => {
      expect(urlForLanguage('or', '/project/tirthademo/site/abc/', '')).toBe(
        '/project/tirthademo/or/site/abc/',
      );
    });
  });
});

describe('deploymentPrefix, the single definition', () => {
  afterEach(() => {
    document.head.innerHTML = '';
  });

  it('reads the live prefix the shell emits', () => {
    document.head.innerHTML = '<meta name="tirtha-prefix" content="/project/tirthademo/">';
    expect(deploymentPrefix()).toBe('/project/tirthademo');
  });

  it('drops the trailing slash, so callers can concatenate a path', () => {
    document.head.innerHTML = '<meta name="tirtha-prefix" content="/beta/tirtha/">';
    expect(`${deploymentPrefix()}/signin/`).toBe('/beta/tirtha/signin/');
  });

  it('returns empty for a deployment mounted at the root', () => {
    expect(deploymentPrefix()).toBe('');
  });
});


describe('isPreRelease', () => {
  const withPrefix = (p) => {
    document.head.innerHTML = `<meta name="tirtha-prefix" content="${p}">`;
    return isPreRelease();
  };

  it('is false on production, which is the site itself', () => {
    // A permanent "Beta" badge on the real archive is a disclaimer nobody
    // meant to make.
    expect(withPrefix('/project/tirtha/')).toBe(false);
  });

  it('is true on the demo and on beta', () => {
    expect(withPrefix('/project/tirthademo/')).toBe(true);
    expect(withPrefix('/beta/tirtha/')).toBe(true);
  });
});

describe('displayConfig', () => {
  beforeEach(() => resetDisplayConfig());
  afterEach(() => resetDisplayConfig());

  const withConfig = (json) => {
    document.head.innerHTML =
      '<meta name="tirtha-prefix" content="/project/tirtha/">' +
      (json === null ? '' : `<meta name="tirtha-display" content='${json}'>`);
    return displayConfig();
  };

  it('reads what the server published', () => {
    expect(withConfig('{"animatedPreviews":false}').animatedPreviews).toBe(false);
  });

  it('falls back to the defaults when the tag is absent', () => {
    // An older shell, or a component rendered in isolation.
    expect(withConfig(null).animatedPreviews).toBe(true);
  });

  it('falls back rather than throwing on a malformed attribute', () => {
    // A deployment mistake must not be a blank page.
    expect(withConfig('{not json').mapClusterRadius).toBe(28);
  });

  it('fills in keys the server did not send', () => {
    // A config written before a setting existed must not remove it.
    const config = withConfig('{"animatedPreviews":false}');
    expect(config.offeredLanguages).toEqual(['en', 'or']);
  });

  it('lets the config decide the Beta badge outright', () => {
    document.head.innerHTML =
      '<meta name="tirtha-prefix" content="/project/tirtha/">' +
      `<meta name="tirtha-display" content='{"showBetaBadge":true}'>`;
    resetDisplayConfig();
    expect(isPreRelease()).toBe(true);
  });

  it('still works it out from the prefix when the config says null', () => {
    document.head.innerHTML =
      '<meta name="tirtha-prefix" content="/project/tirtha/">' +
      `<meta name="tirtha-display" content='{"showBetaBadge":null}'>`;
    resetDisplayConfig();
    expect(isPreRelease()).toBe(false);
  });

  it('never offers a language the server does not serve', () => {
    document.head.innerHTML =
      '<meta name="tirtha-prefix" content="/project/tirtha/">' +
      `<meta name="tirtha-display" content='{"offeredLanguages":["en","fr"]}'>`;
    resetDisplayConfig();
    expect(offeredLanguages()).toEqual(['en']);
  });
});
