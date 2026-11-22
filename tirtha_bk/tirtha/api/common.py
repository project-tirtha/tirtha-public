"""
Shared helpers for the JSON API: visibility scopes, pagination and rate-limit
defaults, the per-IP throttle, conditional (ETag) JSON responses, and 404s.
"""

from __future__ import annotations

import hashlib

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.utils.cache import get_conditional_response

from tirtha.models import Run, Site

API_VERSION = "v1"

# A page size, not a limit on what exists. Kept modest because the map fetches
# every site at once and the payload is what the browser pays for.
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

# Defaults for the settings read at call time below. Named here so the value a
# deployment does not configure is still visible in one place.
DEFAULT_SITE_REQUEST_RATE_LIMIT = 5
DEFAULT_SITE_REQUEST_RATE_WINDOW = 3600
DEFAULT_TRUSTED_PROXY_DEPTH = 1

# Uploads (internal design notes). Generous for a person and a hard bound on a script:
# a contributor sends *one* request for a whole batch of photographs, however
# many files it carries, so a human doing real work makes a handful of these an
# hour. `upload` was the one mutating endpoint with no throttle at all, and it
# is by far the most expensive -- it writes files, decodes every image, and can
# hold a gunicorn worker for the length of the transfer.
DEFAULT_UPLOAD_RATE_LIMIT = 20
DEFAULT_UPLOAD_RATE_WINDOW = 600
# Batched uploads count *contributions* against the limit above -- the first
# batch, which creates one -- and every batch request (first, append, finish,
# discard) against this separate, generous one. A 300-photo visit is ~15
# batches, a 1000-file one 50 plus its finish; 60 per window admits that and
# bounds a client that loops.
DEFAULT_UPLOAD_BATCH_RATE_LIMIT = 60
DEFAULT_UPLOAD_BATCH_RATE_WINDOW = 600
# Volume caps on batched uploads (`views._upload_batch`). Per contribution:
# files and bytes as received. Per batch: bytes, though a lone video may exceed
# it, as before. Per contributor: uploads open at once.
DEFAULT_UPLOAD_MAX_FILES_PER_CONTRIBUTION = 1000
DEFAULT_UPLOAD_MAX_BYTES_PER_CONTRIBUTION = 3 * 1024**3
DEFAULT_UPLOAD_MAX_BYTES_PER_BATCH = 300 * 1024**2
DEFAULT_UPLOAD_MAX_OPEN_PER_CONTRIBUTOR = 3


def _visible_sites():
    """Every query starts here. See the module docstring."""
    return Site.objects.exclude(hidden=True)


def _visible_runs(site=None):
    # **`site__hidden` as well as `hidden`.** A run is only as visible as the
    # site it belongs to. Filtering the run alone meant `/api/v1/runs/<id>/`
    # served a non-hidden run on a *hidden* site -- name, slug, and with an ARK
    # present the model file itself -- breaking the invariant this module's
    # docstring opens with. 25 of the 59 production sites are hidden. It needs a
    # run id to reach, which is not enumerable and which no endpoint emits for a
    # hidden site, so it was bounded; it stops being bounded the moment one
    # leaks in a log line or an old link.
    runs = (
        Run.objects.exclude(hidden=True)
        .exclude(site__hidden=True)
        .exclude(status="Error")
    )
    return runs.filter(site=site) if site is not None else runs


def _published_runs(site=None):
    """
    Runs a visitor can open *right now*.

    Visible (`_visible_runs`) **and** holding an ARK. The ARK is what binds a
    run to a published file -- `serialize_run` reads `ark.url` for `file_url`,
    and a run without one has nothing to show whatever its status says. A run
    that is still training, or that finished but has not been minted yet, is
    therefore not published.
    """
    return _visible_runs(site).exclude(ark__isnull=True)


def _client_ip(request) -> str:
    """
    The caller's address, given the proxies actually in front of us.

    nginx terminates every request (`config/tirtha.ssl.nginx.template` includes
    `proxy_params`, which sets `X-Forwarded-For: $proxy_add_x_forwarded_for`) --
    that is *the client's own header with the connecting peer appended*. So the
    rightmost entries are the ones our infrastructure wrote and everything to
    the left of them is attacker-controlled. Taking the leftmost entry, which is
    the usual mistake, would hand a flooder a fresh throttle bucket per request
    for the price of one header.

    `TRUSTED_PROXY_DEPTH` is how many of the rightmost entries are ours: 1 for
    nginx alone, 2 if a CDN is ever put in front of it, 0 for a deployment with
    no reverse proxy at all, where the header must not be believed. A chain
    shorter than the configured depth cannot contain our own hops, so it is
    discarded rather than half-trusted.
    """
    depth = getattr(settings, "TRUSTED_PROXY_DEPTH", DEFAULT_TRUSTED_PROXY_DEPTH)
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if depth > 0 and forwarded:
        chain = [part.strip() for part in forwarded.split(",") if part.strip()]
        if len(chain) >= depth:
            return chain[-depth]
    return request.META.get("REMOTE_ADDR") or "unknown"


def _rate_limited(
    request,
    bucket: str,
    *,
    limit: int | None = None,
    window: int | None = None,
    identity: str | None = None,
) -> JsonResponse | None:
    """
    Per-IP fixed-window throttle over Django's cache framework.

    A JSON 429 when the caller is over the limit, `None` when they are not.

    Django's cache rather than a throttling dependency: this is two counters on
    two endpoints, and `django-ratelimit` or DRF's throttles would be a package
    added to `pyproject.toml` for an `incr`.

    **The window is only as global as the cache backend is.** With the default
    `LocMemCache` the counter lives inside one process, so 16 gunicorn workers
    give an effective limit of 16x the configured one -- still a bound, still
    far below what an unthrottled endpoint allows, but not the number in the
    setting. Point `CACHES` at a shared backend for the exact limit.

    `bucket` separates the counters, and `limit`/`window` are per-caller
    because the two endpoints being throttled are not alike: writing a site
    request is a rare deliberate act, and fetching the discovery layer happens
    once per map pan. One shared budget would either make the map unusable or
    make the write endpoint's limit meaningless.

    The limit is read here rather than at import so `override_settings` reaches
    it; `views.py` binds settings to module-level names at import and cannot be
    overridden in tests, which is a trap this file stays out of (internal design notes).
    """
    if limit is None:
        limit = getattr(
            settings, "SITE_REQUEST_RATE_LIMIT", DEFAULT_SITE_REQUEST_RATE_LIMIT
        )
    if window is None:
        window = getattr(
            settings, "SITE_REQUEST_RATE_WINDOW", DEFAULT_SITE_REQUEST_RATE_WINDOW
        )
    if not limit or limit <= 0:
        return None

    # `identity` where the caller is known, the address otherwise.
    #
    # An address is the only handle an anonymous endpoint has, but it is both
    # too broad and too narrow for an authenticated one: a university or a phone
    # network puts thousands of people behind one address, and one person with a
    # script can move between addresses freely. Where the endpoint already knows
    # *who* is calling, throttle that instead.
    key = f"tirtha:throttle:{bucket}:{identity or _client_ip(request)}"
    try:
        # `add` only writes when the key is absent, so it both starts and dates
        # the window; `incr` never extends it. A caller cannot hold themselves
        # locked out by continuing to hammer the endpoint.
        count = 1 if cache.add(key, 1, window) else cache.incr(key)
    except ValueError:
        # The key expired between the `add` and the `incr`. That is the start
        # of a new window, not an error.
        cache.set(key, 1, window)
        count = 1

    if count > limit:
        response = JsonResponse(
            {
                "error": "Too many requests from this address. Please try again later.",
                "retry_after": window,
            },
            status=429,
        )
        response["Retry-After"] = str(window)
        return response
    return None


def _conditional(request, payload: dict) -> JsonResponse:
    """
    A JSON response carrying an ETag, answering 304 on revalidation.

    An explicit ETag, **not** `ConditionalGetMiddleware`. The middleware is
    global: it would stamp ETags on the admin and on every React shell response
    as well, and it sets no `Cache-Control` at all -- so a browser applies
    heuristic freshness to API JSON and may serve a stale site list without
    revalidating even once. `must-revalidate` with `max-age=0` says the
    opposite: reuse nothing without asking, but asking is cheap. Doing it in the
    view also keeps the header off the write endpoint, where a cached response
    would be wrong, and needs no change to `MIDDLEWARE` in four settings
    templates -- `SecurityHeadersMiddleware` and `ReadOnlyMiddleware` keep their
    ordering, and Django's own 304 helper copies `Cache-Control`, `ETag` and
    `Vary` onto the 304, so the security headers are still applied on the way
    out.

    The ETag is over the rendered body, so it changes exactly when the payload
    does. This saves the bytes, not the query -- and deliberately so. The
    obvious cheaper validator, `Last-Modified` from `max(Site.updated_at)`,
    would be **wrong**: half of what a site now serialises to comes from `Run`
    and `ARK` rows (`map_status`, `model_count`) and from `Category`, and none
    of those touch `Site.updated_at`. Minting an ARK would flip a pin from
    `in_progress` to `published` without moving the validator, and every
    visitor holding a cached copy would keep being told the model is not ready.
    The query it would have saved is three statements.
    """
    response = JsonResponse(payload)
    return _conditional_bytes(request, response.content, _etag(response.content))


def _etag(content: bytes) -> str:
    return f'"{hashlib.sha256(content).hexdigest()[:32]}"'


def _conditional_bytes(request, content: bytes, etag: str) -> HttpResponse:
    """`_conditional` for a body that is already rendered (see `sites`)."""
    response = HttpResponse(content, content_type="application/json")
    response["ETag"] = etag
    response["Cache-Control"] = "public, max-age=0, must-revalidate"
    return get_conditional_response(request, etag=etag, response=response)


def _not_found(kind: str, identifier: str) -> JsonResponse:
    """
    A hidden resource and a non-existent one answer identically.

    Distinguishing them would let anyone enumerate unpublished sites by
    watching which IDs 404 differently.
    """
    return JsonResponse({"error": f"No such {kind}.", "id": identifier}, status=404)


def _absolute(request, file_field) -> str | None:
    if not file_field:
        return None
    try:
        return request.build_absolute_uri(file_field.url)
    except ValueError:
        return None
