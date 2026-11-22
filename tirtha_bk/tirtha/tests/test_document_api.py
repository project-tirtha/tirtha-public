"""
The canonical documents, reachable without leaving the app.

`shell.document` exists because the React sidebar had grown its own paraphrases
of the Terms and the Privacy Policy, and the request form asked people to
consent to those. Showing the documents in a dialog is a second way of reaching
one copy on disk -- these tests are what stop it becoming a second copy.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from tirtha.shell import DOCUMENTS


class TheDocumentEndpoint(TestCase):
    def test_it_serves_every_document_the_pages_serve(self):
        # Driven off `DOCUMENTS` rather than a list written out here, so a
        # fourth document cannot be added to the pages and quietly missed by
        # the dialog.
        for name in DOCUMENTS:
            with self.subTest(name=name):
                response = self.client.get(reverse("api-document", args=[name]))
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["id"], name)
                self.assertTrue(payload["title"])
                self.assertTrue(payload["html"])

    def test_the_body_is_the_same_markup_the_page_renders(self):
        """
        The point of the whole endpoint.

        Not a paraphrase, not a summary, not a second file: the same template,
        through the same `_opened` helper, so the dialog and the page cannot
        drift apart.
        """
        from tirtha.shell import _opened

        response = self.client.get(reverse("api-document", args=["terms"]))
        expected = str(_opened(DOCUMENTS["terms"][0], response.wsgi_request))
        self.assertEqual(response.json()["html"], expected)

    def test_it_unfolds_the_details_block(self):
        # The templates are authored as a closed `<details>` for the legacy
        # accordion. Served as-is, the dialog would show a heading and nothing
        # else -- and a test that only grepped for the text would pass, because
        # the text is in the markup either way.
        html = self.client.get(reverse("api-document", args=["terms"])).json()["html"]
        self.assertIn("<details open", html)

    def test_an_unknown_document_is_a_404_not_a_500(self):
        response = self.client.get(reverse("api-document", args=["not-a-document"]))
        self.assertEqual(response.status_code, 404)
