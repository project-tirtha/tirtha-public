"""
Tests for `backfill_geo` (internal design notes).

The command writes to a field the map renders directly, so the interesting
cases are all about what it *refuses* to write: a lone GPS fix, a fix from the
contributor's home city, a coordinate an operator typed by hand.
"""

import uuid
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from tirtha.management.commands.backfill_geo import Command
from tirtha.models import Contribution, Contributor, Image, Site
from tirtha.overpass import Candidate


def _site(name="Mukteswar Temple", **kw):
    fields = {
        "name": name,
        "country": "India",
        "state": "Odisha",
        "district": "Khordha",
    }
    fields.update(kw)
    return Site.objects.create(**fields)


def _images(site, points):
    contributor = Contributor.objects.create(
        email=f"{uuid.uuid4().hex}@example.org", name="Tester"
    )
    contribution = Contribution.objects.create(site=site, contributor=contributor)
    for lat, lon in points:
        Image.objects.create(
            contribution=contribution,
            image=f"images/{uuid.uuid4().hex}.jpg",
            gps_latitude=lat,
            gps_longitude=lon,
        )
    return contribution


def _run(*args):
    out = StringIO()
    call_command("backfill_geo", *args, "--exif-only", stdout=out, stderr=StringIO())
    return out.getvalue()


class ExifBackfillTests(TestCase):
    def test_a_cluster_of_fixes_becomes_the_site_coordinate(self):
        site = _site()
        _images(site, [(20.2445, 85.8371), (20.2447, 85.8369), (20.2446, 85.8370)])

        _run()

        site.refresh_from_db()
        self.assertAlmostEqual(site.latitude, 20.2446, places=3)
        self.assertAlmostEqual(site.longitude, 85.8370, places=3)
        self.assertEqual(site.location_source, "exif")
        self.assertIsNotNone(site.location_verified_at)

    def test_one_lone_fix_is_not_a_location(self):
        # A single GPS-tagged photograph is an anecdote. Writing it would put a
        # marker on the map with nothing corroborating it.
        site = _site()
        _images(site, [(20.2445, 85.8371)])

        _run()

        site.refresh_from_db()
        self.assertIsNone(site.latitude)

    def test_a_distant_outlier_does_not_move_the_answer(self):
        # The archive genuinely contains images tagged where the contributor
        # edited them rather than where they shot them. A mean would be dragged
        # halfway to Delhi; the median plus the cluster filter must not be.
        site = _site()
        _images(
            site,
            [
                (20.2445, 85.8371),
                (20.2447, 85.8369),
                (20.2446, 85.8370),
                (28.61, 77.20),
            ],
        )

        _run()

        site.refresh_from_db()
        self.assertAlmostEqual(site.latitude, 20.2446, places=2)

    def test_null_island_fixes_are_discarded(self):
        # Cameras with no fix write 0,0, which is a real place in the Atlantic.
        site = _site()
        _images(site, [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)])

        _run()

        site.refresh_from_db()
        self.assertIsNone(site.latitude)

    def test_out_of_range_values_are_discarded(self):
        site = _site()
        _images(site, [(200.0, 500.0), (200.0, 500.0), (201.0, 500.0)])

        _run()

        site.refresh_from_db()
        self.assertIsNone(site.latitude)


class OverwriteTests(TestCase):
    def test_a_site_that_already_has_a_coordinate_is_left_alone(self):
        site = _site(latitude=1.0, longitude=2.0, location_source="osm")
        _images(site, [(20.2445, 85.8371), (20.2447, 85.8369), (20.2446, 85.8370)])

        _run()

        site.refresh_from_db()
        self.assertEqual(site.latitude, 1.0)

    def test_refresh_never_overwrites_a_hand_entered_coordinate(self):
        # An operator who typed a coordinate in the admin outranks every
        # automatic source; --refresh must not quietly undo that decision.
        site = _site(latitude=1.0, longitude=2.0, location_source="manual")
        _images(site, [(20.2445, 85.8371), (20.2447, 85.8369), (20.2446, 85.8370)])

        _run("--refresh")

        site.refresh_from_db()
        self.assertEqual(site.latitude, 1.0)
        self.assertEqual(site.location_source, "manual")

    def test_refresh_does_replace_a_derived_coordinate(self):
        site = _site(latitude=1.0, longitude=2.0, location_source="osm")
        _images(site, [(20.2445, 85.8371), (20.2447, 85.8369), (20.2446, 85.8370)])

        _run("--refresh")

        site.refresh_from_db()
        self.assertAlmostEqual(site.latitude, 20.2446, places=3)
        self.assertEqual(site.location_source, "exif")


class DryRunTests(TestCase):
    def test_dry_run_writes_nothing(self):
        site = _site()
        _images(site, [(20.2445, 85.8371), (20.2447, 85.8369), (20.2446, 85.8370)])

        output = _run("--dry-run")

        site.refresh_from_db()
        self.assertIsNone(site.latitude)
        self.assertIn("nothing was written", output.lower())


class OsmScopeTests(TestCase):
    """
    A name match is not a location (internal design notes).

    `find_site` widens from the district to the whole state when the district
    turns up nothing. A state-wide hit scores identically on the name while
    having no locality evidence behind it, and the first run of this command
    duly pinned a Darbhanga site to a same-named temple near Gaya.
    """

    def setUp(self):
        self.site = _site(name="Manokamna Temple", state="Bihar", district="Darbhanga")

    @staticmethod
    def _candidate(scope, name="Manokamna Temple", score=100.0):
        return Candidate(
            osm_type="node",
            osm_id=1,
            name=name,
            latitude=24.8,
            longitude=85.0,
            score=score,
            scope=scope,
        )

    def test_a_district_match_is_accepted(self):
        picked = Command()._pick(self.site, [self._candidate("district")], False)
        self.assertIsNotNone(picked)

    def test_a_state_wide_match_is_refused(self):
        picked = Command()._pick(self.site, [self._candidate("state")], False)
        self.assertIsNone(picked)

    def test_a_state_wide_match_is_available_to_a_watching_operator(self):
        picked = Command()._pick(self.site, [self._candidate("state")], True)
        self.assertIsNotNone(picked)

    def test_a_close_runner_up_still_blocks_a_district_match(self):
        # The margin rule did not go away; scope is an additional condition,
        # not a replacement for it.
        candidates = [
            self._candidate("district", score=100.0),
            self._candidate("district", name="Manokamna Mandir", score=95.0),
        ]
        self.assertIsNone(Command()._pick(self.site, candidates, False))
