"""
Tests for embeds and oEmbed (internal design notes).

Two things carry the weight.

**The headers.** The site sets `X-Frame-Options: DENY` and
`Cross-Origin-Resource-Policy: same-origin` globally, both correct everywhere
except here -- an embed served under them renders an empty viewer inside
someone else's page. The relaxation must be scoped to the embed paths, not
applied site-wide.

**Attribution.** Models are CC BY-NC-SA 4.0, and an embed is exactly where
attribution gets stripped, because the surrounding page belongs to someone
else. Credit and licence render *inside* the iframe.
"""

import json
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from django.test import TestCase, override_settings
from django.urls import reverse

from tirtha.models import ARK, Contributor, Run, Site


class _Base(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="Konark Sun Temple",
            country="India",
            state="Odisha",
            district="Puri",
        )
        self.contributor = Contributor.objects.create(
            name="Real Name",
            email="secret@example.org",
            profile_public=True,
            display_name="wanderer",
        )
        self.ark = ARK.objects.create(
            ark="74218/t1abcdef",
            naan="74218",
            shoulder="/t1",
            assigned_name="abcdef",
            url="https://example.org/models/scene.sog",
            metadata={},
            commitment="Permanent, unambiguous identifier.",
        )
        self.run = Run.objects.create(site=self.site, kind="GS", ark=self.ark)
        self.run.contributors.add(self.contributor)
        self.hidden_site = Site.objects.create(
            name="Hidden", country="India", state="X", district="Y", hidden=True
        )


class HeaderTests(_Base):
    def test_the_embed_is_framable(self):
        # X-Frame-Options: DENY is right everywhere except here.
        response = self.client.get(
            reverse(
                "embed-run", kwargs={"site_id": self.site.ID, "run_id": self.run.ID}
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("X-Frame-Options", response.headers)

    def test_model_assets_are_loadable_cross_origin_from_the_embed(self):
        response = self.client.get(
            reverse("embed-site", kwargs={"site_id": self.site.ID})
        )
        self.assertEqual(
            response.headers["Cross-Origin-Resource-Policy"], "cross-origin"
        )

    def test_the_opener_policy_is_dropped_on_the_embed(self):
        # An embedded document has no opener to isolate, and COOP breaks it.
        response = self.client.get(
            reverse("embed-site", kwargs={"site_id": self.site.ID})
        )
        self.assertNotIn("Cross-Origin-Opener-Policy", response.headers)

    def test_the_rest_of_the_site_keeps_the_strict_policy(self):
        # The relaxation is a scoped hole, not a site-wide loosening.
        response = self.client.get(reverse("api-sites"))
        self.assertEqual(
            response.headers["Cross-Origin-Resource-Policy"], "same-origin"
        )
        self.assertEqual(response.headers["Cross-Origin-Opener-Policy"], "same-origin")


class AttributionTests(_Base):
    def test_the_licence_renders_inside_the_iframe(self):
        response = self.client.get(
            reverse("embed-site", kwargs={"site_id": self.site.ID})
        )
        self.assertContains(response, "CC BY-NC-SA 4.0")
        self.assertContains(response, "creativecommons.org")

    def test_contributors_are_credited_inside_the_iframe(self):
        response = self.client.get(
            reverse("embed-site", kwargs={"site_id": self.site.ID})
        )
        self.assertContains(response, "wanderer")

    def test_a_private_contributor_is_not_named(self):
        self.contributor.profile_public = False
        self.contributor.save()
        response = self.client.get(
            reverse("embed-site", kwargs={"site_id": self.site.ID})
        )
        self.assertNotContains(response, "Real Name")

    def test_no_email_reaches_the_embed(self):
        response = self.client.get(
            reverse("embed-site", kwargs={"site_id": self.site.ID})
        )
        self.assertNotIn(b"secret@example.org", response.content)

    def test_the_embed_links_back_to_the_canonical_page(self):
        response = self.client.get(
            reverse("embed-site", kwargs={"site_id": self.site.ID})
        )
        self.assertContains(response, f"site/{self.site.ID}")


class ModelUrlTests(_Base):
    """
    The embed hands its viewer the same file the API would.

    It passed `run.ark.url` verbatim. A run published as `.sog` -- which the
    React viewer cannot read -- rendered an empty canvas inside someone else's
    page while the site's own page, resolving through `api._artifact_url`,
    showed the `.splat` beside it.
    """

    BASE = "https://example.org/project/tirtha/static/models/abc/published"

    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        published = Path(self.root) / "models" / "abc" / "published"
        published.mkdir(parents=True)
        (published / "run.sog").write_bytes(b"s" * 10)
        (published / "run.splat").write_bytes(b"s" * 20)
        self.ark.url = f"{self.BASE}/run.sog"
        self.ark.save()

    def _model_url(self):
        response = self.client.get(
            reverse("embed-site", kwargs={"site_id": self.site.ID})
        )
        self.assertEqual(response.status_code, 200)
        return response.context["model_url"]

    @override_settings(VIEWER_ARTIFACT_FORMATS=(".splat", ".ply", ".glb"))
    def test_a_sog_ark_is_offered_its_readable_sibling(self):
        with self.settings(ARTIFACT_ROOT=self.root):
            self.assertEqual(self._model_url(), f"{self.BASE}/run.splat")
        # The ARK itself is never rewritten.
        self.ark.refresh_from_db()
        self.assertTrue(self.ark.url.endswith("run.sog"))

    @override_settings(VIEWER_ARTIFACT_FORMATS=(".splat", ".ply", ".glb"))
    def test_it_agrees_with_the_api(self):
        from tirtha.api import _artifact_url

        with self.settings(ARTIFACT_ROOT=self.root):
            self.assertEqual(self._model_url(), _artifact_url(self.ark.url))


class VisibilityTests(_Base):
    def test_a_hidden_site_cannot_be_embedded(self):
        response = self.client.get(
            reverse("embed-site", kwargs={"site_id": self.hidden_site.ID})
        )
        self.assertEqual(response.status_code, 404)

    def test_a_site_with_no_published_run_is_a_404(self):
        bare = Site.objects.create(
            name="Bare", country="India", state="X", district="Y"
        )
        response = self.client.get(reverse("embed-site", kwargs={"site_id": bare.ID}))
        self.assertEqual(response.status_code, 404)

    def test_a_hidden_run_is_not_served(self):
        self.run.hidden = True
        self.run.save()
        response = self.client.get(
            reverse(
                "embed-run", kwargs={"site_id": self.site.ID, "run_id": self.run.ID}
            )
        )
        self.assertEqual(response.status_code, 404)


class OEmbedTests(_Base):
    def _canonical(self):
        return "http://testserver" + reverse(
            "shell-site", kwargs={"site_id": self.site.ID}
        )

    def _oembed(self, url=None, **params):
        return self.client.get(
            reverse("oembed"), {"url": url or self._canonical(), **params}
        )

    def test_a_canonical_page_url_resolves(self):
        data = json.loads(self._oembed().content)
        self.assertEqual(data["type"], "rich")
        self.assertIn("<iframe", data["html"])

    def test_the_iframe_points_at_the_embed_route(self):
        data = json.loads(self._oembed().content)
        self.assertIn(f"embed/{self.site.ID}", data["html"])

    def test_attribution_travels_in_the_payload(self):
        # A platform that renders only the iframe still shows the credit
        # inside it; one that reads metadata gets it here too.
        data = json.loads(self._oembed().content)
        self.assertEqual(data["author_name"], "wanderer")
        self.assertEqual(data["license"], "CC BY-NC-SA 4.0")

    def test_a_stale_slug_still_resolves(self):
        # Slugs are decorative and may be years old (§2.2); a shared link must
        # not stop working because a site was renamed.
        url = self._canonical() + "an-old-slug/"
        self.assertEqual(self._oembed(url).status_code, 200)

    def test_dimensions_are_honoured(self):
        data = json.loads(self._oembed(maxwidth=640, maxheight=360).content)
        self.assertEqual((data["width"], data["height"]), (640, 360))
        self.assertIn('width="640"', data["html"])

    def test_non_numeric_dimensions_are_a_400(self):
        self.assertEqual(self._oembed(maxwidth="wide").status_code, 400)

    def test_an_unknown_url_is_a_404(self):
        self.assertEqual(self._oembed("https://example.com/nope").status_code, 404)

    def test_a_hidden_site_is_not_resolvable(self):
        url = "http://testserver" + reverse(
            "shell-site", kwargs={"site_id": self.hidden_site.ID}
        )
        self.assertEqual(self._oembed(url).status_code, 404)

    def test_xml_format_is_supported(self):
        response = self._oembed(format="xml")
        self.assertEqual(response["Content-Type"], "text/xml")
        self.assertIn(b"<oembed>", response.content)

    def test_the_page_advertises_its_oembed_endpoint(self):
        # Discovery: platforms look for this link on the canonical page.
        response = self.client.get(
            reverse("shell-site", kwargs={"site_id": self.site.ID})
        )
        self.assertContains(response, "application/json+oembed")
        self.assertContains(response, quote(str(self.site.ID), safe=""))
