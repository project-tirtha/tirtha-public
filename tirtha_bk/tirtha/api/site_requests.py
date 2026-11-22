"""Site requests: anonymous submissions for a new heritage site (unit 6.6)."""

from __future__ import annotations

import json

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from tirtha.models import SiteSubmissionRequest

from .common import _rate_limited

# A site request is a form's worth of short strings; see `request_site`.
DEFAULT_SITE_REQUEST_MAX_BODY_BYTES = 64 * 1024


def _text(value, default: str = "") -> str:
    """
    One text field out of an untrusted JSON body.

    `(payload.get("name") or "")` assumes the value is a string; JSON promises
    nothing of the sort. A client that sends a number or an object for `name` --
    a buggy one, not only a hostile one -- reached `.strip()` on a dict and the
    view raised AttributeError, which is a 500 on an anonymous endpoint. Reading
    a non-string as unanswered is what a reviewer would make of it anyway, and
    for `name` it lands on the "a site name is required" 400 the client can act
    on.
    """
    return value if isinstance(value, str) and value else default


@require_POST
def request_site(request):
    """
    Submit a request for a new heritage site (internal design notes).

    Replaces the Google Form. Every field the form collects is accepted here,
    because those were live questions answered by real requesters -- dropping
    any of them would be a regression dressed up as a migration.

    Anonymous submission is allowed. Requiring an account to *ask* for a site
    would suppress exactly the local knowledge this is trying to reach; the
    review step is where scrutiny belongs.

    Which is precisely why it is throttled per IP (`_rate_limited`). Anonymous
    and unauthenticated is a decision, not an oversight, but it leaves the only
    cost of writing a `SiteSubmissionRequest` row an HTTP request -- and the
    review step is a human reading them, so a flood is an attack on a person's
    afternoon, not just on the table. The throttle runs *before* validation, so
    malformed floods are counted too; a 400 that costs nothing is still a way
    to keep the endpoint busy.
    """
    throttled = _rate_limited(request, "site-request")
    if throttled is not None:
        return throttled

    # Bounded before `request.body` is read, never after. Every field below is
    # truncated to a few hundred characters at most, so the largest honest
    # body is a few KB; without a cap an anonymous caller could make this
    # process buffer and parse up to `DATA_UPLOAD_MAX_MEMORY_SIZE` (or
    # whatever nginx allows) per request.
    limit = getattr(
        settings, "SITE_REQUEST_MAX_BODY_BYTES", DEFAULT_SITE_REQUEST_MAX_BODY_BYTES
    )
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        declared = 0
    if declared > limit:
        return _too_large(limit)
    body = request.read(limit + 1)
    if len(body) > limit:
        return _too_large(limit)

    try:
        payload = json.loads(body or "{}")
    except ValueError:
        return JsonResponse({"error": "Expected a JSON body."}, status=400)

    # `[]`, `5` and `"hello"` are all valid JSON, and every read below assumes
    # an object. Without this they raise AttributeError inside the view, which
    # on an endpoint that is deliberately anonymous means a 500 -- a Django
    # error page where the client is parsing JSON, and one stack-trace email to
    # ADMINS per request on production.
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Expected a JSON body."}, status=400)

    name = _text(payload.get("name")).strip()
    if not name:
        return JsonResponse({"error": "A site name is required."}, status=400)

    # Both consents, separately, as the form has them. A request that cannot
    # record consent must not be created at all -- the database constraint
    # would reject it anyway, and a 400 explains why.
    if not (payload.get("accepted_terms") and payload.get("accepted_privacy")):
        return JsonResponse(
            {"error": "Both the Terms and the Privacy Policy must be accepted."},
            status=400,
        )

    latitude, longitude = payload.get("latitude"), payload.get("longitude")
    try:
        latitude = float(latitude) if latitude is not None else None
        longitude = float(longitude) if longitude is not None else None
    except (TypeError, ValueError):
        return JsonResponse({"error": "Invalid coordinates."}, status=400)
    if latitude is not None and not (-90 <= latitude <= 90):
        return JsonResponse({"error": "Latitude out of range."}, status=400)
    if longitude is not None and not (-180 <= longitude <= 180):
        return JsonResponse({"error": "Longitude out of range."}, status=400)

    restricted = payload.get("restricted_access")
    if restricted is not None and not isinstance(restricted, bool):
        restricted = None

    submission = SiteSubmissionRequest.objects.create(
        name=name[:200],
        description=_text(payload.get("description"))[:2000],
        country=_text(payload.get("country"), "India")[:100],
        state=_text(payload.get("state"))[:100],
        district=_text(payload.get("district"))[:100],
        latitude=latitude,
        longitude=longitude,
        affiliation=_text(payload.get("affiliation"))[:200],
        source_url=_text(payload.get("source_url"))[:200],
        purview=_text(payload.get("purview"), "unknown")[:20],
        # Left NULL when unanswered: sending someone to photograph a restricted
        # site is a real risk, so "not answered" must not read as "no".
        restricted_access=restricted,
        accepted_terms=True,
        accepted_privacy=True,
        requester=_requester(request),
        # For an anonymous request these are the only way back to the person
        # who filed it. Truncated rather than rejected: a reviewer would rather
        # have a clipped name than no request.
        requester_name=_text(payload.get("requester_name"))[:200],
        requester_email=_text(payload.get("requester_email"))[:254],
    )
    return JsonResponse({"id": submission.ID, "status": submission.status}, status=201)


def _too_large(limit: int) -> JsonResponse:
    return JsonResponse(
        {"error": f"Request body too large (limit {limit} bytes)."}, status=413
    )


def _requester(request):
    """The signed-in contributor, if there is one. Anonymous is fine."""
    from tirtha.models import Contributor

    info = request.session.get("tirtha_user_info") or {}
    email = info.get("email")
    if not email:
        return None
    return Contributor.objects.filter(email=email).first()
