"""
Tests for the JSON API and the React shell (internal design notes).

Two things dominate. **Nothing hidden may leak** -- production hides test rows
and unpublished work behind that flag, so an endpoint that forgets it publishes
them. And **no contributor email may ever appear**, at any endpoint, in any
shape.

The shell's tests are really about the meta tags: they are the entire reason
Django serves the HTML instead of a static file, because unfurl crawlers do not
run JavaScript.
"""

import json

from django.test import TestCase, override_settings
from django.urls import reverse

from tirtha.models import Category, Contribution, Contributor, Run, Site


class _Base(TestCase):
    def setUp(self):
        self.category = Category.objects.create(slug="temple", name="Temple")
        self.site = Site.objects.create(
            name="Mukteswar Temple",
            country="India",
            state="Odisha",
            district="Khordha",
            description="A 10th-century temple.",
            latitude=20.2451,
            longitude=85.8383,
            location_source="osm",
        )
        self.site.categories.add(self.category)
        self.hidden_site = Site.objects.create(
            name="TestMesh",
            country="India",
            state="Odisha",
            district="Khordha",
            hidden=True,
        )
        self.contributor = Contributor.objects.create(
            name="Real Name", email="secret@example.org"
        )
        self.contribution = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )
        self.run = Run.objects.create(site=self.site, kind="GS")
        self.run.contributors.add(self.contributor)
        self.hidden_run = Run.objects.create(site=self.site, kind="GS", hidden=True)

    def get(self, name, **kwargs):
        response = self.client.get(reverse(name, kwargs=kwargs))
        payload = None
        if response["Content-Type"].startswith("application/json"):
            payload = json.loads(response.content)
        return response, payload


class SiteListTests(_Base):
    def test_sites_are_listed(self):
        _, data = self.get("api-sites")
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["name"], "Mukteswar Temple")

    def test_hidden_sites_never_appear(self):
        _, data = self.get("api-sites")
        names = [s["name"] for s in data["results"]]
        self.assertNotIn("TestMesh", names)

    def test_the_response_is_versioned(self):
        _, data = self.get("api-sites")
        self.assertEqual(data["version"], "v1")

    def test_coordinates_carry_their_provenance(self):
        # OSM-derived values carry ODbL obligations that self-collected ones do
        # not, so the source travels with the coordinate (internal design notes).
        _, data = self.get("api-sites")
        self.assertEqual(data["results"][0]["location"]["source"], "osm")

    def test_a_site_without_coordinates_reports_null_location(self):
        Site.objects.create(name="Unlocated", country="India", state="X", district="Y")
        _, data = self.get("api-sites")
        unlocated = [s for s in data["results"] if s["name"] == "Unlocated"][0]
        self.assertIsNone(unlocated["location"])

    def test_filtering_to_located_sites_excludes_them(self):
        # What the map needs: a site with no coordinate cannot be drawn.
        Site.objects.create(name="Unlocated", country="India", state="X", district="Y")
        response = self.client.get(reverse("api-sites"), {"located": "1"})
        data = json.loads(response.content)
        self.assertEqual(data["count"], 1)

    def test_filtering_by_category(self):
        response = self.client.get(reverse("api-sites"), {"category": "temple"})
        self.assertEqual(json.loads(response.content)["count"], 1)
        response = self.client.get(reverse("api-sites"), {"category": "fort"})
        self.assertEqual(json.loads(response.content)["count"], 0)

    def test_search_by_name(self):
        response = self.client.get(reverse("api-sites"), {"q": "mukteswar"})
        self.assertEqual(json.loads(response.content)["count"], 1)

    def test_paging(self):
        for i in range(5):
            Site.objects.create(
                name=f"Site {i}", country="India", state="X", district="Y"
            )
        response = self.client.get(reverse("api-sites"), {"page_size": 2, "page": 2})
        data = json.loads(response.content)
        self.assertEqual(len(data["results"]), 2)
        self.assertEqual(data["count"], 6)

    def test_a_bad_page_parameter_is_a_400_not_a_500(self):
        response = self.client.get(reverse("api-sites"), {"page": "abc"})
        self.assertEqual(response.status_code, 400)

    def test_page_size_is_capped(self):
        response = self.client.get(reverse("api-sites"), {"page_size": "100000"})
        self.assertEqual(json.loads(response.content)["page_size"], 500)


class SiteDetailTests(_Base):
    def test_detail_includes_runs(self):
        _, data = self.get("api-site", site_id=self.site.ID)
        self.assertEqual(len(data["runs"]), 1)
        self.assertEqual(data["runs"][0]["id"], self.run.ID)

    def test_hidden_runs_are_excluded_from_detail(self):
        _, data = self.get("api-site", site_id=self.site.ID)
        ids = [r["id"] for r in data["runs"]]
        self.assertNotIn(self.hidden_run.ID, ids)

    def test_a_hidden_site_is_a_404(self):
        response, payload = self.get("api-site", site_id=self.hidden_site.ID)
        self.assertEqual(response.status_code, 404)
        # JSON, not Django's HTML error page.
        self.assertEqual(payload["error"], "No such site.")

    def test_a_hidden_site_is_indistinguishable_from_a_missing_one(self):
        # Otherwise unpublished sites can be enumerated by watching which IDs
        # 404 differently.
        hidden, _ = self.get("api-site", site_id=self.hidden_site.ID)
        missing, _ = self.get("api-site", site_id="nosuchsiteid00")
        self.assertEqual(hidden.status_code, missing.status_code)
        self.assertEqual(
            json.loads(hidden.content)["error"],
            json.loads(missing.content)["error"],
        )

    def test_an_unknown_site_is_a_404(self):
        response, _ = self.get("api-site", site_id="nosuchsite")
        self.assertEqual(response.status_code, 404)

    def test_external_links_are_null_when_unset(self):
        _, data = self.get("api-site", site_id=self.site.ID)
        self.assertIsNone(data["links"]["wikidata"])

    def test_external_links_are_built_when_set(self):
        self.site.wikidata_qid = "Q1234"
        self.site.osm_type, self.site.osm_id = "way", 42
        self.site.save()
        _, data = self.get("api-site", site_id=self.site.ID)
        self.assertIn("Q1234", data["links"]["wikidata"])
        self.assertIn("way/42", data["links"]["osm"])


class PrivacyTests(_Base):
    """No contributor email, at any endpoint, in any shape."""

    def test_the_list_endpoint_leaks_no_email(self):
        response, _ = self.get("api-sites")
        self.assertNotIn(b"secret@example.org", response.content)

    def test_the_detail_endpoint_leaks_no_email(self):
        response, _ = self.get("api-site", site_id=self.site.ID)
        self.assertNotIn(b"secret@example.org", response.content)

    def test_a_private_contributor_is_anonymous(self):
        # Contributors consented to contribute, not to be named (§11.2).
        _, data = self.get("api-run", run_id=self.run.ID)
        self.assertEqual(data["contributors"], ["Anonymous contributor"])

    def test_an_opted_in_contributor_is_named(self):
        self.contributor.profile_public = True
        self.contributor.display_name = "wanderer"
        self.contributor.save()
        _, data = self.get("api-run", run_id=self.run.ID)
        self.assertEqual(data["contributors"], ["wanderer"])


class RunDetailTests(_Base):
    def test_a_run_carries_its_viewer_framing(self):
        # One camera model for both formats since 6.4 -- the viewer-specific
        # rotaX / initCamPos* split is gone (internal design notes).
        _, data = self.get("api-run", run_id=self.run.ID)
        self.assertIn("camera", data)
        self.assertIn("rotation", data["camera"])
        self.assertIn("up", data["camera"])

    def test_a_hidden_run_is_a_404(self):
        response, _ = self.get("api-run", run_id=self.hidden_run.ID)
        self.assertEqual(response.status_code, 404)

    def test_the_run_names_its_site(self):
        _, data = self.get("api-run", run_id=self.run.ID)
        self.assertEqual(data["site"]["id"], self.site.ID)


class ConditionalRequestTests(_Base):
    """
    Conditional GETs on the read endpoints (unit 6.1, revisited).

    A returning visitor re-fetches the whole site list on every page load --
    the map needs every site at once, because clustering is client-side
    (internal design notes). Before this, none of the API responses carried a validator
    of any kind, so each of those was a full payload over the wire.
    """

    def _etag(self, name, **kwargs):
        response = self.client.get(reverse(name, kwargs=kwargs))
        self.assertIn("ETag", response.headers)
        return response.headers["ETag"]

    def test_the_list_carries_an_etag(self):
        self._etag("api-sites")

    def test_a_revalidation_is_a_304_with_no_body(self):
        etag = self._etag("api-sites")
        response = self.client.get(
            reverse("api-sites"), headers={"if-none-match": etag}
        )
        self.assertEqual(response.status_code, 304)
        self.assertEqual(response.content, b"")

    def test_a_stale_validator_gets_the_whole_payload(self):
        response = self.client.get(
            reverse("api-sites"), headers={"if-none-match": '"stale"'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["count"], 1)

    def test_the_etag_changes_when_a_site_is_added(self):
        before = self._etag("api-sites")
        Site.objects.create(name="New", country="India", state="X", district="Y")
        self.assertNotEqual(before, self._etag("api-sites"))

    def test_minting_an_ark_invalidates_the_list(self):
        # The reason this is an ETag over the body rather than a Last-Modified
        # from `Site.updated_at`: publishing a model flips `map_status` without
        # touching the site row at all. A timestamp validator would keep
        # telling every cached visitor the model is not ready yet.
        from tirtha.models import ARK

        before = self._etag("api-sites")
        self.run.ark = ARK.objects.create(
            ark="99999/aX1",
            naan="99999",
            shoulder="/a",
            assigned_name="X1",
            url="https://example.org/x1.sog",
            metadata={},
        )
        self.run.save()
        self.assertNotEqual(before, self._etag("api-sites"))

    def test_nothing_is_cached_without_revalidating(self):
        # No Cache-Control at all is not the same as "always revalidate": a
        # browser applies heuristic freshness to a response with a validator and
        # no directives, and would serve a stale site list without asking.
        response = self.client.get(reverse("api-sites"))
        self.assertIn("must-revalidate", response.headers["Cache-Control"])
        self.assertIn("max-age=0", response.headers["Cache-Control"])

    def test_the_detail_and_category_endpoints_revalidate_too(self):
        for name, kwargs in (
            ("api-site", {"site_id": self.site.ID}),
            ("api-run", {"run_id": self.run.ID}),
            ("api-categories", {}),
        ):
            with self.subTest(endpoint=name):
                etag = self._etag(name, **kwargs)
                response = self.client.get(
                    reverse(name, kwargs=kwargs), headers={"if-none-match": etag}
                )
                self.assertEqual(response.status_code, 304)

    def test_a_404_carries_no_validator(self):
        # Caching "this does not exist" against a resource that may appear is
        # not worth the confusion it buys.
        response = self.client.get(
            reverse("api-site", kwargs={"site_id": "nosuchsite"})
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("ETag", response.headers)

    def test_the_security_headers_survive_a_304(self):
        # SecurityHeadersMiddleware wraps the view, so a 304 built inside it
        # must still come back through -- the same scoped policy the 200 gets
        # (internal design notes).
        etag = self._etag("api-sites")
        response = self.client.get(
            reverse("api-sites"), headers={"if-none-match": etag}
        )
        self.assertEqual(response.status_code, 304)
        self.assertEqual(
            response.headers["Cross-Origin-Resource-Policy"], "same-origin"
        )


class MapStatusPayloadTests(_Base):
    """
    The two derived fields, as the endpoints present them. The rules
    themselves live in test_map_status.py.
    """

    def test_the_list_carries_the_map_status(self):
        _, data = self.get("api-sites")
        # `self.run` has no ARK, so there is nothing to open yet.
        self.assertEqual(data["results"][0]["map_status"], "in_progress")
        self.assertEqual(data["results"][0]["model_count"], 0)

    def test_the_detail_carries_it_as_well(self):
        _, data = self.get("api-site", site_id=self.site.ID)
        self.assertEqual(data["map_status"], "in_progress")
        self.assertEqual(data["model_count"], 0)

    def test_it_is_not_site_completed(self):
        # `completed` means the site stopped accepting images. Both fields are
        # published, and they are not the same fact.
        self.site.completed = True
        self.site.save()
        _, data = self.get("api-sites")
        self.assertTrue(data["results"][0]["completed"])
        self.assertEqual(data["results"][0]["map_status"], "in_progress")


class MethodTests(_Base):
    def test_the_api_is_read_only(self):
        response = self.client.post(reverse("api-sites"))
        self.assertEqual(response.status_code, 405)


@override_settings(REACT_SHELL_ENABLED=True)
class ShellTests(_Base):
    """
    The meta tags are the feature. Crawlers do not run JavaScript, so anything
    React would fill in later is invisible to them (internal design notes).
    """

    def test_the_root_shell_renders_defaults(self):
        response = self.client.get(reverse("shell"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'property="og:title"')
        self.assertContains(response, "Project Tirtha")

    def test_a_site_route_renders_that_site_s_tags(self):
        response = self.client.get(
            reverse("shell-site", kwargs={"site_id": self.site.ID})
        )
        self.assertContains(response, "Mukteswar Temple")
        self.assertContains(response, "10th-century temple")

    def test_a_site_without_a_description_still_gets_one(self):
        bare = Site.objects.create(
            name="Bare", country="India", state="Odisha", district="Puri"
        )
        response = self.client.get(reverse("shell-site", kwargs={"site_id": bare.ID}))
        self.assertContains(response, "Puri")

    def test_a_run_route_names_the_reconstruction_kind(self):
        response = self.client.get(
            reverse(
                "shell-site-run",
                kwargs={
                    "site_id": self.site.ID,
                    "slug": self.site.verbose_id,
                    "runid": self.run.ID,
                },
            )
        )
        self.assertContains(response, "Gaussian splat")

    def test_a_hidden_site_falls_back_to_defaults_rather_than_leaking(self):
        response = self.client.get(
            reverse("shell-site", kwargs={"site_id": self.hidden_site.ID})
        )
        self.assertNotContains(response, "TestMesh")

    def test_an_unknown_route_still_renders_the_shell(self):
        # React owns routing and knows paths Django does not; 404-ing here
        # would break client-side routes that are perfectly valid.
        response = self.client.get(
            reverse("shell-site", kwargs={"site_id": "nosuchsite"})
        )
        self.assertEqual(response.status_code, 200)

    def test_a_canonical_url_is_always_emitted(self):
        response = self.client.get(reverse("shell"))
        self.assertContains(response, 'rel="canonical"')

    def test_the_shell_publishes_the_url_prefix_for_the_bundle(self):
        # The bundle reads PRE_URL from here rather than hardcoding it, so beta
        # and production can run the same build (internal design notes).
        response = self.client.get(reverse("shell"))
        self.assertContains(response, 'name="tirtha-prefix"')

    def test_a_missing_build_says_so_rather_than_rendering_blank(self):
        with override_settings(VITE_MANIFEST_PATH="/nonexistent/manifest.json"):
            response = self.client.get(reverse("shell"))
        self.assertContains(response, "data-tirtha-no-build")

    def test_a_present_build_is_linked_and_says_nothing_about_building(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps({"index.html": {"file": "assets/main-xyz.js"}}))
            collected = Path(tmp) / "collected"
            (collected / "assets").mkdir(parents=True)
            (collected / "assets" / "main-xyz.js").write_text("//")
            with override_settings(
                VITE_MANIFEST_PATH=str(path), STATIC_ROOT=str(collected)
            ):
                response = self.client.get(reverse("shell"))
        self.assertContains(response, "assets/main-xyz.js")
        self.assertNotContains(response, "data-tirtha-no-build")

    def test_a_manifest_naming_an_uncollected_file_renders_the_no_build_notice(self):
        """
        The white-screen case, made legible.

        `collectstatic` copies `frontend/dist` into `STATIC_ROOT`, but the
        manifest is read from `dist` directly. Rebuild without collecting -- or
        build for a different deployment's asset base -- and the shell emits a
        correct-looking URL for a hash that is not on disk. The browser 404s the
        only script on the page and shows a blank white screen, with a 200
        status and nothing in the server log. This happened twice before the
        guard existed.
        """
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(
                json.dumps({"index.html": {"file": "assets/never-built.js"}})
            )
            empty = Path(tmp) / "collected"
            empty.mkdir()
            with override_settings(
                VITE_MANIFEST_PATH=str(path), STATIC_ROOT=str(empty)
            ):
                response = self.client.get(reverse("shell"))
        self.assertNotContains(response, "assets/never-built.js")
        self.assertContains(response, "data-tirtha-no-build")


class ViteManifestTests(TestCase):
    def test_a_missing_manifest_is_not_fatal(self):
        from tirtha.vite import entry_assets

        with override_settings(VITE_MANIFEST_PATH="/nonexistent/manifest.json"):
            self.assertEqual(entry_assets(), {"js": [], "css": [], "preload": []})

    def test_a_doubled_slash_in_static_url_is_normalised(self):
        # STATIC_URL is built as "/" + PRE_URL + "/static/" and PRE_URL already
        # ends in a slash, so the configured value genuinely contains "//".
        # Merely ugly behind nginx; a 404 on a stricter static server.
        from tirtha.vite import _normalise_url

        self.assertEqual(
            _normalise_url("/project/tirtha//static/"), "/project/tirtha/static"
        )

    def test_absolute_static_urls_survive_normalisation(self):
        # A CDN STATIC_URL must not have its scheme mangled.
        from tirtha.vite import _normalise_url

        self.assertEqual(
            _normalise_url("https://cdn.example.org//static/"),
            "https://cdn.example.org/static",
        )

    def test_assets_are_read_from_the_manifest(self):
        import tempfile
        from pathlib import Path

        from tirtha.vite import entry_assets

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "index.html": {
                            "file": "assets/main-abc123.js",
                            "css": ["assets/main-abc123.css"],
                            "imports": ["_shared.js"],
                        },
                        "_shared.js": {
                            "file": "assets/shared-def.js",
                            "css": ["assets/shared-def.css"],
                        },
                    }
                )
            )
            # The named file has to exist under STATIC_ROOT: `entry_assets`
            # refuses to emit a script tag for an asset that was never
            # collected, because a 404 on the only <script> is a white screen.
            collected = Path(tmp) / "collected"
            (collected / "assets").mkdir(parents=True)
            (collected / "assets" / "main-abc123.js").write_text("//")
            with override_settings(
                VITE_MANIFEST_PATH=str(path), STATIC_ROOT=str(collected)
            ):
                assets = entry_assets()
        self.assertTrue(assets["js"][0].endswith("assets/main-abc123.js"))
        self.assertNotIn("//", assets["js"][0].split("://")[-1])
        # Chunk CSS must be included or the page renders unstyled for a beat.
        self.assertEqual(len(assets["css"]), 2)

    def _preload_manifest(self, tmp):
        """A manifest shaped like the real build: an entry over nested chunks."""
        from pathlib import Path

        path = Path(tmp) / "manifest.json"
        path.write_text(
            json.dumps(
                {
                    "index.html": {
                        "file": "assets/index-abc.js",
                        "imports": ["_react.js", "_maplibre.js"],
                    },
                    # Nested: react's own import is as eagerly loaded as react.
                    "_react.js": {
                        "file": "assets/react-def.js",
                        "imports": ["_runtime.js"],
                    },
                    "_maplibre.js": {"file": "assets/maplibre-ghi.js"},
                    "_runtime.js": {"file": "assets/runtime-jkl.js"},
                }
            )
        )
        return path

    def test_the_chunks_the_entry_imports_are_preloaded(self):
        """
        Without these the browser discovers 78% of the eager bundle only after
        it has fetched and parsed the entry -- an extra serialized round trip
        before maplibre, react and i18n start arriving. Vite's own index.html
        emits them; the Django shell has to as well.
        """
        import tempfile
        from pathlib import Path

        from tirtha.vite import entry_assets

        with tempfile.TemporaryDirectory() as tmp:
            path = self._preload_manifest(tmp)
            collected = Path(tmp) / "collected"
            (collected / "assets").mkdir(parents=True)
            for name in ("index-abc", "react-def", "maplibre-ghi", "runtime-jkl"):
                (collected / "assets" / f"{name}.js").write_text("//")
            with override_settings(
                VITE_MANIFEST_PATH=str(path), STATIC_ROOT=str(collected)
            ):
                assets = entry_assets()
        self.assertEqual(
            [href.split("/")[-1] for href in assets["preload"]],
            ["react-def.js", "runtime-jkl.js", "maplibre-ghi.js"],
        )

    def test_an_uncollected_chunk_is_not_preloaded(self):
        # The guard that refuses to name an uncollected entry applies here too:
        # a preload for a hash that is not on disk buys a console error.
        import tempfile
        from pathlib import Path

        from tirtha.vite import entry_assets

        with tempfile.TemporaryDirectory() as tmp:
            path = self._preload_manifest(tmp)
            collected = Path(tmp) / "collected"
            (collected / "assets").mkdir(parents=True)
            for name in ("index-abc", "react-def", "runtime-jkl"):
                (collected / "assets" / f"{name}.js").write_text("//")
            with override_settings(
                VITE_MANIFEST_PATH=str(path), STATIC_ROOT=str(collected)
            ):
                assets = entry_assets()
        self.assertNotIn(
            "maplibre-ghi.js", [href.split("/")[-1] for href in assets["preload"]]
        )

    def test_the_shell_renders_the_preloads_as_link_tags(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = self._preload_manifest(tmp)
            collected = Path(tmp) / "collected"
            (collected / "assets").mkdir(parents=True)
            for name in ("index-abc", "react-def", "maplibre-ghi", "runtime-jkl"):
                (collected / "assets" / f"{name}.js").write_text("//")
            with override_settings(
                VITE_MANIFEST_PATH=str(path), STATIC_ROOT=str(collected)
            ):
                response = self.client.get(reverse("shell"))
        self.assertContains(
            response, '<link rel="modulepreload" href="/project/tirtha/static/'
        )
        self.assertContains(response, "assets/maplibre-ghi.js")


class ExternalLinkTests(_Base):
    """
    OSM and Wikidata links on the site page and in admin (internal design notes).

    Verifying a seeded coordinate means looking at the thing it was matched to,
    so the link has to be one click, not an ID to paste into a URL by hand.
    """

    def test_the_admin_renders_a_clickable_osm_link(self):
        from django.contrib.admin.sites import site as admin_site

        self.site.osm_type, self.site.osm_id, self.site.osm_version = "way", 42, 7
        self.site.save()
        html = admin_site._registry[Site].external_links(self.site)
        self.assertIn("openstreetmap.org/way/42", html)
        # The version matters: drift detection compares against it (§4).
        self.assertIn("v7", html)

    def test_the_admin_renders_a_clickable_wikidata_link(self):
        from django.contrib.admin.sites import site as admin_site

        self.site.wikidata_qid = "Q1234"
        self.site.save()
        html = admin_site._registry[Site].external_links(self.site)
        self.assertIn("wikidata.org/wiki/Q1234", html)

    def test_a_site_with_no_references_renders_a_placeholder(self):
        from django.contrib.admin.sites import site as admin_site

        self.assertEqual(admin_site._registry[Site].external_links(self.site), "--")
