"""
The read endpoints cost the same whatever the archive holds.

`site_detail` was `4 + 3R` queries in R visible runs: `serialize_run` reached
for `run.ark`, `run.images.count()` and `run.contributors.all()` once per run,
none of them prefetched. Measured at 22 queries for a six-run site.

Six runs is today's maximum and 18 extra round trips is ~10 ms, so this is not
a fire. It becomes one at unit 5.7 — continual reconstruction, whose entire
purpose is that runs-per-site grows without bound. A site with fifty runs would
have cost 154 queries. Cheaper to fix while R is small than to discover it when
R is not.

What is asserted is the *shape*: doubling the runs must not change the count.
A test that only pinned "22" would have passed the day it was written and told
nobody anything.
"""

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from tirtha.models import ARK, Contribution, Contributor, Image, Run, Site


class _Archive(TestCase):
    def make_site(self, name, runs):
        site = Site.objects.create(
            name=name,
            country="India",
            state="Odisha",
            district="Khordha",
            latitude=20.24,
            longitude=85.85,
            description="<p>A temple.</p>",
        )
        contributor = Contributor.objects.create(
            name=f"{name} person", email=f"{name}@example.org"
        )
        contribution = Contribution.objects.create(site=site, contributor=contributor)
        for index in range(runs):
            run = Run.objects.create(site=site, kind="GS", status="Archived")
            run.contributors.add(contributor)
            run.ark = ARK.objects.create(
                ark=f"99999/{name}{index}",
                naan="99999",
                shoulder="/",
                assigned_name=f"{name}{index}",
                url=f"https://example.org/{name}{index}.splat",
                metadata={},
            )
            run.save()
        for index in range(3):
            Image.objects.create(contribution=contribution, image=f"{name}-{index}.jpg")
        return site

    def count(self, url):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return len(ctx)


class SiteDetailIsConstant(_Archive):
    def test_doubling_the_runs_does_not_change_the_query_count(self):
        few = self.make_site("few", runs=3)
        many = self.make_site("many", runs=12)

        base = "/project/tirtha/api/v1/sites/"
        self.assertEqual(
            self.count(f"{base}{few.ID}/"),
            self.count(f"{base}{many.ID}/"),
            "site_detail is linear in the number of runs again",
        )

    def test_it_is_a_small_constant(self):
        site = self.make_site("one", runs=6)
        self.assertLessEqual(self.count(f"/project/tirtha/api/v1/sites/{site.ID}/"), 8)


class TheListIsConstant(_Archive):
    def test_more_sites_do_not_mean_more_queries(self):
        """
        This one was already right -- `annotate_map_status` plus a
        `prefetch_related` on categories -- and is pinned so it stays right.
        """
        for index in range(3):
            self.make_site(f"a{index}", runs=2)
        with_three = self.count("/project/tirtha/api/v1/sites/")

        for index in range(9):
            self.make_site(f"b{index}", runs=2)
        with_twelve = self.count("/project/tirtha/api/v1/sites/")

        self.assertEqual(with_three, with_twelve)


class SanitisingIsPaidOnce(_Archive):
    # With the payload cache on, the second request never reaches the
    # serializer at all (`test_api_review_2026_11`), which would make this
    # assert nothing about the sanitiser's own memo. Off, to test that.
    @override_settings(API_SITES_CACHE_SECONDS=0)
    def test_the_same_description_is_not_sanitised_twice(self):
        """
        bleach was 66% of the list endpoint's wall time, and it was paid again
        in full on every 304 — the ETag is a hash of the rendered body, so a
        conditional response does all the work and then sends none of it.
        """
        from tirtha.api import _sanitise_description

        _sanitise_description.cache_clear()
        for index in range(4):
            self.make_site(f"c{index}", runs=1)

        self.client.get("/project/tirtha/api/v1/sites/")
        first = _sanitise_description.cache_info()
        self.client.get("/project/tirtha/api/v1/sites/")
        second = _sanitise_description.cache_info()

        self.assertEqual(
            second.misses,
            first.misses,
            "the second request re-sanitised text it had already seen",
        )
        self.assertGreater(second.hits, first.hits)
