"""
Tests for multilingual routing and content (internal design notes).

The central decision: **the language lives in the URL path**, not only in a
header. `Accept-Language` negotiation cannot give per-language unfurls or
shareable localised links -- an unfurl crawler sends no useful
Accept-Language, and a link someone shares must open in the language they were
reading.
"""

import json
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from tirtha.models import Site

FRONTEND = Path(__file__).resolve().parents[3] / "frontend" / "src"
FRONTEND_LOCALES = FRONTEND / "i18n" / "locales"


@override_settings(REACT_SHELL_ENABLED=True)
class LanguageRoutingTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="Konark Sun Temple",
            country="India",
            state="Odisha",
            district="Puri",
        )

    def test_the_default_route_is_english(self):
        response = self.client.get(reverse("shell"))
        self.assertContains(response, 'lang="en"')

    def test_a_language_prefixed_route_sets_the_document_language(self):
        for code in ("or", "hi"):
            with self.subTest(lang=code):
                response = self.client.get(reverse("shell-lang", kwargs={"lang": code}))
                self.assertContains(response, f'lang="{code}"')

    def test_an_unknown_language_redirects_rather_than_404ing(self):
        """
        A stale or mistyped prefix still gets the page -- after one redirect.

        This used to assert a 200 with `lang="en"`, and that turned out to be
        the bug rather than the behaviour: Django served `/xx/` happily, but
        the React router recognises only `or` and `hi`, so its basename did not
        match the path and it rendered *nothing*. A 200 that renders nothing is
        indistinguishable from a 404 to whoever is looking at it, and much
        harder to find -- it is exactly how `/or/site/<id>/` came to be
        reported as a 404 while Django was serving it perfectly.

        Redirecting to the canonical English URL keeps the intent (a stale
        prefix must not be a dead end) and drops the blank page.
        """
        # Built by hand, not reversed: the `lang` converter matches only the
        # languages actually served, so `xx` has no route to reverse. That is
        # the point -- an unknown segment is not a language route, it falls to
        # the shell catch-all, and the shell is what redirects it.
        unknown = f"/{settings.PRE_URL}xx/"
        response = self.client.get(unknown)
        self.assertEqual(response.status_code, 301)

        followed = self.client.get(unknown, follow=True)
        self.assertEqual(followed.status_code, 200)
        self.assertEqual(len(followed.redirect_chain), 1)
        self.assertContains(followed, 'lang="en"')

    def test_the_locale_is_declared_for_unfurls(self):
        response = self.client.get(reverse("shell-lang", kwargs={"lang": "or"}))
        self.assertContains(response, 'property="og:locale" content="or"')

    def test_alternates_are_advertised_for_every_language(self):
        # A crawler must know these are the same page in another language,
        # not duplicates competing with each other.
        response = self.client.get(reverse("shell"))
        for code in ("en", "or", "hi"):
            with self.subTest(lang=code):
                self.assertContains(response, f'hreflang="{code}"')

    def test_alternates_preserve_the_route(self):
        response = self.client.get(
            reverse("shell-lang-site", kwargs={"lang": "hi", "site_id": self.site.ID})
        )
        self.assertContains(response, f"/or/site/{self.site.ID}")

    def test_unprefixed_links_keep_working(self):
        # Existing shared links must not break when prefixes are introduced.
        response = self.client.get(
            reverse("shell-site", kwargs={"site_id": self.site.ID})
        )
        self.assertEqual(response.status_code, 200)


class LocalisedPathTests(TestCase):
    """
    The paths are built from `PRE_URL`, not written out.

    These used to hardcode `/project/tirtha/`, which is production's prefix and
    dev's. Beta serves `beta/tirtha/` and the demo `project/tirthademo/`, so the
    two rewriting tests failed on any deployment but the two they were written
    against -- and they failed on the *prefix*, saying nothing about whether the
    language rewriting they exist to check still worked.
    """

    @property
    def prefix(self) -> str:
        return "/" + settings.PRE_URL

    def test_a_prefix_is_inserted_when_absent(self):
        from tirtha.shell import _localised_path

        self.assertEqual(_localised_path(f"{self.prefix}", "or"), f"{self.prefix}or/")

    def test_an_existing_prefix_is_replaced_not_stacked(self):
        from tirtha.shell import _localised_path

        self.assertEqual(
            _localised_path(f"{self.prefix}hi/", "or"), f"{self.prefix}or/"
        )

    def test_the_rest_of_the_route_survives(self):
        from tirtha.shell import _localised_path

        self.assertIn(
            "site/ABC123",
            _localised_path(f"{self.prefix}site/ABC123", "hi"),
        )


class LocaleFileTests(TestCase):
    """
    The UI strings themselves.

    Odia and Hindi are machine-drafted and need native review (§26 records the
    editorial workflow as open). What is tested here is *completeness*: a
    missing key falls back to English silently, which reads as a half-translated
    page rather than an error.
    """

    def _keys(self, obj, prefix=""):
        found = set()
        for key, value in obj.items():
            if isinstance(value, dict):
                found |= self._keys(value, f"{prefix}{key}.")
            else:
                found.add(f"{prefix}{key}")
        return found

    def _load(self, code):
        return json.loads((FRONTEND_LOCALES / f"{code}.json").read_text("utf-8"))

    def test_every_language_has_every_key(self):
        english = self._keys(self._load("en"))
        for code in ("or", "hi"):
            with self.subTest(lang=code):
                self.assertEqual(self._keys(self._load(code)), english)

    def _flat(self, obj, prefix=""):
        """
        Locale files nest arbitrarily deep -- tour copy is three levels.

        **Lists are walked too.** i18next returns an array for
        `t(key, {returnObjects: true})`, which the upload checklist and the
        sidebar's menu both use, and treating one as a leaf handed a `list` to
        `re.findall` -- so every check below either crashed or silently skipped
        the several dozen strings that live inside arrays.
        """
        flat = {}
        for key, value in obj.items():
            path = f"{prefix}{key}"
            if isinstance(value, dict):
                flat.update(self._flat(value, f"{path}."))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, dict):
                        flat.update(self._flat(item, f"{path}.{index}."))
                    else:
                        flat[f"{path}.{index}"] = item
            else:
                flat[path] = value
        return flat

    def test_no_string_is_left_in_english_by_accident(self):
        # A few legitimately match: language names are written in their own
        # script, and "MP4" is not translated. The length bound skips those.
        english = self._flat(self._load("en"))
        for code in ("or", "hi"):
            translated = self._flat(self._load(code))
            odd = [
                key
                for key, value in english.items()
                if not key.startswith("lang.")
                and isinstance(value, str)
                and len(value) > 12
                and translated[key] == value
            ]
            self.assertEqual(odd, [], f"{code} untranslated: {odd}")

    def test_interpolation_placeholders_are_preserved(self):
        # A dropped {{site}} renders a literal brace to the visitor.
        import re

        english = self._flat(self._load("en"))
        for code in ("or", "hi"):
            translated = self._flat(self._load(code))
            for key, value in english.items():
                if not isinstance(value, str):
                    continue
                with self.subTest(lang=code, key=key):
                    self.assertEqual(
                        set(re.findall(r"{{(\w+)}}", value)),
                        set(re.findall(r"{{(\w+)}}", translated[key])),
                    )


class LanguageRouteParityTests(TestCase):
    """
    Every shell route exists in every language (internal design notes).

    The two families were maintained as two hand-written lists and drifted:
    English reached `app/site/<id>/<slug>/` and the `<runid>` route beneath it,
    the language-prefixed side stopped at `<id>/`. So an Odia or Hindi share of
    a site page -- the slug form, which is what the app itself links to -- got a
    Django 404 while the English equivalent worked.

    They are generated from one list now. This asserts the property rather than
    the generator, so it still holds if the generator is replaced.
    """

    SUFFIXES = ("", "-site", "-site-slug", "-site-run")

    def test_every_english_route_has_a_language_counterpart(self):
        from django.urls import get_resolver

        names = set(
            n
            for n in get_resolver().reverse_dict
            if isinstance(n, str) and n.startswith("shell")
        )
        for suffix in self.SUFFIXES:
            with self.subTest(route=suffix):
                self.assertIn(f"shell{suffix}", names)
                self.assertIn(f"shell-lang{suffix}", names)

    def test_a_language_slug_url_resolves(self):
        """The exact shape reported as a 404 from the demo."""
        for lang in ("or", "hi"):
            with self.subTest(lang=lang):
                url = reverse(
                    "shell-lang-site-slug",
                    kwargs={"lang": lang, "site_id": "abc123", "slug": "A__B__C"},
                )
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_the_catchall_covers_language_prefixes_too(self):
        """It matched `app/` only, so `/or/anything` still 404'd."""
        for path in ("or/site", "hi/nonsense/deeper"):
            with self.subTest(path=path):
                response = self.client.get(f"/{settings.PRE_URL}{path}")
                self.assertEqual(response.status_code, 200)


class NoHardcodedUiText(SimpleTestCase):
    """
    Visible text comes from the locale files, not from the JSX.

    The accessibility tests already check `aria-label`; they cannot see a bare
    string between tags, which is where most of the untranslated text was
    hiding. "Contribute", "Description", "Geo Coordinates", "View on Google
    Maps", "Loading sites..." and the two buttons in the map's attribution row
    all rendered in English on the Odia and Hindi pages, beside strings that
    were translated -- which reads worse than an untranslated page, because it
    looks like the translation broke.

    Deliberately narrow: an English *sentence* between tags, three words or
    more, or a known short label. A regex that flagged every capitalised word
    would flag `{count}` interpolations, unit symbols and proper nouns, and a
    test people silence is worse than no test.
    """

    # Two or more words between tags, allowing an ALL-CAPS or parenthesised
    # first word. The narrower first version required three words and a
    # lowercase tail, and five strings walked straight through it: "HEIC Image
    # Preview Unavailable" (caps), "Total Size:" (two words), "Allow full
    # resolution uploads (skip compression/resizing)" (parentheses).
    SENTENCE = re.compile(
        r">\s*([A-Z][A-Za-z]*(?:\s+[A-Za-z0-9',.()/&-]+){1,}[.?!:]?)\s*<",
    )

    # Everything a component may legitimately contain as a literal.
    # Proper nouns and marks, which are not translated. Kept short on purpose:
    # every entry here is a string this test can no longer see.
    ALLOWED = re.compile(
        r"^(Project Tirtha|OpenStreetMap|OpenFreeMap|OpenMapTiles|Google Maps"
        r"|CC BY[A-Z-]*|IMG_)",
    )

    def test_no_component_renders_an_english_sentence(self):
        offenders = []
        for path in FRONTEND.rglob("*.jsx"):
            if ".test." in path.name:
                continue
            source = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
            source = re.sub(r"\{/\*.*?\*/\}", "", source, flags=re.S)
            for match in self.SENTENCE.finditer(source):
                text = match.group(1).strip()
                if self.ALLOWED.match(text):
                    continue
                offenders.append(f"{path.name}: {text[:60]}")

        self.assertEqual(
            offenders,
            [],
            "these render English text that is not in the locale files:\n  "
            + "\n  ".join(offenders),
        )
