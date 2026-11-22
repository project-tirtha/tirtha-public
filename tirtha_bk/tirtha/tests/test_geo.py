"""
Tests for the geo fields on Site (internal design notes).

The stored coordinate is authoritative for rendering; Overpass is a seeding
tool and an enrichment link, never the runtime source. These pin the shape that
makes reconciliation and licence provenance possible later.
"""

from django.test import TestCase
from django.utils import timezone

from tirtha.models import Site


def _site(**kw):
    d = {
        "name": "Mukteswar Temple",
        "country": "India",
        "state": "Odisha",
        "district": "Khordha",
    }
    d.update(kw)
    return Site.objects.create(**d)


class CoordinateTests(TestCase):
    def test_coordinates_default_to_unset(self):
        # 60 existing sites have no coordinates; null must be representable and
        # distinguishable from 0,0 (which is a real place in the Atlantic).
        site = _site()
        self.assertIsNone(site.latitude)
        self.assertIsNone(site.longitude)

    def test_coordinates_round_trip(self):
        site = _site(latitude=20.2445, longitude=85.8371)
        site.refresh_from_db()
        self.assertAlmostEqual(site.latitude, 20.2445, places=4)
        self.assertAlmostEqual(site.longitude, 85.8371, places=4)

    def test_zero_is_preserved_not_treated_as_missing(self):
        site = _site(latitude=0.0, longitude=0.0)
        site.refresh_from_db()
        self.assertIsNotNone(site.latitude)
        self.assertEqual(site.latitude, 0.0)


class ProvenanceTests(TestCase):
    def test_source_accepts_each_documented_origin(self):
        for source in ("manual", "osm", "wikidata", "exif", "site_request"):
            site = _site(name=f"Site {source}", location_source=source)
            site.refresh_from_db()
            self.assertEqual(site.location_source, source)

    def test_source_is_blank_until_a_coordinate_is_set(self):
        # Blank means "never resolved", which is what a bulk backfill keys on.
        self.assertEqual(_site().location_source, "")

    def test_osm_reference_and_version_travel_together(self):
        # osm_version is what makes drift detectable: resolved at v7, mirror
        # says v9 -> flag for review rather than silently move the marker.
        site = _site(osm_type="way", osm_id=123456789, osm_version=7)
        site.refresh_from_db()
        self.assertEqual(
            (site.osm_type, site.osm_id, site.osm_version), ("way", 123456789, 7)
        )

    def test_osm_id_is_wide_enough_for_real_ids(self):
        # OSM way IDs have passed 2^31; a plain IntegerField would overflow.
        site = _site(osm_type="way", osm_id=12_345_678_901)
        site.refresh_from_db()
        self.assertEqual(site.osm_id, 12_345_678_901)

    def test_wikidata_qid_round_trips(self):
        site = _site(wikidata_qid="Q1665268")
        site.refresh_from_db()
        self.assertEqual(site.wikidata_qid, "Q1665268")

    def test_verification_timestamp_is_optional(self):
        site = _site()
        self.assertIsNone(site.location_verified_at)
        site.location_verified_at = timezone.now()
        site.save()
        site.refresh_from_db()
        self.assertIsNotNone(site.location_verified_at)


class MapFramingTests(TestCase):
    def test_framing_has_usable_defaults(self):
        # Every site must be flyable-to before anyone curates framing.
        site = _site()
        self.assertEqual(
            (site.default_zoom, site.default_bearing, site.default_pitch),
            (16.0, 0.0, 45.0),
        )

    def test_framing_is_overridable(self):
        site = _site(default_zoom=18.5, default_bearing=120.0, default_pitch=60.0)
        site.refresh_from_db()
        self.assertEqual(site.default_zoom, 18.5)
        self.assertEqual(site.default_bearing, 120.0)
        self.assertEqual(site.default_pitch, 60.0)
