"""
The Terms of Use and the Privacy Policy, each on a URL of its own.

Before this they were `<details>` blocks folded into the legacy index page,
reachable only at `/#terms`. That is not a URL anyone can cite, an unfurl can
describe, or the SPA can link to from a language-prefixed route -- which is
exactly why the React sidebar ended up carrying its own rewritten, shorter,
*different* versions of both, while the site-request form asked people to
consent to them.

What is asserted here is that there is one copy, that it is the one the lawyers
saw, and that nothing paraphrases it.
"""

import json
import re
from pathlib import Path

from django.test import SimpleTestCase

TEMPLATES = Path(__file__).resolve().parents[1] / "templates" / "tirtha"
FRONTEND = Path(__file__).resolve().parents[3] / "frontend" / "src"


class DocumentRoutes(SimpleTestCase):
    def test_each_document_has_a_url(self):
        for name in ("terms", "privacy", "acknowledgment"):
            with self.subTest(document=name):
                response = self.client.get(f"/project/tirtha/{name}/")
                self.assertEqual(response.status_code, 200)

    def test_each_is_reachable_in_every_language(self):
        # Otherwise an Odia visitor following the consent link leaves the
        # language behind, or lands on a 404 (which is what `../#terms` did).
        for language in ("or", "hi"):
            for name in ("terms", "privacy"):
                with self.subTest(language=language, document=name):
                    response = self.client.get(f"/project/tirtha/{language}/{name}/")
                    self.assertEqual(response.status_code, 200)

    def test_the_page_carries_the_authored_text_not_a_summary(self):
        response = self.client.get("/project/tirtha/terms/")
        body = response.content.decode()
        # A clause specific to the real document. A paraphrase would not have it.
        self.assertIn("exclusive jurisdiction of the courts of Bhubaneswar", body)

    def test_the_privacy_page_carries_the_authored_text(self):
        body = self.client.get("/project/tirtha/privacy/").content.decode()
        self.assertIn("Privacy Policy", body)
        self.assertGreater(len(body), 2000)

    def test_the_document_is_actually_visible(self):
        """
        Not merely present in the HTML.

        These are `<details>` written for the legacy index's accordion, and
        included as-is they render folded: the page showed a heading and nothing
        else. Every test above still passed, because they grep the source, where
        the text is there -- just not shown to anyone. That is the whole class of
        mistake this file exists to catch, committed by the file itself.
        """
        for name in ("terms", "privacy", "acknowledgment"):
            with self.subTest(document=name):
                body = self.client.get(f"/project/tirtha/{name}/").content.decode()
                self.assertIn("<details open", body, f"/{name}/ renders folded")

    def test_the_wrapper_does_not_unfold_the_legacy_accordion(self):
        # The legacy index needs them closed; one copy on disk is the point.
        for name in ("terms", "privacy"):
            with self.subTest(document=name):
                source = (TEMPLATES / f"{name}.html").read_text()
                self.assertNotIn("<details open", source)


class NothingParaphrasesTheDocuments(SimpleTestCase):
    """
    The front end links to them; it does not restate them.

    The sidebar's copies said things the real documents do not -- "Users may
    freely access and view 3D models for non-commercial purposes", where the
    real terms name a specific Creative Commons licence -- and omitted the
    indemnity, the governing law, and the licence under which uploaded images
    are released. Someone consenting to the paraphrase consented to something
    else.
    """

    def _locale_text(self) -> str:
        chunks = []
        for path in FRONTEND.rglob("*.json"):
            if "locales" not in str(path):
                continue
            chunks.append(json.dumps(json.load(path.open()), ensure_ascii=False))
        return "\n".join(chunks)

    def test_no_locale_file_contains_a_terms_or_privacy_body(self):
        text = self._locale_text()
        for phrase in (
            "Please review our terms and conditions",
            "Your privacy is important to us",
            "We collect minimal personal information",
            "Any redistribution or commercial use requires explicit permission",
        ):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, text)

    def test_the_consent_links_point_at_the_canonical_urls(self):
        source = (FRONTEND / "components" / "RequestSiteModal.jsx").read_text()
        # Built from the deployment prefix, so they are right from every route
        # and on every deployment -- not relative, and not hardcoded to
        # production.
        self.assertRegex(source, r"deploymentPrefix\(\)\}/terms/")
        self.assertRegex(source, r"deploymentPrefix\(\)\}/privacy/")

    def test_nothing_invents_a_meditation_centre(self):
        """
        The sidebar's first entry was "About Meditation Center", describing
        centres "dedicated to preserving the spiritual heritage of ancient
        sites". Project Tirtha does not operate meditation centres. It came from
        the template this UI was built from.
        """
        text = self._locale_text()
        self.assertNotIn("Meditation Center", text)
        self.assertNotIn("meditation centers", text)


class TheDocumentsAreNotDuplicated(SimpleTestCase):
    def test_one_copy_of_each_on_disk(self):
        # The wrapper renders them by name; it does not contain them.
        wrapper = (TEMPLATES / "document.html").read_text()
        self.assertIn("{{ document_body }}", wrapper)
        self.assertNotIn("Bhubaneswar", wrapper)

    def test_the_legacy_page_still_serves_the_same_file(self):
        """
        Both pages include the same template, so the two cannot drift.

        The legacy site pulls these into its side panel; the new URLs wrap the
        same files. Two `include`s of one template is the point -- two copies of
        the text would be the problem this is fixing.
        """
        side = (TEMPLATES / "side.html").read_text()
        for name in ("terms", "privacy"):
            with self.subTest(document=name):
                self.assertTrue(
                    re.search(rf"include\s+[\"\']tirtha/{name}\.html[\"\']", side),
                    f"the legacy page no longer includes the canonical {name} template",
                )
