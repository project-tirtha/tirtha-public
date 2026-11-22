"""
Tests for identifier-based routing and slug history (internal design notes).

`verbose_id` is derived from four mutable fields, so it changes whenever a site
is renamed -- and Phase 3 renames 39 of 60. Routing on it would turn every such
edit into a dead URL. These tests pin the contract that makes renaming safe.
"""

from django.test import TestCase
from django.urls import reverse

from tirtha.models import Site, SiteSlugHistory


def _site(**kw):
    d = {
        "name": "Mukteswar Temple",
        "country": "India",
        "state": "Odisha",
        "district": "Khordha",
    }
    d.update(kw)
    return Site.objects.create(**d)


class SlugRetirementTests(TestCase):
    def test_renaming_records_the_old_slug(self):
        site = _site()
        old = site.verbose_id
        site.name = "Mukteswar"
        site.save()
        self.assertNotEqual(site.verbose_id, old)
        self.assertTrue(SiteSlugHistory.objects.filter(slug=old, site=site).exists())

    def test_repeated_renames_all_remain_resolvable(self):
        site = _site()
        slugs = [site.verbose_id]
        for name in ("Mukteswar", "Mukteswara", "Mukteshwar"):
            site.name = name
            site.save()
            slugs.append(site.verbose_id)
        retired = set(SiteSlugHistory.objects.values_list("slug", flat=True))
        # every slug but the current one is retired and still points here
        self.assertEqual(retired, set(slugs[:-1]))

    def test_saving_without_a_rename_records_nothing(self):
        site = _site()
        site.description = "changed"
        site.save()
        self.assertEqual(SiteSlugHistory.objects.count(), 0)

    def test_district_correction_also_retires_the_slug(self):
        # verbose_id is built from location too, so an administrative fix
        # changes the URL just as a rename does.
        site = _site()
        old = site.verbose_id
        site.district = "Puri"
        site.save()
        self.assertTrue(SiteSlugHistory.objects.filter(slug=old).exists())

    def test_history_is_removed_with_the_site(self):
        site = _site()
        site.name = "Renamed"
        site.save()
        site.delete()
        self.assertEqual(SiteSlugHistory.objects.count(), 0)


class RoutingTests(TestCase):
    def test_canonical_url_is_keyed_on_the_immutable_id(self):
        site = _site()
        url = reverse("siteSlug", args=[site.ID, site.verbose_id])
        self.assertIn(str(site.ID), url)

    def test_canonical_url_survives_a_rename(self):
        site = _site()
        url = reverse("site", args=[site.ID])
        site.name = "Something Else"
        site.save()
        self.assertEqual(url, reverse("site", args=[site.ID]))

    def test_legacy_slug_url_redirects_permanently(self):
        site = _site()
        r = self.client.get(reverse("legacySite", args=[site.verbose_id]))
        self.assertEqual(r.status_code, 301)
        self.assertIn(str(site.ID), r["Location"])

    def test_retired_slug_still_resolves(self):
        site = _site()
        old = site.verbose_id
        site.name = "Mukteswar"
        site.save()
        r = self.client.get(reverse("legacySite", args=[old]))
        self.assertEqual(r.status_code, 301)
        self.assertIn(str(site.ID), r["Location"])

    def test_unknown_slug_is_not_found(self):
        r = self.client.get(
            reverse("legacySite", args=["India__Nowhere__Nowhere__Nothing"])
        )
        self.assertEqual(r.status_code, 404)
