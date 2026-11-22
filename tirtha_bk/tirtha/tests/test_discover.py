"""
Tests for the discovery layer (internal design notes).

The endpoint proxies a 15 GB Overpass mirror onto a public map, and almost
everything worth asserting here is about what it refuses to do: never reach the
network from a test, never let a mirror failure reach the visitor as an error,
never publish the upstream address, never query at all when the answer is
already known, and never show a pin for a site we already hold.

The mirror is stubbed in every test. Nothing here talks to `:8003`.
"""

import json
import re
from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from tirtha.models import Site
from tirtha.overpass import DISCOVERY_MAX_RESULTS

MIRROR = "http://127.0.0.1:8003/api/interpreter"

# A viewport over central Bhubaneswar, ~0.09 deg across -- roughly a desktop
# map at zoom 13.
BBOX = "85.80,20.20,85.89,20.29"


def _element(osm_type="node", osm_id=12345, name="Some Temple", **tags):
    el = {"type": osm_type, "id": osm_id, "tags": {"name": name, **tags}}
    if osm_type == "node":
        el["lat"], el["lon"] = 20.25, 85.84
    else:
        el["center"] = {"lat": 20.25, "lon": 85.84}
    return el


class _Stub:
    """Records every query it is asked and answers with a fixed payload."""

    def __init__(self, *elements, error=None):
        self.payload = {"elements": list(elements)}
        self.error = error
        self.queries = []

    def __call__(self, url, data=None, timeout=None, **kw):
        self.queries.append(data["data"])
        if self.error is not None:
            raise self.error
        payload = self.payload

        class _Response:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return payload

        return _Response()

    @property
    def called(self) -> bool:
        return bool(self.queries)


class _Base(TestCase):
    """
    Both the throttle counter and the viewport cache live in the process-wide
    cache, which Django does not reset between tests. Clearing it keeps each
    test inside its own window and its own cold cache, rather than the fourth
    test in a class inheriting the first three's answers.
    """

    def setUp(self):
        cache.clear()

    def _get(self, bbox=BBOX, zoom=14, **extra):
        params = {}
        if bbox is not None:
            params["bbox"] = bbox
        if zoom is not None:
            params["zoom"] = zoom
        return self.client.get(reverse("api-discover"), params, **extra)

    def _body(self, response):
        return json.loads(response.content)


@override_settings(OVERPASS_URL=MIRROR, DISCOVER_MIN_ZOOM=12)
class QueryTests(_Base):
    def _query(self, **kw):
        stub = _Stub(_element())
        with patch("requests.post", stub):
            self._get(**kw)
        return stub.queries[0]

    def test_the_tag_union_is_all_four_arms(self):
        # `heritage=*` is sparse and inconsistent in India, which is the whole
        # reason this is a union and not one filter (internal design notes).
        ql = self._query()
        self.assertIn('nwr["heritage"];', ql)
        self.assertIn('nwr["historic"];', ql)
        self.assertIn('nwr["amenity"="place_of_worship"]["building"="temple"];', ql)
        self.assertIn('nwr["wikidata"];', ql)

    def test_place_of_worship_is_conjoined_with_building_temple(self):
        # Alone it is every mosque, church and roadside shrine in the viewport.
        ql = self._query()
        self.assertNotIn('nwr["amenity"="place_of_worship"];', ql)

    def test_the_query_is_scoped_to_the_viewport(self):
        # Overpass wants south,west,north,east where the client sends
        # west,south,east,north; transposing it wrongly returns nothing rather
        # than erroring, so the order is asserted rather than assumed.
        ql = self._query(bbox="85.80,20.20,85.89,20.29")
        self.assertIn("[bbox:20.200000,85.800000,20.300000,85.900000]", ql)

    def test_the_queried_box_contains_the_requested_one(self):
        # Snapped outward, so a pan inside the cell is answered from a payload
        # that already covers where the visitor now is.
        ql = self._query(bbox="85.83,20.22,85.86,20.24")
        south, west, north, east = (
            float(v) for v in ql.split("[bbox:")[1].split("]")[0].split(",")
        )
        self.assertLessEqual(west, 85.83)
        self.assertLessEqual(south, 20.22)
        self.assertGreaterEqual(east, 85.86)
        self.assertGreaterEqual(north, 20.24)

    def test_a_bbox_already_on_the_grid_is_not_widened(self):
        # `85.80 / 0.05` is 1715.9999999999998 in binary floating point, so
        # without the epsilon in `_bbox_cell` an edge sitting exactly on a grid
        # line floors to the cell below and the query grows by a whole cell on
        # that side -- for most viewports, on all four.
        ql = self._query(bbox="85.80,20.20,85.90,20.30")
        self.assertIn("[bbox:20.200000,85.800000,20.300000,85.900000]", ql)

    def test_no_meta_is_requested(self):
        # Discovery is the unpinned half of the Overpass split (internal design notes);
        # element versions exist for drift detection on coordinates we pinned.
        ql = self._query()
        self.assertIn("out center tags", ql)
        self.assertNotIn("meta", ql)

    def test_the_result_count_is_capped(self):
        ql = self._query()
        self.assertRegex(ql, r"out center tags \d+;")


@override_settings(OVERPASS_URL=MIRROR, DISCOVER_MIN_ZOOM=12)
class PayloadTests(_Base):
    def _fetch(self, *elements, **kw):
        stub = _Stub(*elements)
        with patch("requests.post", stub):
            response = self._get(**kw)
        return response, self._body(response), stub

    def test_the_success_envelope(self):
        response, body, _ = self._fetch(_element(wikidata="Q123"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["version"], "v1")
        self.assertEqual(body["min_zoom"], 12)
        self.assertEqual(body["count"], 1)
        self.assertNotIn("reason", body)
        self.assertEqual(
            body["results"][0],
            {
                "osm_type": "node",
                "osm_id": "12345",
                "name": "Some Temple",
                "latitude": 20.25,
                "longitude": 85.84,
                "wikidata": "Q123",
                "kind": "wikidata",
            },
        )

    def test_attribution_is_always_present(self):
        # ODbL (internal design notes). These are live, unpinned OSM rows with no
        # `location_source` to hang the credit on, so it travels in the payload
        # -- and it has to be there on the empty answers too, because the
        # frontend renders the line from this field.
        for label, kwargs in (
            ("results", {}),
            ("empty", {"zoom": 3}),
        ):
            with self.subTest(label):
                _, body, _ = self._fetch(_element(), **kwargs)
                self.assertIn("OpenStreetMap", body["attribution"])
                self.assertIn("ODbL", body["attribution"])

    def test_osm_id_is_a_string(self):
        # An identifier, not a quantity. Nothing should do arithmetic on it.
        _, body, _ = self._fetch(_element(osm_id=987654321))
        self.assertEqual(body["results"][0]["osm_id"], "987654321")

    def test_kind_reports_the_strongest_arm_matched(self):
        cases = (
            ({"heritage": "2", "historic": "temple"}, "heritage"),
            ({"historic": "ruins", "wikidata": "Q1"}, "historic"),
            ({"amenity": "place_of_worship", "building": "temple"}, "place_of_worship"),
            ({"wikidata": "Q9"}, "wikidata"),
        )
        for tags, expected in cases:
            with self.subTest(tags=tags):
                # Same viewport each time, so without this every subtest after
                # the first is answered from the first one's cache entry.
                cache.clear()
                _, body, _ = self._fetch(_element(**tags))
                self.assertEqual(body["results"][0]["kind"], expected)

    def test_unnamed_elements_are_dropped(self):
        # A pin exists to be tapped and turned into a site request; "request an
        # unnamed way" is not a request anyone can act on. It also removes most
        # of what the `wikidata=*` arm sweeps up.
        _, body, _ = self._fetch(_element(name="", wikidata="Q1"), _element(osm_id=2))
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["osm_id"], "2")

    def test_ways_use_their_representative_point(self):
        _, body, _ = self._fetch(_element(osm_type="way", osm_id=7))
        self.assertAlmostEqual(body["results"][0]["latitude"], 20.25)


@override_settings(OVERPASS_URL=MIRROR, DISCOVER_MIN_ZOOM=12)
class QuietFailureTests(_Base):
    """
    Three documented ways to answer nothing, all of them 200.

    The discovery layer is one overlay on a working map. An error status turns
    a missing overlay into an error banner over sites that are rendering fine,
    and none of the three are the visitor's fault.
    """

    def test_below_minimum_zoom_answers_empty_without_querying(self):
        stub = _Stub(_element())
        with patch("requests.post", stub):
            response = self._get(zoom=11)
        body = self._body(response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["reason"], "zoom")
        self.assertEqual(body["results"], [])
        self.assertEqual(body["min_zoom"], 12)
        # The point of a zoom threshold is not asking at all.
        self.assertFalse(stub.called)

    @override_settings(OVERPASS_URL="")
    def test_an_unset_mirror_answers_empty_without_querying(self):
        # Overpass is optional infrastructure and this is the dev default, so
        # it is the common path, not an edge case (internal design notes).
        stub = _Stub(_element())
        with patch("requests.post", stub):
            response = self._get()
        body = self._body(response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["reason"], "unconfigured")
        self.assertEqual(body["results"], [])
        self.assertFalse(stub.called)

    @override_settings(OVERPASS_URL="")
    def test_an_unset_mirror_says_so_rather_than_asking_for_more_zoom(self):
        # Where there is no mirror, zooming in will never produce a pin, so
        # "zoom" would be advice that cannot work.
        body = self._body(self._get(zoom=2))
        self.assertEqual(body["reason"], "unconfigured")

    def test_a_mirror_timeout_is_not_a_500(self):
        stub = _Stub(error=requests.exceptions.Timeout())
        with patch("requests.post", stub):
            response = self._get()
        body = self._body(response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["reason"], "unavailable")
        self.assertEqual(body["results"], [])

    def test_a_mirror_error_is_not_a_500(self):
        stub = _Stub(error=requests.exceptions.ConnectionError("refused"))
        with patch("requests.post", stub):
            response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._body(response)["reason"], "unavailable")

    def test_the_upstream_address_is_never_echoed(self):
        # The browser must never reach :8003 (internal design notes). Publishing the
        # internal endpoint in an error body to anyone who can make the mirror
        # time out would defeat keeping it bound to localhost.
        stub = _Stub(
            error=requests.exceptions.ConnectionError(
                f"HTTPConnectionPool(host='127.0.0.1', port=8003): {MIRROR}"
            )
        )
        with patch("requests.post", stub):
            response = self._get()
        text = response.content.decode()
        self.assertNotIn("8003", text)
        self.assertNotIn("127.0.0.1", text)
        self.assertNotIn("interpreter", text)


@override_settings(OVERPASS_URL=MIRROR, DISCOVER_MIN_ZOOM=12)
class ExclusionTests(_Base):
    """
    The layer shows what is *possible*. A site we already hold is not.
    """

    def _fetch(self, *elements):
        with patch("requests.post", _Stub(*elements)):
            return self._body(self._get())

    def test_a_matched_site_is_not_offered_as_a_discovery_pin(self):
        Site.objects.create(
            name="Some Temple",
            country="India",
            state="Odisha",
            district="Khordha",
            osm_type="node",
            osm_id=12345,
        )
        body = self._fetch(_element(osm_id=12345), _element(osm_id=999, name="Other"))
        self.assertEqual([r["osm_id"] for r in body["results"]], ["999"])

    def test_the_match_is_on_the_type_as_well_as_the_id(self):
        # Node 5 and way 5 are different elements. An id-only exclusion would
        # silently hide the wrong one.
        Site.objects.create(
            name="Ours",
            country="India",
            state="O",
            district="K",
            osm_type="node",
            osm_id=5,
        )
        body = self._fetch(_element(osm_type="way", osm_id=5, name="A Way"))
        self.assertEqual(body["count"], 1)

    def test_a_hidden_site_still_excludes_its_element(self):
        # Production hides test rows and work in progress (internal design notes). A
        # site being reconstructed is the last thing to offer as unclaimed.
        Site.objects.create(
            name="In progress",
            country="India",
            state="O",
            district="K",
            osm_type="way",
            osm_id=42,
            hidden=True,
        )
        body = self._fetch(_element(osm_type="way", osm_id=42))
        self.assertEqual(body["results"], [])

    def test_a_site_with_no_osm_reference_excludes_nothing(self):
        Site.objects.create(name="Unmatched", country="India", state="O", district="K")
        body = self._fetch(_element())
        self.assertEqual(body["count"], 1)

    def test_the_exclusion_is_one_query_not_one_per_result(self):
        # A viewport can return hundreds of elements, and `.exists()` per
        # element would be hundreds of round trips to save a set lookup.
        Site.objects.create(
            name="Ours",
            country="India",
            state="O",
            district="K",
            osm_type="node",
            osm_id=1,
        )
        many = [_element(osm_id=i, name=f"Temple {i}") for i in range(1, 61)]
        with patch("requests.post", _Stub(*many)):
            with self.assertNumQueries(1):
                response = self._get()
        self.assertEqual(self._body(response)["count"], 59)


@override_settings(OVERPASS_URL=MIRROR, DISCOVER_MIN_ZOOM=12)
class CacheTests(_Base):
    def test_a_repeated_viewport_does_not_query_the_mirror_twice(self):
        stub = _Stub(_element())
        with patch("requests.post", stub):
            self.assertEqual(self._get().status_code, 200)
            self.assertEqual(self._get().status_code, 200)
        self.assertEqual(len(stub.queries), 1)

    def test_a_small_pan_lands_in_the_same_cell(self):
        # The grid is what stops every pixel of panning being a fresh mirror
        # query and a fresh cache entry that is never read again.
        stub = _Stub(_element())
        with patch("requests.post", stub):
            self._get(bbox="85.831,20.221,85.849,20.239")
            self._get(bbox="85.832,20.222,85.848,20.238")
        self.assertEqual(len(stub.queries), 1)

    def test_a_different_region_is_a_different_entry(self):
        stub = _Stub(_element())
        with patch("requests.post", stub):
            self._get(bbox="85.80,20.20,85.89,20.29")
            self._get(bbox="86.80,19.80,86.89,19.89")
        self.assertEqual(len(stub.queries), 2)

    def test_zoom_is_not_part_of_the_cache_key(self):
        # Nothing in the tag union depends on zoom, so zooming over the same
        # ground must not miss an answer already held.
        stub = _Stub(_element())
        with patch("requests.post", stub):
            self._get(zoom=13)
            self._get(zoom=16)
        self.assertEqual(len(stub.queries), 1)

    def test_the_exclusion_is_applied_after_the_cache(self):
        # Caching the filtered list would keep showing a discovery pin for a
        # site an operator matched ten minutes ago, for the entry's whole life.
        stub = _Stub(_element(osm_id=12345))
        with patch("requests.post", stub):
            self.assertEqual(self._body(self._get())["count"], 1)
            Site.objects.create(
                name="Some Temple",
                country="India",
                state="O",
                district="K",
                osm_type="node",
                osm_id=12345,
            )
            self.assertEqual(self._body(self._get())["count"], 0)
        self.assertEqual(len(stub.queries), 1)

    @override_settings(DISCOVER_CACHE_SECONDS=0)
    def test_the_ttl_is_read_at_call_time(self):
        # Bound at import, `override_settings` would never reach it -- a trap
        # `views.py` falls into and this module stays out of (internal design notes).
        stub = _Stub(_element())
        with patch("requests.post", stub):
            self._get()
            self._get()
        self.assertEqual(len(stub.queries), 2)


@override_settings(OVERPASS_URL=MIRROR, DISCOVER_MIN_ZOOM=12)
class ValidationTests(_Base):
    """
    The one discovery failure that is the caller's fault, and so a 400.

    Answering a broken bbox with a cheerful empty layer would hide the bug for
    as long as it took someone to notice the pins never appear.
    """

    def _refused(self, **kw):
        stub = _Stub(_element())
        with patch("requests.post", stub):
            response = self._get(**kw)
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", self._body(response))
        self.assertFalse(stub.called)
        return response

    def test_an_unparseable_bbox_is_refused(self):
        for bbox in ("", "here", "85.8,20.2", "85.8,20.2,85.9,20.3,1", "a,b,c,d"):
            with self.subTest(bbox=bbox):
                self._refused(bbox=bbox)

    def test_an_inverted_bbox_is_refused(self):
        self._refused(bbox="85.89,20.20,85.80,20.29")  # east of west
        self._refused(bbox="85.80,20.29,85.89,20.20")  # north of south

    def test_a_degenerate_bbox_is_refused(self):
        self._refused(bbox="85.80,20.20,85.80,20.20")

    def test_an_out_of_range_bbox_is_refused(self):
        self._refused(bbox="85.80,-91.0,85.89,20.29")
        self._refused(bbox="-181.0,20.20,85.89,20.29")

    def test_a_non_finite_bbox_is_refused(self):
        # nan slips past every comparison silently: all of them are False.
        self._refused(bbox="nan,20.20,85.89,20.29")
        self._refused(bbox="85.80,20.20,inf,20.29")

    @override_settings(DISCOVER_MAX_BBOX_SPAN=2.0)
    def test_a_bbox_over_the_whole_country_is_refused(self):
        # Zoom is a claim the client makes about itself and nothing binds it to
        # the bbox, which is why the span ceiling exists as well.
        self._refused(bbox="68.0,6.0,98.0,36.0", zoom=14)

    @override_settings(DISCOVER_MAX_BBOX_SPAN=0.05)
    def test_the_span_ceiling_is_read_at_call_time(self):
        self._refused(bbox="85.80,20.20,85.89,20.29")

    def test_a_non_integer_zoom_is_refused(self):
        for zoom in ("", "twelve", "13.5", None):
            with self.subTest(zoom=zoom):
                self._refused(zoom=zoom)

    def test_the_endpoint_rejects_post(self):
        self.assertEqual(self.client.post(reverse("api-discover")).status_code, 405)


@override_settings(OVERPASS_URL=MIRROR, DISCOVER_MIN_ZOOM=12)
class ThrottleTests(_Base):
    """
    Its own per-IP budget, on the same counter the site-request endpoint uses.

    This one proxies to a 15 GB database, and the viewport cache in front of it
    only absorbs repeats of the *same* cell -- a caller walking the grid misses
    it every time.
    """

    def _fetch(self, **extra):
        with patch("requests.post", _Stub(_element())):
            return self._get(**extra)

    @override_settings(DISCOVER_RATE_LIMIT=3, DISCOVER_RATE_WINDOW=3600)
    def test_requests_are_accepted_up_to_the_limit_then_refused(self):
        for i in range(3):
            self.assertEqual(
                self._fetch(bbox=f"85.{i}0,20.20,85.{i}9,20.29").status_code, 200
            )
        response = self._fetch(bbox="86.00,20.20,86.09,20.29")
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response.headers)

    @override_settings(DISCOVER_RATE_LIMIT=1)
    def test_the_throttle_runs_before_validation(self):
        # A 400 that costs nothing is still a way to keep the endpoint busy.
        self.assertEqual(self._fetch(bbox="nonsense").status_code, 400)
        self.assertEqual(self._fetch().status_code, 429)

    @override_settings(DISCOVER_RATE_LIMIT=1, SITE_REQUEST_RATE_LIMIT=5)
    def test_the_budget_is_separate_from_the_write_endpoint(self):
        # One shared budget would either make the map unusable or make the
        # write endpoint's limit meaningless.
        self._fetch()
        self.assertEqual(self._fetch().status_code, 429)
        response = self.client.post(
            reverse("api-site-request"),
            data=json.dumps(
                {"name": "X", "accepted_terms": True, "accepted_privacy": True}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)

    @override_settings(DISCOVER_RATE_LIMIT=1)
    def test_the_map_itself_is_unaffected(self):
        # Locking a visitor out of the site list because they panned the
        # discovery layer would be far worse than the flood.
        self._fetch()
        self.assertEqual(self._fetch().status_code, 429)
        self.assertEqual(self.client.get(reverse("api-sites")).status_code, 200)

    @override_settings(DISCOVER_RATE_LIMIT=1, TRUSTED_PROXY_DEPTH=1)
    def test_a_second_address_has_its_own_budget(self):
        first = {"headers": {"x-forwarded-for": "203.0.113.7"}}
        second = {"headers": {"x-forwarded-for": "198.51.100.9"}}
        self.assertEqual(self._fetch(**first).status_code, 200)
        self.assertEqual(self._fetch(**first).status_code, 429)
        self.assertEqual(self._fetch(**second).status_code, 200)


@override_settings(OVERPASS_URL=MIRROR, DISCOVER_MIN_ZOOM=12)
class RankingTests(_Base):
    """
    What a dense viewport loses when it cannot show everything.

    `out ... N` truncates the union arbitrarily -- Overpass has no idea which
    arm we care about. In a city bbox the `wikidata=*` arm alone matches
    railway stations, hospitals and colleges, so an arbitrary cut could drop
    genuine `heritage` sites to make room for a hospital. The fix is to ask the
    mirror for more than we keep and choose the casualties here.
    """

    def _fetch(self, *elements):
        stub = _Stub(*elements)
        with patch("requests.post", stub):
            response = self._get()
        return self._body(response), stub

    def test_more_is_requested_from_the_mirror_than_is_returned(self):
        _, stub = self._fetch(_element())
        asked = int(re.search(r"out center tags (\d+);", stub.queries[0]).group(1))
        self.assertGreater(asked, DISCOVERY_MAX_RESULTS)

    def test_weaker_evidence_is_what_gets_dropped(self):
        """A hospital tagged `wikidata` must not displace a heritage site."""
        noise = [
            _element(osm_id=i, name=f"Hospital {i}", wikidata=f"Q{i}")
            for i in range(DISCOVERY_MAX_RESULTS + 50)
        ]
        gem = _element(osm_id=999999, name="Rani Gumpha", heritage="2")
        # Last in the mirror's output, so an arbitrary cut would lose it.
        body, _ = self._fetch(*noise, gem)

        self.assertEqual(len(body["results"]), DISCOVERY_MAX_RESULTS)
        names = [r["name"] for r in body["results"]]
        self.assertIn("Rani Gumpha", names)
        self.assertEqual(body["results"][0]["kind"], "heritage")

    def test_truncation_is_declared(self):
        """
        Otherwise it is a lie by omission: the client cannot tell a viewport
        holding forty sites from one holding four hundred.
        """
        many = [
            _element(osm_id=i, name=f"Temple {i}", historic="ruins")
            for i in range(DISCOVERY_MAX_RESULTS + 1)
        ]
        body, _ = self._fetch(*many)
        self.assertTrue(body["truncated"])

    def test_a_viewport_that_fits_is_not_declared_truncated(self):
        body, _ = self._fetch(_element(heritage="2"))
        self.assertFalse(body["truncated"])

    def test_quiet_failures_are_not_truncated(self):
        with override_settings(OVERPASS_URL=""):
            body = self._body(self._get())
        self.assertEqual(body["reason"], "unconfigured")
        self.assertFalse(body["truncated"])

    def test_ordering_is_stable_within_an_arm(self):
        """Ranking reorders arms, not the mirror's own order inside one."""
        body, _ = self._fetch(
            _element(osm_id=1, name="A", historic="ruins"),
            _element(osm_id=2, name="B", historic="ruins"),
            _element(osm_id=3, name="C", historic="ruins"),
        )
        self.assertEqual([r["name"] for r in body["results"]], ["A", "B", "C"])
