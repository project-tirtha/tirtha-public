"""
Tests for the Overpass seeding path (internal design notes).

The properties that matter here are mostly about restraint. Overpass is a
seeding tool, not the runtime source: nothing it returns may reach the database
without a human confirming it, an unreachable mirror must not break the admin,
and the reconciliation job reports drift rather than acting on it. The ranking
is a convenience and is tested as one -- it orders candidates, it does not
decide.

No test here touches the network; the mirror is stubbed.
"""

import json
from io import StringIO
from unittest.mock import patch

import requests
from django.contrib.admin.sites import site as admin_site
from django.core.management import call_command
from django.test import TestCase, override_settings

from tirtha.models import Site
from tirtha.overpass import OverpassClient, OverpassUnavailable

MIRROR = "http://127.0.0.1:8003/api/interpreter"


def _element(osm_type="way", osm_id=1, name="Mukteswar Temple", version=7, **tags):
    el = {"type": osm_type, "id": osm_id, "version": version, "tags": {"name": name}}
    el["tags"].update(tags)
    if osm_type == "node":
        el["lat"], el["lon"] = 20.2451, 85.8383
    else:
        el["center"] = {"lat": 20.2451, "lon": 85.8383}
    return el


def _payload(*elements):
    return {"elements": list(elements)}


class _Stub:
    """Returns a queued payload per call, so widening can be observed."""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.queries = []

    def __call__(self, url, data=None, timeout=None, **kw):
        self.queries.append(data["data"])
        payload = self.payloads.pop(0) if self.payloads else {"elements": []}

        class _Response:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return payload

        return _Response()


@override_settings(OVERPASS_URL=MIRROR)
class ClientAvailabilityTests(TestCase):
    @override_settings(OVERPASS_URL="")
    def test_an_unset_mirror_is_not_an_error_state(self):
        # Overpass is optional infrastructure -- dev must not need 15 GB to show
        # a marker (internal design notes).
        client = OverpassClient()
        self.assertFalse(client.configured)
        with self.assertRaises(OverpassUnavailable) as ctx:
            client.query("[out:json];node(1);out;")
        self.assertIn("setup-overpass.sh", str(ctx.exception))

    def test_a_timeout_is_reported_not_raised_as_requests_noise(self):
        with patch("requests.post", side_effect=requests.exceptions.Timeout()):
            with self.assertRaises(OverpassUnavailable) as ctx:
                OverpassClient().query("[out:json];node(1);out;")
        self.assertIn("timed out", str(ctx.exception))

    def test_an_html_error_page_is_reported_as_a_rejected_query(self):
        # Overpass answers its own errors with HTTP 200 and an HTML body, so a
        # JSON decode failure is the signal.
        class _Html:
            def raise_for_status(self):
                pass

            def json(self):
                raise ValueError("not json")

        with patch("requests.post", return_value=_Html()):
            with self.assertRaises(OverpassUnavailable) as ctx:
                OverpassClient().query("[out:json];node(1);out;")
        self.assertIn("rejected", str(ctx.exception))


@override_settings(OVERPASS_URL=MIRROR)
class ParsingTests(TestCase):
    def test_ways_and_relations_use_their_representative_point(self):
        cands = OverpassClient()._to_candidates(_payload(_element(osm_type="way")))
        self.assertEqual(len(cands), 1)
        self.assertAlmostEqual(cands[0].latitude, 20.2451)

    def test_nodes_use_their_own_coordinate(self):
        cands = OverpassClient()._to_candidates(_payload(_element(osm_type="node")))
        self.assertAlmostEqual(cands[0].longitude, 85.8383)

    def test_elements_without_a_coordinate_are_dropped(self):
        # A candidate that cannot seed a coordinate is not a candidate.
        payload = _payload({"type": "relation", "id": 9, "tags": {"name": "X"}})
        self.assertEqual(OverpassClient()._to_candidates(payload), [])

    def test_the_element_version_is_carried_through(self):
        # Drift detection is an integer compare against this (internal design notes).
        cands = OverpassClient()._to_candidates(_payload(_element(version=12)))
        self.assertEqual(cands[0].version, 12)

    def test_quotes_in_a_site_name_cannot_break_out_of_the_query(self):
        site = Site(name='Rani"s Temple', district="Khordha", state="Odisha")
        stub = _Stub(_payload(_element()))
        with patch("requests.post", stub):
            OverpassClient().find_site(site)
        self.assertNotIn('"Rani"s', stub.queries[0])


@override_settings(OVERPASS_URL=MIRROR)
class TransliterationTests(TestCase):
    """
    Romanised Indic names have no single spelling, and ours and OSM's disagree
    constantly. The first run of this matcher against the real mirror reported
    "no candidates" for well-known temples that are in fact mapped -- these are
    those exact pairs.
    """

    def test_a_dropped_trailing_a_still_matches(self):
        # We store "Brahmeswara Temple"; OSM has way/459233424 "Brahmeswar
        # Temple". No exact token in common.
        cands = OverpassClient()._to_candidates(
            _payload(_element(name="Brahmeswar Temple"))
        )
        ranked = OverpassClient.rank(cands, "Brahmeswara Temple")
        self.assertGreater(ranked[0].score, 90)

    def test_a_swapped_vowel_still_matches(self):
        # Ours: "Bhaskareswar Temple". OSM: "Bhaskaraswar temple".
        cands = OverpassClient()._to_candidates(
            _payload(_element(name="Bhaskaraswar temple"))
        )
        ranked = OverpassClient.rank(cands, "Bhaskareswar Temple")
        self.assertGreater(ranked[0].score, 85)

    def test_sh_and_ee_spellings_fold_together(self):
        # Ours: "Sri Bhoga Nandishwara Temple". OSM: "Bhoga Nandeeshwara Temple".
        cands = OverpassClient()._to_candidates(
            _payload(_element(name="Bhoga Nandeeshwara Temple"))
        )
        ranked = OverpassClient.rank(cands, "Sri Bhoga Nandishwara Temple")
        self.assertGreater(ranked[0].score, 90)

    def test_folding_does_not_make_unrelated_names_match(self):
        # The failure mode of a loose matcher is confident nonsense. "Bhaja
        # Caves" against "Elephanta Caves" shares the generic word only.
        cands = OverpassClient()._to_candidates(
            _payload(_element(name="Elephanta Caves"))
        )
        ranked = OverpassClient.rank(cands, "Bhaja Caves")
        self.assertLess(ranked[0].score, 60)

    def test_the_search_regex_is_stemmed_so_variants_are_reachable(self):
        # Scoring can only rank what the query returned, so the stem is what
        # makes the variant findable in the first place.
        site = Site(name="Brahmeswara Temple", district="Khordha", state="Odisha")
        stub = _Stub({"elements": []}, _payload(_element(name="Brahmeswar Temple")))
        with patch("requests.post", stub):
            found = OverpassClient().find_site(site)
        self.assertIn("brahme", stub.queries[1].lower())
        self.assertEqual(len(found), 1)


class RankingTests(TestCase):
    def test_an_exact_name_match_outranks_a_partial_one(self):
        cands = OverpassClient()._to_candidates(
            _payload(
                _element(osm_id=1, name="Mukteswar Temple Complex"),
                _element(osm_id=2, name="Mukteswar Temple"),
            )
        )
        ranked = OverpassClient.rank(cands, "Mukteswar Temple")
        self.assertEqual(ranked[0].osm_id, 2)

    def test_the_word_temple_alone_does_not_make_a_match(self):
        # 39 of 60 names contain it (internal design notes), so it carries no
        # distinguishing power -- matching on it would surface every temple in
        # the district.
        cands = OverpassClient()._to_candidates(
            _payload(
                _element(osm_id=1, name="Some Other Temple"),
                _element(osm_id=2, name="Lingaraj"),
            )
        )
        ranked = OverpassClient.rank(cands, "Lingaraj Temple")
        self.assertEqual(ranked[0].osm_id, 2)

    def test_heritage_tags_break_ties_but_do_not_override_names(self):
        cands = OverpassClient()._to_candidates(
            _payload(
                _element(osm_id=1, name="Konark Sun Temple"),
                _element(osm_id=2, name="Totally Different", heritage="1"),
            )
        )
        ranked = OverpassClient.rank(cands, "Konark Sun Temple")
        self.assertEqual(ranked[0].osm_id, 1)

    def test_among_equal_names_the_tagged_one_wins(self):
        cands = OverpassClient()._to_candidates(
            _payload(
                _element(osm_type="node", osm_id=1, name="Gopinath Temple"),
                _element(
                    osm_type="node",
                    osm_id=2,
                    name="Gopinath Temple",
                    heritage="2",
                    wikidata="Q42",
                ),
            )
        )
        ranked = OverpassClient.rank(cands, "Gopinath Temple")
        self.assertEqual(ranked[0].osm_id, 2)
        self.assertEqual(ranked[0].wikidata_qid, "Q42")


@override_settings(OVERPASS_URL=MIRROR)
class SearchWideningTests(TestCase):
    def setUp(self):
        self.site = Site(
            name="Jagannath Temple Labanagiri",
            district="Khordha",
            state="Odisha",
            country="India",
        )

    def test_a_district_hit_stops_the_search(self):
        # Widening past a hit would bury a good district match under
        # state-wide noise.
        stub = _Stub(_payload(_element(name="Jagannath Temple Labanagiri")))
        with patch("requests.post", stub):
            found = OverpassClient().find_site(self.site)
        self.assertEqual(len(stub.queries), 1)
        self.assertEqual(len(found), 1)
        self.assertIn("Khordha", stub.queries[0])

    def test_the_search_widens_to_the_state_when_the_district_finds_nothing(self):
        stub = _Stub({"elements": []}, {"elements": []}, _payload(_element()))
        with patch("requests.post", stub):
            found = OverpassClient().find_site(self.site)
        self.assertEqual(len(found), 1)
        self.assertIn("Odisha", stub.queries[-1])

    def test_nothing_anywhere_returns_empty_rather_than_raising(self):
        with patch("requests.post", _Stub()):
            self.assertEqual(OverpassClient().find_site(self.site), [])

    def test_a_site_with_no_name_is_not_searched_for(self):
        stub = _Stub()
        with patch("requests.post", stub):
            self.assertEqual(OverpassClient().find_site(Site(name="")), [])
        self.assertEqual(stub.queries, [])


@override_settings(OVERPASS_URL=MIRROR)
class FetchElementTests(TestCase):
    def test_a_deleted_element_reads_as_absent_not_as_a_failure(self):
        # This is precisely the case reconciliation exists to catch.
        with patch("requests.post", _Stub({"elements": []})):
            self.assertIsNone(OverpassClient().fetch_element("way", 123))

    def test_an_element_type_that_is_not_an_osm_type_is_refused(self):
        with self.assertRaises(ValueError):
            OverpassClient().fetch_element("building", 1)


@override_settings(OVERPASS_URL=MIRROR)
class SeedingActionTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="Mukteswar Temple",
            country="India",
            state="Odisha",
            district="Khordha",
        )
        self.admin = admin_site._registry[Site]

    def _apply(self, value, **extra):
        class _Request:
            POST = {"apply": "1", f"choice-{self.site.pk}": value, **extra}

            def __init__(self):
                self._messages = []

        request = _Request()
        with patch.object(self.admin, "message_user", lambda *a, **k: None):
            self.admin._apply_overpass_choices(
                request, Site.objects.filter(pk=self.site.pk)
            )
        self.site.refresh_from_db()

    def test_confirming_a_candidate_records_where_it_came_from(self):
        # Provenance is what says the row is ODbL and what a bulk re-backfill
        # is allowed to overwrite (internal design notes).
        self._apply("way/123/7/20.2451/85.8383")
        self.assertEqual(self.site.location_source, "osm")
        self.assertEqual(self.site.osm_type, "way")
        self.assertEqual(self.site.osm_id, 123)
        self.assertEqual(self.site.osm_version, 7)
        self.assertAlmostEqual(self.site.latitude, 20.2451)
        self.assertIsNotNone(self.site.location_verified_at)

    def test_skipping_writes_nothing(self):
        self._apply("skip")
        self.assertIsNone(self.site.latitude)
        self.assertEqual(self.site.location_source, "")

    def test_a_missing_version_is_stored_as_absent_not_as_zero(self):
        # No version means the mirror was imported without OVERPASS_META=yes,
        # so drift detection cannot work for this row. Zero would claim it can.
        self._apply("way/123/None/20.2451/85.8383")
        self.assertIsNone(self.site.osm_version)

    def test_a_malformed_choice_is_ignored_rather_than_half_applied(self):
        self._apply("way/123")
        self.assertIsNone(self.site.latitude)

    def test_the_wikidata_qid_rides_along_when_offered(self):
        self._apply("way/123/7/20.2451/85.8383", **{f"qid-{self.site.pk}": "Q1234"})
        self.assertEqual(self.site.wikidata_qid, "Q1234")

    def test_raw_tags_are_kept_so_categories_can_be_re_derived(self):
        tags = json.dumps({"historic": "temple", "heritage": "2"})
        self._apply("way/123/7/20.2451/85.8383", **{f"tags-{self.site.pk}": tags})
        self.assertEqual(self.site.osm_tags["historic"], "temple")

    @override_settings(OVERPASS_URL="")
    def test_the_action_says_so_rather_than_failing_when_unconfigured(self):
        messages_seen = []

        class _Request:
            POST = {}

        with patch.object(
            self.admin,
            "message_user",
            lambda req, msg, level=None: messages_seen.append(str(msg)),
        ):
            result = self.admin.seed_from_overpass(
                _Request(), Site.objects.filter(pk=self.site.pk)
            )
        self.assertIsNone(result)
        self.assertIn("OVERPASS_URL is not set", messages_seen[0])


@override_settings(OVERPASS_URL=MIRROR)
class DriftReportTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="Konark Sun Temple",
            country="India",
            state="Odisha",
            district="Puri",
            latitude=19.8876,
            longitude=86.0945,
            osm_type="way",
            osm_id=555,
            osm_version=7,
            location_source="osm",
        )

    @staticmethod
    def _same_place(version):
        """The stored element, unmoved -- only its version advanced."""
        el = _element(osm_id=555, name="Konark Sun Temple", version=version)
        el["center"] = {"lat": 19.8876, "lon": 86.0945}
        return _payload(el)

    def _run(self, *payloads):
        out = StringIO()
        with patch("requests.post", _Stub(*payloads)):
            call_command("check_osm_drift", stdout=out, stderr=out)
        return out.getvalue()

    def test_an_unchanged_element_reports_no_drift(self):
        self.assertIn("No drift", self._run(self._same_place(7)))

    def test_a_retag_is_reported_without_moving_anything(self):
        # Never silently relocate a marker (internal design notes).
        out = self._run(self._same_place(9))
        self.assertIn("retagged", out)
        self.assertIn("v7 -> v9", out)
        self.site.refresh_from_db()
        self.assertAlmostEqual(self.site.latitude, 19.8876)
        self.assertEqual(self.site.osm_version, 7)

    def test_a_real_move_is_distinguished_from_a_retag(self):
        moved = _element(osm_id=555, version=9)
        moved["center"] = {"lat": 19.9999, "lon": 86.5555}
        out = self._run(_payload(moved))
        self.assertIn("moved", out)

    def test_a_deleted_element_is_reported_as_gone(self):
        self.assertIn("gone", self._run({"elements": []}))

    def test_a_row_with_no_stored_version_is_flagged_as_uncheckable(self):
        # The old mirror ran OVERPASS_META=no, so rows seeded from it cannot be
        # compared at all -- that is a finding, not a silent pass.
        self.site.osm_version = None
        self.site.save()
        out = self._run(self._same_place(9))
        self.assertIn("unversioned", out)

    def test_sites_that_were_never_located_are_not_queried(self):
        Site.objects.create(name="Unlocated", country="India", state="X", district="Y")
        stub = _Stub(self._same_place(7))
        with patch("requests.post", stub):
            call_command("check_osm_drift", stdout=StringIO())
        self.assertEqual(len(stub.queries), 1)

    @override_settings(OVERPASS_URL="")
    def test_the_command_is_a_no_op_without_a_mirror(self):
        out = StringIO()
        call_command("check_osm_drift", stdout=out, stderr=out)
        self.assertIn("OVERPASS_URL is not set", out.getvalue())


@override_settings(OVERPASS_URL=MIRROR)
class CandidateReportTests(TestCase):
    """
    The dry-run report exists so the hit rate is knowable before anyone starts
    confirming. Its one hard property is that it writes nothing.
    """

    def setUp(self):
        self.site = Site.objects.create(
            name="Mukteswar Temple",
            country="India",
            state="Odisha",
            district="Khordha",
        )

    def _run(self, *payloads, **opts):
        out = StringIO()
        with patch("requests.post", _Stub(*payloads)):
            call_command("osm_candidates", stdout=out, stderr=out, **opts)
        return out.getvalue()

    def test_the_report_never_writes(self):
        self._run(_payload(_element(name="Mukteswar Temple")))
        self.site.refresh_from_db()
        self.assertIsNone(self.site.latitude)
        self.assertEqual(self.site.location_source, "")

    def test_a_lone_exact_match_reads_as_clear(self):
        out = self._run(_payload(_element(name="Mukteswar Temple")))
        self.assertIn("clear", out)

    def test_two_plausible_matches_read_as_needing_a_judgement_call(self):
        # The Gopinath case: a scorer cannot separate these, so the report must
        # not pretend it can.
        out = self._run(
            _payload(
                _element(osm_type="node", osm_id=1, name="Mukteswar Temple"),
                _element(osm_type="node", osm_id=2, name="Mukteswar Temple"),
            )
        )
        self.assertIn("ambiguous", out)

    def test_no_match_is_reported_rather_than_passed_over(self):
        out = self._run({"elements": []}, {"elements": []}, {"elements": []})
        self.assertIn("none", out)
        self.assertIn("nothing found", out)

    def test_located_sites_are_skipped_unless_asked_for(self):
        self.site.latitude, self.site.longitude = 20.0, 85.0
        self.site.save()
        stub = _Stub(_payload(_element()))
        with patch("requests.post", stub):
            call_command("osm_candidates", stdout=StringIO())
        self.assertEqual(stub.queries, [])

    def test_json_output_is_parseable(self):
        out = self._run(_payload(_element(name="Mukteswar Temple")), **{"json": True})
        rows = json.loads(out)
        self.assertEqual(rows[0]["name"], "Mukteswar Temple")
        self.assertEqual(rows[0]["candidates"][0]["osm"], "way/1")


class GeoMigrationDumpTests(TestCase):
    """
    The emitter that makes confirmed coordinates survive cutover
    (internal design notes).

    Confirmations are human decisions made in the admin. The cutover restores a
    fresh production dump and re-runs the chain, so anything typed into a
    database is lost at that point -- this turns those decisions into a
    migration that replays like every other change.
    """

    def setUp(self):
        self.site = Site.objects.create(
            name="Konark Sun Temple",
            country="India",
            state="Odisha",
            district="Puri",
            latitude=19.8876,
            longitude=86.0945,
            osm_type="way",
            osm_id=555,
            osm_version=7,
            location_source="osm",
        )

    def _dump(self, **opts):
        out, err = StringIO(), StringIO()
        call_command("dump_geo_migration", stdout=out, stderr=err, **opts)
        return out.getvalue(), err.getvalue()

    def test_a_confirmed_site_is_emitted(self):
        source, _ = self._dump()
        self.assertIn(self.site.ID, source)
        self.assertIn("19.8876", source)

    def test_the_output_is_a_valid_migration_module(self):
        source, _ = self._dump()
        namespace = {}
        exec(compile(source, "generated", "exec"), namespace)  # noqa: S102
        self.assertIn("Migration", namespace)
        self.assertTrue(callable(namespace["apply_geo"]))

    def test_unlocated_sites_are_not_emitted(self):
        Site.objects.create(name="Unlocated", country="India", state="X", district="Y")
        source, _ = self._dump()
        self.assertNotIn("Unlocated", source)

    def test_manually_placed_sites_are_excluded_by_default(self):
        # The default dumps the OSM-sourced set, which is what the seeding
        # session produces; a hand-entered coordinate is not that.
        Site.objects.create(
            name="ByHand",
            country="India",
            state="X",
            district="Y",
            latitude=1.0,
            longitude=2.0,
            location_source="manual",
        )
        source, _ = self._dump()
        self.assertNotIn("ByHand", source)

    def test_the_odbl_obligation_is_recorded_in_the_output(self):
        source, _ = self._dump()
        self.assertIn("ODbL", source)

    def test_nothing_to_dump_says_so_rather_than_emitting_an_empty_migration(self):
        Site.objects.all().update(latitude=None, longitude=None)
        source, err = self._dump()
        self.assertEqual(source.strip(), "")
        self.assertIn("No confirmed coordinates", err)

    def test_the_generated_migration_reapplies_the_coordinates(self):
        source, _ = self._dump()
        namespace = {}
        exec(compile(source, "generated", "exec"), namespace)  # noqa: S102

        Site.objects.filter(pk=self.site.pk).update(
            latitude=None, longitude=None, location_source=""
        )

        class _Apps:
            @staticmethod
            def get_model(app_label, model_name):
                return Site

        namespace["apply_geo"](_Apps, None)
        self.site.refresh_from_db()
        self.assertAlmostEqual(self.site.latitude, 19.8876)
        self.assertEqual(self.site.location_source, "osm")

    def test_a_later_manual_correction_is_not_clobbered(self):
        # Replaying the seeding must not undo a human fix made afterwards.
        source, _ = self._dump()
        namespace = {}
        exec(compile(source, "generated", "exec"), namespace)  # noqa: S102

        Site.objects.filter(pk=self.site.pk).update(
            latitude=1.234, longitude=5.678, location_source="manual"
        )

        class _Apps:
            @staticmethod
            def get_model(app_label, model_name):
                return Site

        namespace["apply_geo"](_Apps, None)
        self.site.refresh_from_db()
        self.assertAlmostEqual(self.site.latitude, 1.234)

    def test_a_site_deleted_since_confirmation_is_skipped(self):
        source, _ = self._dump()
        namespace = {}
        exec(compile(source, "generated", "exec"), namespace)  # noqa: S102
        self.site.delete()

        class _Apps:
            @staticmethod
            def get_model(app_label, model_name):
                return Site

        namespace["apply_geo"](_Apps, None)  # must not raise
