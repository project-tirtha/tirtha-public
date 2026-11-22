"""Discovery: OSM heritage sites near the viewport, via Overpass (internal design notes)."""

from __future__ import annotations

import logging
import math

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from tirtha.models import Site
from tirtha.overpass import (
    DISCOVERY_KIND_RANK,
    DISCOVERY_MAX_RESULTS,
    OverpassClient,
    OverpassUnavailable,
    discovery_kind,
)

from .common import API_VERSION, _conditional, _rate_limited

# Named for the package, as it was when this was one `api.py`, so log
# configuration and filtering keyed on `tirtha.api` keep matching.
logger = logging.getLogger("tirtha.api")

# Discovery layer (internal design notes). Defaults for the settings `discover` reads
# at call time, so a deployment that configures none of them still gets a layer
# that behaves.
DEFAULT_DISCOVER_MIN_ZOOM = 12
DEFAULT_DISCOVER_MAX_BBOX_SPAN = 2.0
DEFAULT_DISCOVER_CACHE_SECONDS = 900
DEFAULT_DISCOVER_RATE_LIMIT = 240
DEFAULT_DISCOVER_RATE_WINDOW = 3600

# **Required in every discovery response** (internal design notes). The layer is live,
# unpinned OSM data passing through this process on its way to a browser, and
# ODbL's attribution obligation attaches to it -- unlike a site coordinate,
# where the obligation is recorded once in `location_source="osm"` and the
# credit is rendered from the row. There is no row here to hang provenance on,
# so the credit travels in the payload, and the frontend has nothing else to
# render it from.
OSM_ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"

# The grid the viewport is snapped to before it becomes a cache key, in
# degrees. ~5.5 km, chosen against the minimum zoom rather than picked round:
# a web-mercator tile at z12 is 360/2**12 = 0.088 deg, so one grid cell is
# smaller than a tile and the snapped box is never wildly larger than what was
# asked for, while a desktop viewport at z12 (~0.7 deg across) still covers
# more than a dozen cells and a pan of a few hundred metres almost always lands
# in the same one.
#
# Without this every pixel of panning is a distinct key, which means a fresh
# mirror query *and* a fresh cache entry that will never be read again -- a
# cache that only ever misses, and costs memory to do it.
DISCOVER_BBOX_GRID = 0.05


# ------------------------------------------------------ discovery (internal design notes)


def _parse_bbox(raw: str, max_span: float) -> tuple[float, float, float, float]:
    """
    `west,south,east,north` in degrees, or `ValueError` with a reason.

    The order is MapLibre's `getBounds().toArray().flat()`, which is what the
    caller has to hand; Overpass wants the transposition and gets it in
    `overpass.discovery_ql`, once, where it can be checked.

    A bad bbox is the one discovery failure that is a **400 rather than an
    empty 200**. The three documented empty answers -- `unconfigured`, `zoom`,
    `unavailable` -- are all statements about our side, and the map degrades
    quietly because none of them are the client's doing. An inverted or
    unparseable bbox is a bug in the caller, and answering it with a cheerful
    empty layer would hide that bug for as long as it took someone to notice
    the pins never appear.

    The span ceiling is not the same guard as the minimum zoom, which is why
    both exist. Zoom is a claim the client makes about itself and nothing binds
    it to the bbox: `zoom=14&bbox=68,6,98,36` passes the zoom check and asks
    the mirror for every heritage element in India.
    """
    parts = [part.strip() for part in (raw or "").split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be four numbers: west,south,east,north.")
    try:
        west, south, east, north = (float(part) for part in parts)
    except ValueError:
        raise ValueError("bbox must be four numbers: west,south,east,north.")
    if not all(math.isfinite(v) for v in (west, south, east, north)):
        # nan slips past every comparison below silently -- all of them are
        # False -- so it has to be rejected before them, not by them.
        raise ValueError("bbox must be four finite numbers.")
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError("bbox longitudes must be between -180 and 180.")
    if not (-90 <= south <= 90 and -90 <= north <= 90):
        raise ValueError("bbox latitudes must be between -90 and 90.")
    if west >= east or south >= north:
        # Also the antimeridian case. A viewport crossing +/-180 would need to
        # be split into two queries, and the extract is one country nowhere
        # near it, so refusing is honest where silently querying the inverse of
        # the world would not be.
        raise ValueError("bbox must have west < east and south < north.")
    if (east - west) > max_span or (north - south) > max_span:
        raise ValueError(
            f"bbox is too large; each side may span at most {max_span} degrees."
        )
    return west, south, east, north


def _bbox_cell(bbox) -> tuple[int, int, int, int]:
    """
    The grid cell indices a viewport snaps out to (see `DISCOVER_BBOX_GRID`).

    Snapped **outward** -- floor the south-west corner, ceil the north-east --
    so the snapped box always contains the requested one. The query and the
    cache entry are both for the snapped box, which is the part that makes this
    a cache rather than a key-mangling scheme: a pan that stays inside the cell
    is answered from a payload that already covers where the visitor now is. If
    the snapped box were only used for the key and the raw box for the query,
    the second pan would be served results that stop at the first pan's edge.

    The cost is up to one grid cell of overshoot per side, so a query may cover
    slightly more than `DISCOVER_MAX_BBOX_SPAN`. That is bounded and small; the
    ceiling is there to refuse a query over a country, not to be exact.
    """
    west, south, east, north = bbox
    grid = DISCOVER_BBOX_GRID
    # The epsilon is not decoration. `85.80 / 0.05` is 1715.9999999999998 in
    # binary floating point, so a bbox edge sitting exactly on a grid line
    # floors to the cell *below* it and the query silently widens by a whole
    # cell on that side -- and by two, on the two viewports out of three where
    # it happens to both corners. 1e-9 of a cell is 5e-11 degrees, some five
    # microns on the ground, which is far below any coordinate this handles and
    # far above the error being corrected.
    eps = 1e-9
    return (
        math.floor(west / grid + eps),
        math.floor(south / grid + eps),
        math.ceil(east / grid - eps),
        math.ceil(north / grid - eps),
    )


def _cell_bbox(cell) -> tuple[float, float, float, float]:
    return tuple(index * DISCOVER_BBOX_GRID for index in cell)


def _discover_cache_key(cell) -> str:
    """
    One key per grid cell.

    **Zoom is deliberately not part of the key.** What comes back depends only
    on the bbox -- the tag union has no zoom-dependent arm -- so zooming in and
    out over the same ground would otherwise miss the cache on every step for
    an answer already sitting in it. The zoom threshold is a gate on whether we
    query at all, not an input to the query.

    Integer cell indices rather than formatted floats, so the key is exact and
    cannot depend on how a float rounds when it is printed.
    """
    return "tirtha:discover:{}:{}:{}:{}:{}".format(API_VERSION, *cell)


def _discovery_element(candidate) -> dict:
    return {
        "osm_type": candidate.osm_type,
        # A string. OSM ids are well inside the range a JS number holds today,
        # but they are identifiers rather than quantities: nothing should be
        # arithmetic on one, and a client that round-trips it through JSON must
        # get back what it was sent.
        "osm_id": str(candidate.osm_id),
        "name": candidate.name,
        "latitude": candidate.latitude,
        "longitude": candidate.longitude,
        # "" rather than null when absent: it comes straight from a tag, and a
        # stable type saves every caller a null check for a field that is empty
        # far more often than not.
        "wikidata": candidate.wikidata_qid,
        "kind": discovery_kind(candidate.tags),
    }


def _without_our_sites(elements: list[dict]) -> list[dict]:
    """
    Drop anything already matched to a `Site`, in one query.

    The layer's whole claim is that it shows what is *possible* -- a site we
    already hold is not a thing to request, and drawing it twice in two styles
    invites a duplicate `SiteSubmissionRequest` for work that is already in the
    pipeline. So the exclusion is on `osm_type` + `osm_id`, the pair the
    seeding flow writes (internal design notes).

    **One query, not one per result.** A viewport can return 300 elements and
    `Site.objects.filter(osm_id=...).exists()` per element would be 300 round
    trips to save a set membership test. The `__in` pair over-matches -- it is
    a cross product, so a node and a way sharing an id both match the filter --
    and the exact tuple comparison in Python is what makes it correct.

    Every `Site` row counts, including hidden ones. Production hides test rows
    and work in progress (internal design notes), and a site being reconstructed but not
    yet published is the last thing to offer a visitor as unclaimed ground. The
    trade is that the absence of a pin weakly implies a row exists for it,
    which is a far smaller cost than soliciting duplicate requests.
    """
    if not elements:
        return []
    types = {element["osm_type"] for element in elements}
    ids = {int(element["osm_id"]) for element in elements}
    ours = set(
        Site.objects.filter(osm_type__in=types, osm_id__in=ids).values_list(
            "osm_type", "osm_id"
        )
    )
    return [e for e in elements if (e["osm_type"], int(e["osm_id"])) not in ours]


def _rank_and_cap(elements: list[dict]) -> tuple[list[dict], bool]:
    """
    Strongest evidence first, then cut -- and say whether anything was cut.

    Sorting is by which arm of the union matched (`heritage` before `historic`
    before `place_of_worship` before `wikidata`), so what a dense viewport loses
    is the weakest evidence rather than an arbitrary tail. The sort is stable,
    so within an arm the mirror's own order survives.

    `truncated` exists because the alternative is a lie by omission: a client
    cannot otherwise tell a viewport that holds forty heritage sites from one
    that holds four hundred, and "zoom in for more" is only honest advice if the
    client knows there *is* more.
    """
    ranked = sorted(
        elements,
        key=lambda element: DISCOVERY_KIND_RANK.get(element.get("kind", ""), 99),
    )
    return ranked[:DISCOVERY_MAX_RESULTS], len(ranked) > DISCOVERY_MAX_RESULTS


def _discover_response(
    request, min_zoom, results, reason=None, truncated=False
) -> JsonResponse:
    payload = {
        "version": API_VERSION,
        # Echoed so the client knows when it is worth asking again, rather than
        # hard-coding a threshold that then has to match this deployment's.
        "min_zoom": min_zoom,
        "attribution": OSM_ATTRIBUTION,
        "count": len(results),
        "truncated": truncated,
        "results": results,
    }
    if reason:
        payload["reason"] = reason
    return _conditional(request, payload)


DEFAULT_OVERPASS_TIMEOUT = 15


def _discover_timeout() -> int:
    configured = getattr(settings, "OVERPASS_TIMEOUT", None)
    try:
        timeout = (
            int(configured) if configured is not None else DEFAULT_OVERPASS_TIMEOUT
        )
    except (TypeError, ValueError):
        timeout = DEFAULT_OVERPASS_TIMEOUT
    return max(1, timeout)


@require_GET
def discover(request):
    """
    Heritage sites OSM knows about and we do not (internal design notes).

    Turns the map from a browser of what we have into a map of what is
    possible: tap an unreconstructed site, request it, contribute to it.

    **Django proxies; the browser never reaches the mirror.** Overpass stays
    bound to `127.0.0.1:8003` (internal design notes), so this view is the only way to
    it -- which is also why the upstream address never appears in a response.
    `OverpassUnavailable` carries the failing URL in its message for the log,
    and putting that message in the body would publish the internal endpoint to
    anyone who could make the mirror time out.

    **Three failures answer 200 with an empty `results` and a `reason`.** The
    discovery layer is one layer on a working map, and an error status turns a
    missing overlay into an error banner over sites that are rendering fine.
    None of the three are the caller's fault:

    * `unconfigured` -- `OVERPASS_URL` is unset. Not an edge case: Overpass is
      optional infrastructure and dev runs without it, so this is the common
      answer, and it must cost nothing (internal design notes).
    * `zoom` -- below `DISCOVER_MIN_ZOOM`. An all-India heritage query is far
      too much to ship to a browser.
    * `unavailable` -- the mirror errored or timed out. A 15 GB database behind
      a map layer must never be able to 500 the map.

    A malformed bbox is the exception and answers 400; see `_parse_bbox`.
    """
    min_zoom = getattr(settings, "DISCOVER_MIN_ZOOM", DEFAULT_DISCOVER_MIN_ZOOM)

    # Before validation, like `request_site`, and for a sharper reason: this
    # endpoint proxies to a 15 GB database, and the cache in front of it only
    # absorbs repeats of the *same* cell. A caller walking the grid misses it
    # every time. The budget is its own (`bucket="discover"`) and far larger
    # than the write endpoint's, because one visitor panning a map legitimately
    # spends dozens of these in a session.
    throttled = _rate_limited(
        request,
        "discover",
        limit=getattr(settings, "DISCOVER_RATE_LIMIT", DEFAULT_DISCOVER_RATE_LIMIT),
        window=getattr(settings, "DISCOVER_RATE_WINDOW", DEFAULT_DISCOVER_RATE_WINDOW),
    )
    if throttled is not None:
        return throttled

    try:
        zoom = int(request.GET["zoom"])
    except (KeyError, TypeError, ValueError):
        return JsonResponse({"error": "zoom must be an integer."}, status=400)

    try:
        bbox = _parse_bbox(
            request.GET.get("bbox", ""),
            getattr(settings, "DISCOVER_MAX_BBOX_SPAN", DEFAULT_DISCOVER_MAX_BBOX_SPAN),
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    # The timeout is stated rather than inherited, and never above
    # `OVERPASS_TIMEOUT`. This runs on a gunicorn worker serving a map pan:
    # every second the mirror takes is a second that worker serves nobody, so
    # the bound has to be the deployment's (15 s on production), not whatever
    # default `OverpassClient` happens to carry.
    client = OverpassClient(timeout=_discover_timeout())
    # `unconfigured` is checked before `zoom`, and the order is a decision:
    # where there is no mirror, no amount of zooming will ever produce a pin,
    # and answering "zoom in" would be advice that cannot work.
    if not client.configured:
        return _discover_response(request, min_zoom, [], reason="unconfigured")
    if zoom < min_zoom:
        return _discover_response(request, min_zoom, [], reason="zoom")

    cell = _bbox_cell(bbox)
    key = _discover_cache_key(cell)
    # Django's cache framework, no new dependency.
    #
    # NOTE: `CACHES` is unconfigured on every deployment here, so this is
    # per-process `LocMemCache` -- 16 gunicorn workers hold 16 independent
    # copies, and the hit rate is what one worker's share of the traffic
    # earns. Still worth having (a visitor's requests are not spread evenly and
    # panning repeats cells), still not what a shared backend would give. The
    # same caveat is recorded on the site-request throttle in `_rate_limited`,
    # and one shared backend fixes both.
    elements = cache.get(key)
    if elements is None:
        try:
            elements = [
                _discovery_element(candidate)
                for candidate in client.discover(_cell_bbox(cell))
            ]
        except OverpassUnavailable:
            # Logged with the exception, which names the upstream URL --
            # server-side, where an operator needs it, and nowhere else.
            logger.warning("Overpass discovery failed for %s", key, exc_info=True)
            return _discover_response(request, min_zoom, [], reason="unavailable")
        cache.set(
            key,
            elements,
            getattr(settings, "DISCOVER_CACHE_SECONDS", DEFAULT_DISCOVER_CACHE_SECONDS),
        )

    # Cached *before* the exclusion, deliberately. What is expensive is the
    # mirror; what changes is which elements are ours. Caching the filtered
    # list would keep showing a discovery pin for a site an operator matched
    # ten minutes ago, for the rest of the entry's life.
    # Ranked and capped *after* the exclusion, so removing sites we already
    # hold makes room for the next-best evidence instead of leaving a gap.
    results, truncated = _rank_and_cap(_without_our_sites(elements))
    return _discover_response(request, min_zoom, results, truncated=truncated)
