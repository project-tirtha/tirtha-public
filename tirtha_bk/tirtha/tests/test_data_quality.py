"""
Tests for the one-off production corrections in 0019 (internal design notes).

These run at cutover against rows nobody will re-inspect first, so what is
tested is mostly the guards: that a fix declines to act when the data has
drifted from what was audited, and that the deletion cannot take anything real
with it.
"""

import importlib

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from tirtha.models import Contribution, Contributor, Image, Site, SiteSlugHistory

# The module name starts with a digit, so it cannot be imported with `from`.
_0019 = importlib.import_module("tirtha.migrations.0019_data_quality_fixes")


class _Apps:
    """Stands in for the migration's historical model registry."""

    @staticmethod
    def get_model(app_label, model_name):
        return {
            "Site": Site,
            "Image": Image,
            "SiteSlugHistory": SiteSlugHistory,
        }[model_name]


class KolhapurStateTests(TestCase):
    def test_the_district_in_the_state_field_is_corrected(self):
        site = Site.objects.create(
            name="Kopeshwar Temple",
            country="India",
            state="Kolhapur",
            district="Kolhapur",
        )
        _0019.fix_kopeshwar_state(_Apps, None)
        site.refresh_from_db()
        self.assertEqual(site.state, "Maharashtra")

    def test_the_published_url_keeps_working(self):
        # verbose_id is built from state, so correcting it changes the slug.
        # Without the history row the published URL 404s (internal design notes).
        site = Site.objects.create(
            name="Kopeshwar Temple",
            country="India",
            state="Kolhapur",
            district="Kolhapur",
        )
        old_slug = site.verbose_id
        _0019.fix_kopeshwar_state(_Apps, None)
        site.refresh_from_db()
        self.assertNotEqual(site.verbose_id, old_slug)
        self.assertTrue(SiteSlugHistory.objects.filter(slug=old_slug).exists())

    def test_a_site_already_correct_is_left_alone(self):
        site = Site.objects.create(
            name="Kopeshwar Temple",
            country="India",
            state="Maharashtra",
            district="Kolhapur",
        )
        before = site.verbose_id
        _0019.fix_kopeshwar_state(_Apps, None)
        site.refresh_from_db()
        self.assertEqual(site.verbose_id, before)
        self.assertEqual(SiteSlugHistory.objects.count(), 0)


class TypoDuplicateTests(TestCase):
    def _dupe(self):
        return Site.objects.create(
            name="Victorial Memorial Statue",
            country="India",
            state="West Bengal",
            district="Kolkata",
        )

    def test_an_empty_duplicate_is_removed(self):
        self._dupe()
        _0019.remove_typo_duplicate(_Apps, None)
        self.assertFalse(Site.objects.filter(name="Victorial Memorial Statue").exists())

    def test_the_correctly_spelled_site_is_untouched(self):
        keep = Site.objects.create(
            name="Victoria Memorial",
            country="India",
            state="West Bengal",
            district="Kolkata",
        )
        self._dupe()
        _0019.remove_typo_duplicate(_Apps, None)
        self.assertTrue(Site.objects.filter(pk=keep.pk).exists())

    def test_a_duplicate_that_has_acquired_content_is_kept(self):
        # The guard that matters: between the audit and cutover someone may
        # have uploaded to the row. Deleting would cascade the contribution and
        # its images away.
        dupe = self._dupe()
        contributor = Contributor.objects.create(name="T", email="t@example.org")
        contribution = Contribution.objects.create(site=dupe, contributor=contributor)
        Image.objects.create(
            contribution=contribution,
            image=SimpleUploadedFile("a.jpg", b"x", content_type="image/jpeg"),
        )
        _0019.remove_typo_duplicate(_Apps, None)
        self.assertTrue(Site.objects.filter(pk=dupe.pk).exists())

    def test_running_twice_is_harmless(self):
        # Migrations get replayed against restored snapshots more than once.
        self._dupe()
        _0019.remove_typo_duplicate(_Apps, None)
        _0019.remove_typo_duplicate(_Apps, None)
        self.assertEqual(Site.objects.filter(name__startswith="Victorial").count(), 0)


class CreatingASiteWithAThumbnail(TestCase):
    """
    `Site.objects.create(thumbnail=...)` has to work in one statement.

    `Site.save()` called `super().save(*args, **kwargs)` a second time after
    resizing the thumbnail. `create()` passes `force_insert=True`, so that
    second call issued a second INSERT and raised
    `UNIQUE constraint failed: tirtha_site.verbose_id`.

    The resize rewrites bytes at `thumbnail.path` and changes no column, so
    there was never anything to persist. Two-step creation happened to work,
    which is why the admin never hit it.
    """

    @staticmethod
    def _png():
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (500, 400), (90, 80, 70)).save(buffer, format="PNG")
        return SimpleUploadedFile(
            "t_thumb.png", buffer.getvalue(), content_type="image/png"
        )

    def test_one_statement_is_enough(self):
        site = Site.objects.create(
            name="Single Statement Temple", thumbnail=self._png()
        )
        self.assertTrue(Site.objects.filter(ID=site.ID).exists())

    def test_it_inserts_exactly_one_row(self):
        Site.objects.create(name="One Row Temple", thumbnail=self._png())
        self.assertEqual(Site.objects.filter(name="One Row Temple").count(), 1)

    def test_the_thumbnail_is_still_resized_and_its_derivatives_written(self):
        # Removing the second save must not remove the work before it.
        from django.core.files.storage import default_storage

        from tirtha.models import derivative_path

        site = Site.objects.create(name="Derivative Temple", thumbnail=self._png())
        for kind in ("preview", "card"):
            self.assertTrue(
                default_storage.exists(derivative_path(site.thumbnail.name, kind)),
                f"the {kind} derivative was not written",
            )


class SearchDoesNotCollapseSameNamedSites(TestCase):
    """
    Two sites share a name, and `/search/` returned one of them.

    `meshes_json` was keyed by `site.name`. The archive holds two sites called
    exactly "Gopinath Temple" -- one in Puri, one in Khordha -- so every search
    matching both silently dropped one. Keyed by `verbose_id`, which is unique
    by construction, with the name carried as a field for the title.
    """

    def setUp(self):
        Site.objects.create(name="Gopinath Temple", district="Puri", state="Odisha")
        Site.objects.create(name="Gopinath Temple", district="Khordha", state="Odisha")

    def test_both_are_returned(self):
        payload = self.client.get(reverse("search"), {"query": "Gopinath"}).json()
        self.assertEqual(len(payload["meshes_json"]), 2)

    def test_a_site_without_a_thumbnail_does_not_500_the_search(self):
        # `site.thumbnail.url` raises when the field is empty, and the loop had
        # no guard, so one such row broke every search that matched it.
        response = self.client.get(reverse("search"), {"query": "Gopinath"})
        self.assertEqual(response.status_code, 200)

    def test_each_carries_its_own_name_for_the_title(self):
        payload = self.client.get(reverse("search"), {"query": "Gopinath"}).json()
        for entry in payload["meshes_json"].values():
            self.assertEqual(entry["name"], "Gopinath Temple")
