"""
Sites: the list (with its catalogue cache and ETag), the detail, categories
and documents, and the site serializer the map is drawn from.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache

import bleach
from django.conf import settings
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db import connection
from django.db.models import Count, Exists, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.utils import translation
from django.views.decorators.http import require_GET

from tirtha.models import (
    ARK,
    THUMBNAIL_DERIVATIVES,
    Category,
    Contribution,
    Image,
    Run,
    Site,
    derivative_path,
)
from tirtha.turntable import find_turntable

from .common import (
    API_VERSION,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    _absolute,
    _conditional,
    _conditional_bytes,
    _etag,
    _not_found,
    _published_runs,
    _visible_runs,
    _visible_sites,
)
from .runs import serialize_run


def annotate_map_status(queryset):
    """
    Attach `model_count` and `has_visible_runs` to a `Site` queryset.

    The map colours a pin by what a visitor can do with the site, and a
    clustered map (internal design notes) needs that for every site in the cluster at
    once -- so it has to come off the list query, not from a follow-up per row.
    Both annotations are correlated subqueries rather than a join with
    `Count(filter=...)`: the list already joins `categories` when `?category=`
    is given, and a second multi-valued join would either fan the rows out or
    force a `DISTINCT` over the whole payload. Subqueries cost one scalar each
    and leave the row count alone, so the list stays at a constant three
    queries no matter how many sites exist.

    Both are built from `_published_runs` / `_visible_runs`, which is the point:
    `_map_status` falls back to those same two querysets when an object arrives
    unannotated, so the two paths cannot drift apart.
    """
    published = (
        _published_runs()
        .filter(site=OuterRef("pk"))
        # Run.Meta.ordering is ["-started_at"], which would otherwise land in
        # the GROUP BY of the aggregate subquery.
        .order_by()
        .values("site")
        .annotate(total=Count("pk"))
        .values("total")
    )
    # The image count, for the same reason: `_map_status` needs to tell a site
    # nobody has photographed from one that has been photographed and never
    # reconstructed, and those are different things to ask of a visitor.
    images = (
        # Not an upload still arriving (batched uploads, `finalized_at`).
        Image.objects.filter(
            contribution__site=OuterRef("pk"), contribution__finalized_at__isnull=False
        )
        .order_by()
        .values("contribution__site")
        .annotate(total=Count("pk"))
        .values("total")
    )
    return queryset.annotate(
        model_count=Coalesce(
            Subquery(published, output_field=IntegerField()),
            0,
        ),
        has_visible_runs=Exists(_visible_runs().filter(site=OuterRef("pk"))),
        image_count=Coalesce(Subquery(images, output_field=IntegerField()), 0),
    )


def _map_status(site) -> tuple[str, int]:
    """
    What the map should say about this site, and how many models it has.

    Four values, and each one is a different thing to offer the visitor:

    * ``published``    -- at least one run they can open now.
    * ``in_progress``  -- runs exist, none of them published yet.
    * ``awaiting_run`` -- enough photographs, and nothing has been reconstructed
      from them. Contributing more does not help; running the pipeline does.
    * ``needs_images`` -- too few photographs to reconstruct from. This is where
      contributing helps.

    The fourth exists because the other three made the interface lie. Four sites
    were labelled ``needs_images`` -- "needs images -- contribute here" -- while
    holding 278, 293, 301 and 541 photographs each, which is eleven to
    twenty-one times `MESHOPS_MIN_IMAGES`. Three of them have **no runs at all**
    and the fourth has three `Archived` runs that are hidden. The database holds
    **zero** `Error` runs, so nothing has failed; the work simply has not been
    done, and asking those contributors for more photographs was the one
    response that could not help.

    **Not** `Site.completed`, which means "no longer accepting images"
    (`models.py`) and says nothing about whether a model exists.

    "Runs" here means *visible* runs. A hidden run is hidden at every endpoint
    (module docstring), and an `Error` run is one `_visible_runs` already
    refuses to serve; a site whose only run is hidden or errored genuinely has
    nothing to show and more images is the honest thing to ask for.

    Uses the annotations from `annotate_map_status` when they are present and
    recomputes from the same querysets when they are not -- `site_detail`
    fetches a single row and does not pay for the subqueries.
    """
    model_count = getattr(site, "model_count", None)
    has_runs = getattr(site, "has_visible_runs", None)
    image_count = getattr(site, "image_count", None)
    if model_count is None or has_runs is None:
        model_count = _published_runs(site).count()
        has_runs = model_count > 0 or _visible_runs(site).exists()
    if image_count is None:
        image_count = Image.objects.filter(
            contribution__site=site, contribution__finalized_at__isnull=False
        ).count()
    if model_count:
        return "published", model_count
    if has_runs:
        return "in_progress", 0
    minimum = getattr(settings, "MESHOPS_MIN_IMAGES", 25)
    if image_count >= minimum:
        return "awaiting_run", 0
    return "needs_images", 0


def _derivative_urls(request, site) -> dict:
    """
    The small copies of the thumbnail, offered only where they exist on disk.

    Derived by convention from the thumbnail's own path rather than stored on
    new fields, so there is no migration and nothing to keep in step -- but that
    means the file may simply not be there yet, on a site saved before the
    derivatives existed and not yet backfilled. A missing key is the honest
    answer; the client falls back to the full thumbnail.
    """
    if not site.thumbnail:
        return {}

    urls = {}
    for kind in THUMBNAIL_DERIVATIVES:
        name = derivative_path(site.thumbnail.name, kind)
        try:
            if not default_storage.exists(name):
                continue
            url = default_storage.url(name)
        except (ValueError, NotImplementedError, OSError):
            continue
        urls[f"thumbnail_{kind}"] = (
            request.build_absolute_uri(url) if request is not None else url
        )

    # The turntable, on the same terms. Not a `THUMBNAIL_DERIVATIVES` entry
    # because that dict is fed to Pillow and this is an MP4 rendered from the
    # model itself; offered only where it exists, which for now is nowhere
    # until the backfill is run.
    turntable = find_turntable(site.thumbnail.name, default_storage)
    if turntable:
        try:
            url = default_storage.url(turntable)
            urls["thumbnail_turntable"] = (
                request.build_absolute_uri(url) if request is not None else url
            )
            # The client needs to know whether to build a `<video>` or an
            # `<img>`: a GIF in a `<video>` renders nothing at all, silently.
            urls["thumbnail_turntable_kind"] = (
                "video" if turntable.endswith((".mp4", ".webm")) else "image"
            )
        except (ValueError, NotImplementedError, OSError):
            pass

    return urls


# What a curated site description may contain.
#
# 27 of the 59 descriptions hold real markup -- paragraphs, ordered lists, and
# links out to Wikipedia and Google Maps. React escapes by default, so a visitor
# was reading `</p> <ol> <li><a href=...>` as text.
#
# Sanitised **here** rather than in the client. The API has more than one
# consumer -- the React app, the embed route, an oEmbed card, anything a
# researcher writes against it -- and "every client must remember to sanitise"
# is a rule that holds until the first one forgets. A description is
# operator-curated, not visitor-submitted, which lowers the odds of anything
# hostile but does not change where the guarantee belongs.
DESCRIPTION_TAGS = ["p", "br", "em", "i", "strong", "b", "ul", "ol", "li", "a"]
DESCRIPTION_ATTRS = {"a": ["href", "title"]}
DESCRIPTION_PROTOCOLS = ["http", "https", "mailto"]


@lru_cache(maxsize=512)
def _sanitise_description(text: str) -> str:
    """
    The expensive half, memoised on the text itself.

    Measured: bleach is **66% of the wall time** of `/api/v1/sites/` at 34
    visible sites (19.9 ms of 30.2) and 76% at 286 (166 of 219) -- 0.59 ms per
    description, paid for every site on every request. And paid again in full on
    a 304, because the ETag is a hash of the rendered body, so the conditional
    response does all the work and then sends none of it.

    Keyed on the description rather than the site id: the same text always
    sanitises to the same markup, an edit is a different key, and nothing has to
    be invalidated. 512 entries covers the archive many times over; the bound
    exists so a pathological import cannot grow it without limit.

    Reusing one `Linker` instead of constructing one per call was measured and
    saves almost nothing (34.7 -> 31.9 ms for 59) -- the cost is in the parsing,
    so caching is the fix, not reuse.

    `strip=True` drops a disallowed tag and keeps its text, so a `<script>` in a
    description loses the tag and its body is escaped rather than the paragraph
    around it disappearing.

    Every link is forced to `rel="noopener noreferrer"` and `target="_blank"`:
    these point off-site by definition, and `noopener` is what stops the
    destination reaching back through `window.opener`.
    """
    cleaned = bleach.clean(
        text,
        tags=DESCRIPTION_TAGS,
        attributes=DESCRIPTION_ATTRS,
        protocols=DESCRIPTION_PROTOCOLS,
        strip=True,
    )
    return bleach.linkifier.Linker(
        callbacks=[
            lambda attrs, new: {
                **attrs,
                (None, "rel"): "noopener noreferrer",
                (None, "target"): "_blank",
            }
        ],
        skip_tags=["pre", "code"],
        parse_email=False,
    ).linkify(cleaned)


# A caretaker's note, not a description. 13 of 34 sites carry exactly this, and
# it was published as the whole of their Description section -- so a visitor
# reading about Konark was shown "[To be updated]" and nothing else.
#
# Masked at the serializer rather than edited in the database: the note is
# meaningful to an operator looking at the admin, and the moment somebody writes
# real text it appears with no code change. Anchored and whitespace-tolerant so
# it cannot swallow a description that merely mentions the phrase.
#
# `Placeholder Text` is in this list because it is the *model's own default*
# (`Site.description`, models.py). The pattern matched only "[to be updated]"
# variants, so the one string almost every site actually carries went straight
# through: 34 of 35 visible sites were publishing the words "Placeholder Text"
# to visitors, in the panel and in the API, which is the exact outcome this
# mask was written to prevent.
_PLACEHOLDER_DESCRIPTION = re.compile(
    r"^\s*(?:\[?\s*to\s+be\s+updated\s*\.?\s*\]?|placeholder(?:\s+text)?)\s*$",
    re.I,
)


def _is_placeholder(text: str) -> bool:
    return bool(text) and bool(_PLACEHOLDER_DESCRIPTION.match(text))


def _public_description(text: str) -> str:
    """The description, or nothing if it is only a note to ourselves."""
    return "" if _is_placeholder(text) else (text or "")


def _description_html(text: str) -> str:
    """The description as markup a browser may render."""
    if not text or _is_placeholder(text):
        return ""
    return _sanitise_description(text)


def serialize_category(category: Category) -> dict:
    return {
        "slug": category.slug,
        "name": category.name,
        "icon": category.icon,
        "description": category.description,
    }


def serialize_site(site: Site, request, *, detail: bool = False) -> dict:
    map_status, model_count = _map_status(site)
    data = {
        "id": site.ID,
        # The display slug, not an identifier: routing is on `id`, which never
        # changes, and a stale slug redirects (§2.2).
        "slug": site.verbose_id,
        "name": site.name,
        "description": _public_description(site.description),
        # The same text, sanitised, for a client that renders markup. The plain
        # field stays exactly as stored -- a consumer that wants text should not
        # have to un-render HTML to get it.
        "description_html": _description_html(site.description),
        "country": site.country,
        "state": site.state,
        "district": site.district,
        "status": site.status,
        "completed": site.completed,
        # What the map pin means: `published` | `in_progress` | `needs_images`
        # (see `_map_status`). Category drives the icon glyph, this drives the
        # colour -- one channel each, never both (internal design notes). Distinct from
        # `completed`, which is only whether the site still takes images.
        "map_status": map_status,
        # Published runs, for "12 sites - 8 published - 19 models".
        "model_count": model_count,
        "thumbnail": _absolute(request, site.thumbnail),
        **_derivative_urls(request, site),
        "preview": _absolute(request, site.preview),
        "location": (
            {
                "latitude": site.latitude,
                "longitude": site.longitude,
                # Provenance travels with the coordinate: OSM-derived values
                # carry ODbL share-alike obligations that self-collected and
                # CC0 ones do not (§5).
                "source": site.location_source,
                "verified_at": site.location_verified_at,
                "zoom": site.default_zoom,
                "bearing": site.default_bearing,
                "pitch": site.default_pitch,
            }
            if site.latitude is not None and site.longitude is not None
            else None
        ),
        "categories": [c.slug for c in site.categories.all()],
    }
    if detail:
        data["links"] = {
            "wikidata": (
                f"https://www.wikidata.org/wiki/{site.wikidata_qid}"
                if site.wikidata_qid
                else None
            ),
            "osm": (
                f"https://www.openstreetmap.org/{site.osm_type}/{site.osm_id}"
                if site.osm_type and site.osm_id
                else None
            ),
        }
        # `4 + 3R` before this: `serialize_run` reached for `run.ark`,
        # `run.images.count()` and `run.contributors.all()` per run, so a site
        # with six runs cost 22 queries. Constant now, and it matters before
        # unit 5.7 rather than after -- continual reconstruction is the unit
        # whose entire point is that runs-per-site grows without bound.
        data["runs"] = [
            serialize_run(run, request)
            for run in (
                _visible_runs(site)
                .select_related("ark")
                .prefetch_related("contributors")
                .annotate(_image_count=Count("images"))
                .order_by("-ended_at")
            )
        ]
    return data


DEFAULT_API_SITES_CACHE_SECONDS = 60
_SITES_CACHE_PREFIX = "tirtha:api:sites:v1"


def _catalogue_version() -> str:
    """
    A cheap stamp that moves whenever the site list could have changed.

    One round trip of scalar aggregates over the tables `serialize_site` reads:

    * `Site` -- count, newest `updated_at`, and the hidden count. The last
      because a bulk `queryset.update(hidden=...)` skips `auto_now`; the admin
      actions now set `updated_at` themselves, but a shell or another code path
      might not.
    * `Run` -- count, newest `started_at`/`ended_at`, and the hidden, `Error`
      and ARK-bearing counts, which are what `map_status`/`model_count` read.
      `Run` has no `updated_at`, so these stand in for one.
    * `ARK` -- count and newest `created_at` (minting publishes a model).
    * `Contribution` and `Image` counts -- `awaiting_run` vs `needs_images`
      turns on the image count.
    * `Category` count, and the site/category link count.

    It cannot see every edit -- a run's `status` flipping between two non-Error
    values, a category renamed, a derivative written to disk. Those are what
    the short TTL (`API_SITES_CACHE_SECONDS`) is for: they appear within a
    minute, and everything the stamp does see appears on the next request.
    Keying on a stamp instead of deleting on save is what lets this work
    without a signal: nothing has to remember to invalidate it.
    """
    run_ark = Run._meta.get_field("ark").column
    site_categories = Site.categories.through._meta.db_table
    tables = {
        "site": Site._meta.db_table,
        "run": Run._meta.db_table,
        "ark": ARK._meta.db_table,
        "contribution": Contribution._meta.db_table,
        "image": Image._meta.db_table,
        "category": Category._meta.db_table,
    }
    q = connection.ops.quote_name
    sql = (
        "SELECT "
        f"(SELECT COUNT(*) FROM {q(tables['site'])}), "
        f"(SELECT MAX({q('updated_at')}) FROM {q(tables['site'])}), "
        f"(SELECT COUNT(*) FROM {q(tables['site'])} WHERE {q('hidden')} = %s), "
        f"(SELECT COUNT(*) FROM {q(tables['run'])}), "
        f"(SELECT MAX({q('started_at')}) FROM {q(tables['run'])}), "
        f"(SELECT MAX({q('ended_at')}) FROM {q(tables['run'])}), "
        f"(SELECT COUNT(*) FROM {q(tables['run'])} WHERE {q('hidden')} = %s), "
        f"(SELECT COUNT(*) FROM {q(tables['run'])} WHERE {q('status')} = %s), "
        f"(SELECT COUNT({q(run_ark)}) FROM {q(tables['run'])}), "
        f"(SELECT COUNT(*) FROM {q(tables['ark'])}), "
        f"(SELECT MAX({q('created_at')}) FROM {q(tables['ark'])}), "
        f"(SELECT COUNT(*) FROM {q(tables['contribution'])}), "
        f"(SELECT COUNT(*) FROM {q(tables['image'])}), "
        f"(SELECT COUNT(*) FROM {q(tables['category'])}), "
        f"(SELECT COUNT(*) FROM {q(site_categories)})"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, [True, True, "Error"])
        row = cursor.fetchone()
    return hashlib.sha256(repr(row).encode()).hexdigest()[:24]


def _sites_cache_seconds() -> int:
    return int(
        getattr(settings, "API_SITES_CACHE_SECONDS", DEFAULT_API_SITES_CACHE_SECONDS)
    )


def _sites_cache_key(request, version: str) -> str:
    """
    Everything the rendered body depends on besides the database.

    The host and scheme because every URL in the payload is absolute
    (`build_absolute_uri`); the language because `name`/`description` are
    translated fields; the query string because it filters and pages.
    """
    variant = "|".join(
        (
            request.scheme,
            request.get_host(),
            translation.get_language() or "",
            request.GET.urlencode(),
        )
    )
    digest = hashlib.sha256(variant.encode()).hexdigest()[:24]
    return f"{_SITES_CACHE_PREFIX}:{version}:{digest}"


@require_GET
def sites(request):
    """
    List sites. Supports `?category=`, `?q=`, `?located=1`, and paging.

    **Cached, rendered, with its ETag** (review 2026-11-22 7). Measured at
    60 ms per request and 55 ms for a 304, because the conditional answer
    still built the whole payload -- three queries, bleach, and 2+N
    filesystem checks per site for the thumbnail derivatives and turntable --
    only to hash it and send nothing. The rendered bytes and their ETag now
    sit in the Django cache under a key that includes `_catalogue_version`,
    so a hit (200 or 304) costs one aggregate query and a cache read, and any
    change the stamp can see is a miss on the very next request.

    `API_SITES_CACHE_SECONDS` (default 60) bounds how long a change the stamp
    cannot see is served stale; 0 disables the cache.
    """
    ttl = _sites_cache_seconds()
    if ttl <= 0:
        return _sites_response(request)
    key = _sites_cache_key(request, _catalogue_version())
    cached = cache.get(key)
    if cached is None:
        response = _sites_response(request)
        if response.status_code != 200:
            return response
        cached = (response.content, _etag(response.content))
        cache.set(key, cached, ttl)
    content, etag = cached
    return _conditional_bytes(request, content, etag)


def _sites_response(request):
    queryset = annotate_map_status(_visible_sites().prefetch_related("categories"))

    category = request.GET.get("category")
    if category:
        queryset = queryset.filter(categories__slug=category)

    query = (request.GET.get("q") or "").strip()
    if query:
        queryset = queryset.filter(name__icontains=query)

    if request.GET.get("located") in ("1", "true"):
        # What the map needs: a site with no coordinate cannot be drawn.
        queryset = queryset.exclude(latitude__isnull=True)

    try:
        # Clamped at **both** ends. `min()` alone caps the ceiling, so
        # `?page_size=-1` reached `queryset[0:-1]` and Django raises
        # "Negative indexing is not supported" -- an unauthenticated 500 on an
        # endpoint with no throttle, which on production also means one email to
        # ADMINS per request.
        page_size = max(
            1, min(int(request.GET.get("page_size", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
        )
        page = max(int(request.GET.get("page", 1)), 1)
    except (TypeError, ValueError):
        return JsonResponse(
            {"error": "page and page_size must be integers."}, status=400
        )

    total = queryset.count()

    # The offset is bounded, not the page number.
    #
    # `page_size` was clamped at both ends a few lines up for exactly this
    # reason; `page` was clamped only at the bottom, so `(page - 1) * page_size`
    # handed the database an offset past the 64-bit boundary and it raised.
    # `?page=99999999999999999999` was a 500 with an HTML body -- anonymous,
    # unthrottled, and breaking the rule that this API always answers in JSON.
    #
    # Python integers are arbitrary precision, so the multiplication itself is
    # fine; capping it at `total` before it reaches the driver is the whole fix.
    # A page past the end then returns an empty list, which is what a client
    # walking pages expects as its terminator -- clamping `page` instead would
    # hand someone who asked for page 999 the last page and call it page 999.
    start = min((page - 1) * page_size, total)
    rows = queryset.order_by("name")[start : start + page_size]

    return _conditional(
        request,
        {
            "version": API_VERSION,
            "count": total,
            "page": page,
            "page_size": page_size,
            "results": [serialize_site(s, request) for s in rows],
        },
    )


@require_GET
def site_detail(request, site_id: str):
    # Deliberately *not* annotated: this is one row, the detail payload already
    # walks the site's runs, and `_map_status` recomputes from the same
    # querysets the annotation is built from. The fallback path is therefore
    # exercised in production rather than being code only tests reach.
    site = _visible_sites().prefetch_related("categories").filter(ID=site_id).first()
    if site is None:
        # A JSON 404, not Django's HTML error page: a client that asked for
        # JSON and has to parse an HTML template to discover the resource is
        # missing has been handed a second problem.
        return _not_found("site", site_id)
    return _conditional(request, serialize_site(site, request, detail=True))


@require_GET
def categories(request):
    # Conditional like the other three read endpoints the frontend hits on
    # every page load. Categories in particular change perhaps twice a year and
    # are fetched on every visit, so almost every fetch of them is a 304.
    return _conditional(
        request,
        {
            "version": API_VERSION,
            "results": [serialize_category(c) for c in Category.objects.all()],
        },
    )


@require_GET
def document(request, name: str):
    """
    One of the canonical documents, as markup the app can show in place
    (unit 6.9).

    **The document, not a description of it.** `shell.document` exists because
    the React sidebar had grown its own paraphrases of the Terms and the Privacy
    Policy, and the request form then asked people to consent to those -- a
    rewritten legal document presented as the original is a second document
    nobody wrote. Serving the text in a dialog must not quietly reintroduce
    that, so this renders the same templates through the same `_opened` helper
    that the standalone pages use. There is one copy on disk and this is a
    second way of reaching it, not a second version of it.

    The standalone URLs stay. They are what an unfurl describes, what a person
    cites, and what someone who opens the link in a new tab gets; a dialog is
    reachable only from inside the app.

    Untranslated on purpose, and the same way `shell.document` is untranslated:
    these are legal texts nobody has reviewed a translation of. The *title*
    comes from `DOCUMENTS`, which is marked for translation, so the dialog's
    heading follows the interface even while its body does not.
    """
    from ..shell import DOCUMENTS, _opened

    if name not in DOCUMENTS:
        return _not_found("document", name)

    template, title = DOCUMENTS[name]
    return _conditional(
        request,
        {
            "version": API_VERSION,
            "id": name,
            "title": str(title),
            # Rendered markup, from a template in this repository. It is not
            # user input and never has been: the only writers are whoever edits
            # `tirtha/templates/tirtha/*.html`.
            "html": str(_opened(template, request)),
        },
    )
