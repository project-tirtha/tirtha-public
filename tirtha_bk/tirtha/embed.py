"""
Embeds and oEmbed (internal design notes).

A chrome-free viewer a third party can `<iframe>`, plus an oEmbed endpoint so
platforms resolve it automatically.

**Attribution is not optional.** Models are published CC BY-NC-SA 4.0 /
CC BY-NC-ND 4.0, and an embed is exactly where attribution gets stripped -- the
whole surface is someone else's page. The licence and the contributors ride
along in the embed chrome and in the oEmbed payload, not as a link back that a
platform may not render.

**Instagram and TikTok cannot be reached this way at all** (§12.3): no arbitrary
web embeds, no link unfurling. That is what the turntable render is for; an
iframe is not a substitute.
"""

from __future__ import annotations

from html import escape

from django.conf import settings
from django.db.models import F
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_GET

from tirtha.models import Site
from tirtha.vite import entry_assets

# Published licence per §5 / §12.2. Kept here as the single source for both the
# embed chrome and the oEmbed payload, so they cannot drift apart.
LICENCE_NAME = "CC BY-NC-SA 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by-nc-sa/4.0/"

DEFAULT_WIDTH = 800
DEFAULT_HEIGHT = 600


def _resolve(site_id: str, run_id: str | None):
    site = Site.objects.exclude(hidden=True).filter(ID=site_id).first()
    if site is None:
        raise Http404("No such site.")
    # `_published_runs`, not a hand-rolled queryset.
    #
    # This filtered on `hidden` alone and ordered by `-ended_at`. PostgreSQL
    # sorts NULLs *first* under DESC, and a run that has started but not
    # finished has `ended_at = NULL` -- so the instant a reconstruction began,
    # every third-party embed of that site switched to a run with no artifact
    # and rendered nothing. It would have corrected itself when the run
    # finished, which is the kind of fault nobody catches.
    #
    # `api._published_runs` already answers "which runs may be shown": not
    # hidden, site not hidden, not Error, and carrying an ARK -- a run without
    # an ARK has nothing to show whatever its status says. Sharing it means the
    # embed cannot drift from the API again.
    from .api import _published_runs

    runs = _published_runs(site)
    run = (
        runs.filter(ID=run_id).first()
        if run_id
        else runs.order_by(F("ended_at").desc(nulls_last=True), "-started_at").first()
    )
    if run is None:
        raise Http404("No published reconstruction.")
    return site, run


@require_GET
@xframe_options_exempt
def embed(request, site_id: str, run_id: str = None):
    """
    The embeddable viewer.

    `xframe_options_exempt` is required: the site sets `X-Frame-Options: DENY`
    globally, which is correct everywhere except here.
    """
    from .api import _artifact_url

    site, run = _resolve(site_id, run_id)
    assets = entry_assets()
    contributors = sorted({c.public_name for c in run.contributors.all()})
    return render(
        request,
        "tirtha/embed.html",
        {
            "site": site,
            "run": run,
            # What the viewer can read, as the API offers it -- not the ARK
            # verbatim, which may name a `.sog` the embedded viewer cannot draw.
            "model_url": _artifact_url(run.ark.url) if run.ark_id else None,
            "contributors": contributors,
            "licence_name": LICENCE_NAME,
            "licence_url": LICENCE_URL,
            "canonical_url": request.build_absolute_uri(
                reverse("shell-site", kwargs={"site_id": site.ID})
            ),
            "vite_js": assets["js"],
            "vite_css": assets["css"],
            "url_prefix": "/" + settings.PRE_URL,
        },
    )


@require_GET
def oembed(request):
    """
    oEmbed provider endpoint (rich type).

    Returns the iframe HTML platforms should use. `url` is the *canonical* page,
    not the embed path -- that is what a person pastes, and resolving it is the
    entire point of oEmbed.
    """
    target = request.GET.get("url", "")
    site, run = _from_canonical_url(target)
    if site is None:
        return JsonResponse({"error": "Unsupported url."}, status=404)

    try:
        width = int(request.GET.get("maxwidth", DEFAULT_WIDTH))
        height = int(request.GET.get("maxheight", DEFAULT_HEIGHT))
    except (TypeError, ValueError):
        return JsonResponse(
            {"error": "maxwidth and maxheight must be integers."}, status=400
        )

    embed_url = request.build_absolute_uri(
        reverse("embed-run", kwargs={"site_id": site.ID, "run_id": run.ID})
    )
    payload = {
        "version": "1.0",
        "type": "rich",
        "provider_name": "Project Tirtha",
        "provider_url": request.build_absolute_uri("/" + settings.PRE_URL),
        "title": site.name,
        # Attribution travels in the payload itself: a platform that renders
        # only the iframe still shows the credit inside it, and one that reads
        # the metadata gets it here too.
        "author_name": ", ".join(
            sorted({c.public_name for c in run.contributors.all()})
        )
        or "Project Tirtha contributors",
        "license": LICENCE_NAME,
        "license_url": LICENCE_URL,
        "width": width,
        "height": height,
        "html": (
            f'<iframe src="{embed_url}" width="{width}" height="{height}" '
            'frameborder="0" allowfullscreen '
            'allow="xr-spatial-tracking; fullscreen" '
            f'title="{escape(site.name, quote=True)} -- Project Tirtha"></iframe>'
        ),
    }
    if request.GET.get("format", "json") == "xml":
        return HttpResponse(_as_xml(payload), content_type="text/xml")
    return JsonResponse(payload)


def _from_canonical_url(url: str):
    """
    Pull the site and run out of a pasted page URL.

    Deliberately tolerant of the trailing slug, which is decorative and may be
    stale (§2.2) -- a link shared a year ago must still resolve.
    """
    if not url:
        return None, None
    parts = [p for p in url.split("?")[0].split("/") if p]
    if "site" not in parts:
        return None, None
    tail = parts[parts.index("site") + 1 :]
    if not tail:
        return None, None
    site_id = tail[0]
    run_id = tail[2] if len(tail) > 2 else None
    try:
        return _resolve(site_id, run_id)
    except Http404:
        return None, None


def _as_xml(payload: dict) -> str:
    from xml.sax.saxutils import escape

    body = "".join(
        f"<{key}>{escape(str(value))}</{key}>" for key, value in payload.items()
    )
    return f'<?xml version="1.0" encoding="utf-8"?><oembed>{body}</oembed>'
