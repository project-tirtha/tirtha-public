"""
Site descriptions are markup, and the API is what makes them safe to render.

27 of the 59 curated descriptions contain real HTML -- paragraphs, ordered
lists, and links out to Wikipedia and Google Maps. React escapes by default, so
a visitor read `</p> <ol> <li><a href="...">` as literal text on the page.

The sanitising belongs here rather than in the client. This API has more than
one consumer -- the React app, the embed route, oEmbed, anything written against
it later -- and "every client remembers to sanitise" is a rule that holds until
the first one forgets. A description is operator-curated rather than
visitor-submitted, which lowers the odds of anything hostile without changing
where the guarantee belongs.
"""

from django.test import TestCase

from tirtha.api import _description_html, serialize_site
from tirtha.models import Site


class Sanitising(TestCase):
    def test_ordinary_markup_survives(self):
        html = _description_html("<p>A temple.</p><ol><li>One</li></ol>")
        self.assertIn("<p>A temple.</p>", html)
        self.assertIn("<li>One</li>", html)

    def test_a_script_tag_loses_its_tag_and_keeps_its_text(self):
        """
        `strip=True`, so a disallowed tag is removed and its text stays. The
        alternative escapes the tag into something a reader sees.
        """
        html = _description_html("<p>ok</p><script>alert(1)</script>")
        self.assertNotIn("<script", html)
        self.assertIn("<p>ok</p>", html)

    def test_event_handlers_are_removed(self):
        html = _description_html('<p onclick="evil()">text</p>')
        self.assertNotIn("onclick", html)
        self.assertIn("text", html)

    def test_a_javascript_url_is_not_a_link(self):
        html = _description_html('<a href="javascript:alert(1)">click</a>')
        self.assertNotIn("javascript:", html)

    def test_outbound_links_cannot_reach_back_through_the_opener(self):
        """
        These point off-site by definition. `noopener` is what stops the
        destination reaching `window.opener` and navigating this tab.
        """
        html = _description_html('<a href="https://en.wikipedia.org/wiki/X">X</a>')
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertIn('target="_blank"', html)

    def test_an_image_is_not_allowed(self):
        # An <img> in a description is a request to a third party on page load,
        # which is a tracking pixel wearing a photograph.
        html = _description_html('<p>a</p><img src="https://x.test/p.gif">')
        self.assertNotIn("<img", html)

    def test_empty_and_plain_text_are_unharmed(self):
        self.assertEqual(_description_html(""), "")
        self.assertEqual(_description_html(None), "")
        self.assertIn("Just words.", _description_html("Just words."))


class TheApiPublishesBoth(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="Kopeshwar Temple",
            country="India",
            state="Maharashtra",
            district="Kolhapur",
            latitude=16.6180,
            longitude=74.5960,
            description='<p>A temple.</p><ol><li><a href="https://en.wikipedia.org/wiki/Kopeshwar_Temple">Wikipedia</a></li></ol>',
        )

    def test_both_fields_are_present(self):
        data = self.client.get(f"/project/tirtha/api/v1/sites/{self.site.ID}/").json()
        self.assertIn("description", data)
        self.assertIn("description_html", data)

    def test_the_plain_field_is_left_exactly_as_stored(self):
        """
        A consumer that wants text should not have to un-render HTML to get it,
        and an operator editing in the admin should see what they typed.
        """
        data = self.client.get(f"/project/tirtha/api/v1/sites/{self.site.ID}/").json()
        self.assertEqual(data["description"], self.site.description)

    def test_the_html_field_is_safe_and_linked(self):
        data = self.client.get(f"/project/tirtha/api/v1/sites/{self.site.ID}/").json()
        html = data["description_html"]
        self.assertIn("<ol>", html)
        self.assertIn('rel="noopener noreferrer"', html)

    def test_the_list_endpoint_carries_it_too(self):
        # The map's panel renders from the list payload, which is where the raw
        # markup was showing.
        results = self.client.get("/project/tirtha/api/v1/sites/").json()["results"]
        row = next(r for r in results if r["id"] == self.site.ID)
        self.assertIn("<ol>", row["description_html"])


class TheClientRendersIt(TestCase):
    def test_the_panel_uses_the_sanitised_field(self):
        """
        `dangerouslySetInnerHTML` is only defensible against the field the
        server sanitised. Pinned so nobody points it at `description`.
        """
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[3]
            / "frontend"
            / "src"
            / "components"
            / "Sidebar.jsx"
        ).read_text()
        self.assertIn("descriptionHtml", source)
        self.assertIn("dangerouslySetInnerHTML", source)
        self.assertNotIn(
            "dangerouslySetInnerHTML={{ __html: selectedTemple.description }}", source
        )


class PlaceholderDescriptionTests(TestCase):
    """
    "[To be updated]" is a note to ourselves, not a description.

    13 of 34 sites carried exactly that string, and the API published it as the
    whole of their Description section -- so a visitor reading about a
    thirteenth-century temple was shown a caretaker's placeholder and nothing
    else.

    Masked at the serializer rather than edited out of the database: the note is
    useful to an operator looking at the admin, and the moment somebody writes
    real text it appears again with no code change.
    """

    def setUp(self):
        self.site = Site.objects.create(
            name="Unwritten Temple",
            country="India",
            state="Odisha",
            district="Puri",
            latitude=20.0,
            longitude=85.0,
        )

    def _payload(self):
        return serialize_site(self.site, request=None)

    def test_the_placeholder_is_not_published(self):
        self.site.description = "[To be updated]"
        self.site.save()
        payload = self._payload()
        self.assertEqual(payload["description"], "")
        self.assertEqual(payload["description_html"], "")

    def test_its_spelling_variants_are_caught(self):
        for text in (
            "[To be updated]",
            "To be updated",
            "  [to be updated]  ",
            "[TO BE UPDATED.]",
            "to be  updated",
        ):
            with self.subTest(text=text):
                self.site.description = text
                self.site.save()
                self.assertEqual(self._payload()["description"], "")

    def test_the_models_own_default_is_caught(self):
        """
        The one the pattern missed, and the one nearly every site carries.

        `Site.description` defaults to "Placeholder Text". The mask matched only
        the "[to be updated]" family, so the default went straight through: 34
        of 35 visible sites were publishing the words "Placeholder Text" to
        visitors, in the panel and in the API. A mask that catches every
        placeholder except the common one is not a mask.
        """
        from tirtha.models import Site

        default = Site._meta.get_field("description").default
        for text in (
            default,
            "placeholder text",
            "  Placeholder  ",
            "PLACEHOLDER TEXT",
        ):
            with self.subTest(text=text):
                self.site.description = text
                self.site.save()
                self.assertEqual(self._payload()["description"], "")
                self.assertEqual(self._payload()["description_html"], "")

    def test_a_description_that_merely_mentions_placeholder_text_survives(self):
        # Anchored, like the clause above it: widening the pattern must not
        # start swallowing real prose.
        self.site.description = "The caretaker replaced the placeholder text in 2019."
        self.site.save()
        self.assertIn("caretaker", self._payload()["description"])

    def test_a_real_description_that_mentions_the_phrase_survives(self):
        """
        Anchored, so it masks a description that *is* the note and not one that
        happens to contain it.
        """
        self.site.description = "A 13th-century temple. Dimensions to be updated."
        self.site.save()
        self.assertIn("13th-century", self._payload()["description"])

    def test_a_written_description_appears_with_no_code_change(self):
        self.site.description = "Built in the 13th century by Narasimhadeva I."
        self.site.save()
        self.assertIn("Narasimhadeva", self._payload()["description"])
