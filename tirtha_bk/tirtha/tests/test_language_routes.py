"""
Which language-prefixed URLs exist, and what the others do instead.

English is served unprefixed (internal design notes), so `<prefix>/en/` is not a URL
this deployment means to answer. But the route's `<str:lang>` matched any
two-letter string, Django served it 200, and the React router -- which
deliberately excludes `en` from its basename -- then matched nothing and rendered
a blank page.

That URL was on every page. It was the `hreflang="en"` alternate: the tag whose
entire job is telling a crawler where the English version of this page is. So the
one link guaranteed to be followed by a machine pointed at a blank 200, and
English was split into two competing URLs, one of which shows nothing.

A 200 that renders nothing is indistinguishable from a 404 to whoever is looking
at it, and much harder to find. That is the same failure that made
`/project/tirthademo/or/site/<id>/` get reported as a 404 when Django was
serving it perfectly.
"""

import re

from django.test import TestCase


class Alternates(TestCase):
    def test_the_english_alternate_is_the_unprefixed_url(self):
        body = self.client.get("/project/tirtha/or/").content.decode()
        alternates = dict(re.findall(r'hreflang="(\w+)" href="([^"]+)"', body))
        self.assertTrue(alternates["en"].endswith("/project/tirtha/"))
        self.assertNotIn("/en/", alternates["en"])

    def test_the_other_alternates_carry_their_segment(self):
        body = self.client.get("/project/tirtha/").content.decode()
        alternates = dict(re.findall(r'hreflang="(\w+)" href="([^"]+)"', body))
        self.assertIn("/or/", alternates["or"])
        self.assertIn("/hi/", alternates["hi"])

    def test_a_site_page_keeps_its_path_across_languages(self):
        body = self.client.get("/project/tirtha/site/abc123/").content.decode()
        alternates = dict(re.findall(r'hreflang="(\w+)" href="([^"]+)"', body))
        for language in ("en", "or", "hi"):
            with self.subTest(language=language):
                self.assertTrue(
                    alternates[language].endswith("site/abc123/"),
                    f"{language} alternate drops the site: {alternates[language]}",
                )


class UnservedSegments(TestCase):
    """
    Everything that is not `or` or `hi` redirects, once, to the English URL.
    """

    CASES = [
        ("/project/tirtha/en/", "/project/tirtha/"),
        ("/project/tirtha/en/terms/", "/project/tirtha/terms/"),
        ("/project/tirtha/en/site/abc/", "/project/tirtha/site/abc/"),
        # Not a language we serve at all, and a plausible typo for `or`.
        ("/project/tirtha/fr/", "/project/tirtha/"),
        ("/project/tirtha/od/site/abc/", "/project/tirtha/site/abc/"),
        ("/project/tirtha/xx/privacy/", "/project/tirtha/privacy/"),
    ]

    def test_each_redirects_to_the_canonical_english_url(self):
        for path, target in self.CASES:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 301, path)
                self.assertEqual(response.headers["Location"], target)

    def test_the_catch_all_applies_the_same_rule(self):
        """
        The four named route shapes are not the only way in.

        `/fr/app/nothing-here` and `/en/app/x/y/` match none of them and fall to
        `shell-catchall`, which passes no `lang` -- so they were still 200s the
        client router could not match, which is a blank page. Nothing links
        them, but a stale bookmark or a truncated share is exactly how someone
        arrives at one.
        """
        for path, target in (
            ("/project/tirtha/fr/nothing-here", "/project/tirtha/nothing-here"),
            ("/project/tirtha/en/x/y/", "/project/tirtha/x/y/"),
            ("/project/tirtha/od/a/b/c", "/project/tirtha/a/b/c"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 301, path)
                self.assertEqual(response.headers["Location"], target)

    def test_a_path_with_no_language_segment_is_not_mangled(self):
        # `app` is two letters short of a language but three long; the guard is
        # a shape check, and stripping the route's own first segment would be
        # the obvious way to get this wrong.
        response = self.client.get("/project/tirtha/nothing-here")
        self.assertEqual(response.status_code, 200)

    def test_the_redirect_does_not_loop(self):
        """
        The first attempt did. `_localised_path` only strips a segment it
        recognises as a language, so redirecting `/fr/app/` through it produced
        `/fr/app/` again -- a permanent redirect to itself, which a browser
        reports as ERR_TOO_MANY_REDIRECTS and a crawler records as a broken URL.
        """
        for path, _ in self.CASES:
            with self.subTest(path=path):
                response = self.client.get(path, follow=True)
                self.assertEqual(len(response.redirect_chain), 1, path)
                self.assertEqual(response.status_code, 200)


class ServedSegments(TestCase):
    def test_odia_and_hindi_render_rather_than_redirect(self):
        for path in (
            "/project/tirtha/or/",
            "/project/tirtha/hi/",
            "/project/tirtha/or/site/abc/",
            "/project/tirtha/hi/terms/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_the_page_declares_the_language_the_url_asks_for(self):
        for language in ("or", "hi"):
            with self.subTest(language=language):
                body = self.client.get(f"/project/tirtha/{language}/").content.decode()
                self.assertIn(f'<html lang="{language}"', body)

    def test_a_not_found_path_under_a_language_keeps_that_language(self):
        """
        The catch-all route passes no `lang`, so the view reads it back off the
        path. Without that, an unmatched path under /or/ rendered its meta tags
        in English -- a crawler would be told the Odia page is English.
        """
        body = self.client.get("/project/tirtha/or/nothing-here").content.decode()
        self.assertIn('<html lang="or"', body)
