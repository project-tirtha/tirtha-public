"""
API fixes from review 2026-11-22 §6/§7.

* `/api/v1/sites/` serves a cached, rendered payload keyed on a cheap version
  stamp, so a hit -- 200 or 304 -- costs one query, and a change the stamp can
  see is visible on the next request.
* Runs carry `model_bytes` and `model_format`.
* `request_site` refuses an oversized body before parsing it.
* `discover` bounds its upstream call by `OVERPASS_TIMEOUT`.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tirtha import api
from tirtha.models import ARK, Run, Site


def _site(name="Konark", **kw):
    return Site.objects.create(
        name=name, country="India", state="Odisha", district="Puri", **kw
    )


class SitesAreCached(TestCase):
    def setUp(self):
        cache.clear()
        self.site = _site()
        self.url = reverse("api-sites")

    def _get(self, **extra):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(self.url, **extra)
        return response, len(ctx)

    def _names(self, response):
        return [s["name"] for s in json.loads(response.content)["results"]]

    def test_a_hit_costs_one_query(self):
        first, miss = self._get()
        second, hit = self._get()
        self.assertEqual(first.content, second.content)
        self.assertEqual(first["ETag"], second["ETag"])
        self.assertEqual(hit, 1, "a cache hit should cost only the version stamp")
        self.assertLess(hit, miss)

    def test_a_revalidation_is_a_cheap_304(self):
        first, _ = self._get()
        response, queries = self._get(HTTP_IF_NONE_MATCH=first["ETag"])
        self.assertEqual(response.status_code, 304)
        self.assertEqual(queries, 1)
        self.assertEqual(response["ETag"], first["ETag"])
        self.assertIn("must-revalidate", response["Cache-Control"])

    def test_a_new_site_is_seen_on_the_next_request(self):
        self._get()
        _site("Lingaraj")
        response, _ = self._get()
        self.assertIn("Lingaraj", self._names(response))

    def test_a_bulk_hide_is_seen_without_waiting_for_the_ttl(self):
        self._get()
        # `update()` does not touch `updated_at`; the hidden count still moves.
        Site.objects.filter(pk=self.site.pk).update(hidden=True)
        response, _ = self._get()
        self.assertNotIn("Konark", self._names(response))

    def test_minting_an_ark_is_seen_on_the_next_request(self):
        run = Run.objects.create(site=self.site, kind="GS", status="Archived")
        status = lambda r: json.loads(r.content)["results"][0]["map_status"]  # noqa: E731
        self.assertEqual(status(self._get()[0]), "in_progress")
        run.ark = ARK.objects.create(
            ark="99999/c1",
            naan="99999",
            shoulder="/",
            assigned_name="c1",
            url="https://example.org/x.splat",
            metadata={},
        )
        run.save()
        self.assertEqual(status(self._get()[0]), "published")

    def test_query_strings_are_cached_apart(self):
        _site("Lingaraj")
        everything, _ = self._get()
        filtered = self.client.get(self.url, {"q": "Ling"})
        self.assertEqual(self._names(filtered), ["Lingaraj"])
        self.assertEqual(len(self._names(everything)), 2)

    def test_bad_paging_is_still_a_400_and_not_cached(self):
        self.assertEqual(self.client.get(self.url, {"page": "x"}).status_code, 400)
        self.assertEqual(self.client.get(self.url, {"page": "x"}).status_code, 400)

    @override_settings(API_SITES_CACHE_SECONDS=0)
    def test_zero_disables_it(self):
        _, first = self._get()
        _, second = self._get()
        self.assertEqual(first, second)
        self.assertGreater(second, 1)

    def test_the_default_is_sixty_seconds(self):
        self.assertEqual(api.DEFAULT_API_SITES_CACHE_SECONDS, 60)


class RunsCarryTheirModelSize(TestCase):
    def setUp(self):
        cache.clear()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.site = _site()
        published = self.root / "models" / str(self.site.ID) / "published"
        published.mkdir(parents=True)
        self.file = published / "run.splat"
        self.file.write_bytes(b"x" * 1234)
        self.run = Run.objects.create(site=self.site, kind="GS", status="Archived")
        self.run.ark = ARK.objects.create(
            ark="99999/m1",
            naan="99999",
            shoulder="/",
            assigned_name="m1",
            url=f"https://example.org/project/tirtha/static/models/{self.site.ID}/published/run.splat",
            metadata={},
        )
        self.run.save()

    def _run(self):
        response = self.client.get(reverse("api-run", args=[self.run.ID]))
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)

    def test_bytes_and_format_of_the_offered_file(self):
        with override_settings(
            ARTIFACT_ROOT=str(self.root), VIEWER_ARTIFACT_FORMATS=(".splat",)
        ):
            data = self._run()
        self.assertEqual(data["model_bytes"], 1234)
        self.assertEqual(data["model_format"], "splat")
        self.assertTrue(data["file_url"].endswith(".splat"))

    def test_the_size_follows_the_file_actually_offered(self):
        (self.file.parent / "run.compressed.ply").write_bytes(b"x" * 100)
        with override_settings(
            ARTIFACT_ROOT=str(self.root),
            VIEWER_ARTIFACT_FORMATS=(".compressed.ply", ".splat"),
        ):
            data = self._run()
        self.assertTrue(data["file_url"].endswith(".compressed.ply"))
        self.assertEqual(
            (data["model_bytes"], data["model_format"]), (100, "compressed.ply")
        )

    def test_unmeasurable_is_null_not_a_guess(self):
        with override_settings(ARTIFACT_ROOT=""):
            data = self._run()
        self.assertIsNone(data["model_bytes"])
        self.assertEqual(data["model_format"], "splat")

    def test_a_run_without_an_ark_has_neither(self):
        run = Run.objects.create(site=self.site, kind="GS", status="Archived")
        data = json.loads(self.client.get(reverse("api-run", args=[run.ID])).content)
        self.assertIsNone(data["model_bytes"])
        self.assertIsNone(data["model_format"])

    def test_the_site_detail_runs_carry_them_too(self):
        with override_settings(
            ARTIFACT_ROOT=str(self.root), VIEWER_ARTIFACT_FORMATS=(".splat",)
        ):
            data = json.loads(
                self.client.get(reverse("api-site", args=[self.site.ID])).content
            )
        (run,) = data["runs"]
        self.assertEqual((run["model_bytes"], run["model_format"]), (1234, "splat"))

    def test_formats(self):
        for url, fmt in [
            ("a/b.glb", "glb"),
            ("a/b.SOG", "sog"),
            ("a/b.compressed.ply", "compressed.ply"),
            ("a/b.ply", "ply"),
            ("a/b.spz", "spz"),
            ("a/b.obj", None),
            (None, None),
        ]:
            self.assertEqual(api._model_format(url), fmt, url)


class SiteRequestBodyIsCapped(TestCase):
    def setUp(self):
        cache.clear()

    def _post(self, body):
        return self.client.post(
            reverse("api-site-request"), data=body, content_type="application/json"
        )

    def test_an_oversized_body_is_413(self):
        body = json.dumps({"name": "x", "description": "y" * (70 * 1024)})
        response = self._post(body)
        self.assertEqual(response.status_code, 413)
        self.assertIn("error", json.loads(response.content))

    @override_settings(SITE_REQUEST_MAX_BODY_BYTES=100)
    def test_the_limit_is_configurable(self):
        self.assertEqual(self._post(json.dumps({"name": "z" * 200})).status_code, 413)

    def test_a_normal_request_still_works(self):
        response = self._post(
            json.dumps(
                {"name": "Mukteswar", "accepted_terms": True, "accepted_privacy": True}
            )
        )
        self.assertEqual(response.status_code, 201)

    def test_the_default_is_64_kb(self):
        self.assertEqual(api.DEFAULT_SITE_REQUEST_MAX_BODY_BYTES, 64 * 1024)


@override_settings(
    OVERPASS_URL="http://127.0.0.1:8003/api/interpreter", DISCOVER_MIN_ZOOM=12
)
class DiscoverIsBoundedByTheSetting(TestCase):
    def setUp(self):
        cache.clear()

    def _timeout_used(self):
        seen = []

        def fake_post(url, data=None, timeout=None, **kw):
            seen.append(timeout)

            class R:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return {"elements": []}

            return R()

        with patch("requests.post", fake_post):
            response = self.client.get(
                reverse("api-discover"), {"bbox": "85.80,20.20,85.89,20.29", "zoom": 14}
            )
        self.assertEqual(response.status_code, 200)
        return seen

    @override_settings(OVERPASS_TIMEOUT=15)
    def test_the_upstream_timeout_is_the_setting(self):
        seen = self._timeout_used()
        self.assertTrue(seen)
        self.assertTrue(all(t is not None and t <= 15 for t in seen), seen)
