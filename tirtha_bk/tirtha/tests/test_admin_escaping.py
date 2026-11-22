"""
The admin's HTML-returning display methods escape what they interpolate.

The admin is the operator UI (~2k LOC) and several of its columns render HTML so
a reviewer can click through to a site, an image or a contributor. Those columns
used `mark_safe` over an f-string, which marks the *result* safe without
escaping anything put into it -- so any interpolated value was injected verbatim.

Three of the interpolated values are not ours to trust:

* `Contributor.name` is the display name from the visitor's own Google account.
* `Site.osm_type` / `osm_id` / `wikidata_qid` are copied from OSM tags by the
  Overpass importer (internal design notes).
* `Run.metrics` is a JSON blob written by the pipeline.

The consequence was stored XSS against an administrator: a contributor sets
their Google display name to markup, uploads, and it executes in the session of
whoever reviews the contribution -- which is a session with delete rights over
every run and ARK on the site.

`format_html` escapes its arguments. These tests assert the escaping rather than
the call, so they still hold if the implementation changes again.
"""

import io

from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image as PILImage

from tirtha.admin import ImageAdmin, SiteAdmin
from tirtha.models import Contribution, Contributor, Image, Site

PAYLOAD = '<img src=x onerror="alert(1)">'


def _png() -> SimpleUploadedFile:
    """`Site.save()` opens the thumbnail with Pillow, so it must be decodable."""
    buffer = io.BytesIO()
    PILImage.new("RGB", (1, 1), "black").save(buffer, format="PNG")
    return SimpleUploadedFile("t.png", buffer.getvalue(), "image/png")


class AdminEscapingTests(TestCase):
    def test_contributor_display_name_is_escaped(self):
        """The one an outsider controls directly, with no review in between."""
        contributor = Contributor.objects.create(
            name=PAYLOAD, email="attacker@example.com"
        )
        site = Site.objects.create(name="Konark", country="India", state="Odisha")
        contribution = Contribution.objects.create(site=site, contributor=contributor)
        image = Image.objects.create(
            contribution=contribution,
            image=SimpleUploadedFile("x.jpg", b"\xff\xd8\xff", "image/jpeg"),
        )

        html = str(ImageAdmin(Image, AdminSite()).get_contributor_link(image))

        self.assertNotIn("<img src=x", html)
        self.assertNotIn('onerror="alert(1)"', html)
        self.assertIn("&lt;img", html)
        # Still a working link, not just a blank cell.
        self.assertIn("<a href=", html)

    def test_osm_tags_are_escaped(self):
        """
        Copied from OSM by the importer; nobody retypes them.

        `osm_type` is `varchar(10)`, so it cannot carry much -- the first
        version of this test put 25 characters in it and passed only because
        SQLite does not enforce `max_length`. PostgreSQL rejects the insert, so
        the test was asserting nothing about a value the column could hold.
        `wikidata_qid` is the field with room for a payload, and it is the one
        that matters: both are copied verbatim from OSM tags.
        """
        site = Site.objects.create(
            name="Konark",
            country="India",
            state="Odisha",
            osm_type='way"<b>',
            osm_id="123",
            wikidata_qid=PAYLOAD,
        )

        html = str(SiteAdmin(Site, AdminSite()).external_links(site))

        self.assertNotIn("<img src=x", html)
        self.assertNotIn("<b>", html)
        self.assertIn("&lt;img", html)
        self.assertIn("&lt;b&gt;", html)

    def test_a_site_with_no_external_ids_renders_a_dash(self):
        """The escaping change must not turn an empty cell into a crash."""
        site = Site.objects.create(name="Konark", country="India", state="Odisha")
        self.assertEqual(SiteAdmin(Site, AdminSite()).external_links(site), "--")

    def test_verbose_id_is_escaped_in_the_image_alt_attribute(self):
        """
        An admin-entered site name reaches an `alt="..."` attribute through
        `verbose_id`, so a bare quote in a name is enough to break out of it.
        """
        site = Site.objects.create(
            name='Konark" onerror="alert(1)', country="India", state="Odisha"
        )
        site.thumbnail = _png()
        site.save()

        html = str(SiteAdmin(Site, AdminSite()).get_thumbnail(site))

        self.assertNotIn('onerror="alert(1)"', html)
        self.assertIn("&quot;", html)
