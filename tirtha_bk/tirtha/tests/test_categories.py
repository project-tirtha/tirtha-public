"""
Tests for site categories (internal design notes).

Many-to-many is the point: a fort can contain a temple, and Dhauli is both a
stupa and a rock edict. A single-valued field would force a false choice on
exactly the sites that are most interesting.
"""

from django.test import TestCase
from django.utils import translation

from tirtha.models import Category, Site


def _site(**kw):
    d = {
        "name": "Mukteswar Temple",
        "country": "India",
        "state": "Odisha",
        "district": "Khordha",
    }
    d.update(kw)
    return Site.objects.create(**d)


class CategoryTests(TestCase):
    def setUp(self):
        self.temple = Category.objects.create(
            slug="temple",
            name="Temple",
            icon="temple",
            osm_tags=["amenity=place_of_worship+religion=hindu", "building=temple"],
        )
        self.fort = Category.objects.create(
            slug="fort", name="Fort", icon="fort", osm_tags=["historic=fort"]
        )
        self.stupa = Category.objects.create(slug="stupa", name="Stupa", icon="stupa")

    def test_a_site_can_hold_several_categories(self):
        site = _site(name="Dhauli Shanti Stupa")
        site.categories.set([self.stupa, self.temple])
        self.assertEqual(site.categories.count(), 2)

    def test_a_category_spans_many_sites(self):
        for n in ("Mukteswar", "Rajarani", "Brahmeswara"):
            _site(name=n).categories.add(self.temple)
        self.assertEqual(self.temple.sites.count(), 3)

    def test_sites_are_filterable_by_category(self):
        a = _site(name="Fort Site")
        a.categories.add(self.fort)
        b = _site(name="Temple Site")
        b.categories.add(self.temple)
        found = Site.objects.filter(categories__slug="fort")
        self.assertEqual(list(found), [a])

    def test_filtering_by_two_categories_finds_sites_with_both(self):
        both = _site(name="Fort With Temple")
        both.categories.set([self.fort, self.temple])
        _site(name="Just A Fort").categories.add(self.fort)
        found = Site.objects.filter(categories=self.fort).filter(categories=self.temple)
        self.assertEqual(list(found), [both])

    def test_categories_are_optional(self):
        self.assertEqual(_site().categories.count(), 0)

    def test_icon_is_carried_for_the_map_glyph(self):
        # Category drives the marker glyph; run status drives the colour.
        self.assertEqual(self.temple.icon, "temple")

    def test_source_tags_are_retained_for_re_derivation(self):
        self.assertIn("historic=fort", self.fort.osm_tags)

    def test_category_names_are_translatable(self):
        self.temple.name_hi = "मंदिर"
        self.temple.save()
        with translation.override("hi"):
            self.temple.refresh_from_db()
            self.assertEqual(self.temple.name, "मंदिर")

    def test_category_slug_is_not_translated(self):
        # The slug is an identifier used in filters and URLs.
        self.assertFalse(hasattr(self.temple, "slug_hi"))


class SiteOsmTagsTests(TestCase):
    def test_raw_tags_default_to_empty(self):
        self.assertEqual(_site().osm_tags, {})

    def test_raw_tags_round_trip(self):
        tags = {"historic": "fort", "wikidata": "Q1665268", "name:or": "ବାରବାଟୀ"}
        site = _site(osm_tags=tags)
        site.refresh_from_db()
        self.assertEqual(site.osm_tags["name:or"], "ବାରବାଟୀ")
