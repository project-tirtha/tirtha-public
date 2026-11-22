"""Statistics: platform totals and one site's contribution figures."""

from __future__ import annotations

from django.core.cache import cache
from django.db.models import Count
from django.views.decorators.http import require_GET

from tirtha.models import Contributor, Image

from .common import (
    API_VERSION,
    _conditional,
    _not_found,
    _published_runs,
    _visible_sites,
)
from .runs import _reconstruction_seconds

# Platform totals change when a run publishes or a contribution lands -- neither
# is a per-second event, and the alternative is three full-table counts on every
# page load.
_STATS_CACHE_KEY = "tirtha:api:stats"
_STATS_CACHE_SECONDS = 300


@require_GET
def site_stats(request, site_id: str):
    """
    One site's contribution figures, for the panel beside the model (unit 6.10).

    A tenth dead endpoint: the sidebar has asked `sites/<id>/stats/` since the
    port and nothing served it, so the panel showed zero images and no
    contributors for every site -- including the ones with thousands.

    Contributors are named only where they opted into a public profile
    (internal design notes). The rest are counted, not named: the count is a fact
    about the site, the name is a fact about a person who asked not to be
    listed.
    """
    site = _visible_sites().filter(ID=site_id).first()
    if site is None:
        return _not_found("site", site_id)

    # Open (batched, still arriving) uploads are not counted until finalized.
    images = Image.objects.filter(
        contribution__site=site, contribution__finalized_at__isnull=False
    )
    named = (
        Contributor.objects.filter(
            contributions__site=site,
            contributions__finalized_at__isnull=False,
            profile_public=True,
        )
        .annotate(images=Count("contributions__images", distinct=True))
        .order_by("-images")[:5]
    )

    latest = _published_runs(site).exclude(ended_at=None).order_by("-ended_at").first()

    return _conditional(
        request,
        {
            "version": API_VERSION,
            "site": site.ID,
            "total_images": images.count(),
            "total_contributors": Contributor.objects.filter(
                contributions__site=site, contributions__finalized_at__isnull=False
            )
            .distinct()
            .count(),
            "total_models": _published_runs(site).count(),
            # The most recent published run, so the panel can say how long the
            # model people are looking at took to build.
            "latest_run": (
                {
                    "kind": latest.kind,
                    "ended_at": latest.ended_at,
                    "duration_seconds": _reconstruction_seconds(latest),
                }
                if latest
                else None
            ),
            "top_contributors": [
                {"handle": c.handle, "name": c.public_name, "images": c.images}
                for c in named
            ],
        },
    )


@require_GET
def stats(request):
    """
    Platform totals for the sidebar card (unit 6.10).

    The card asked `/api/platform/statistics/`, which is not a route on this
    backend -- so it showed "Could not load statistics" on every page load
    since the port. This is that endpoint, under the versioned prefix where the
    rest of the API lives.

    Every count here is one the site already publishes elsewhere (the README
    stats block, the leaderboard), so nothing new is disclosed. What is *not*
    published is per-contributor upload counts for people who did not opt into
    a public profile (§11.2), so the top-contributor list comes from the same
    `public_board` the leaderboard uses rather than from a raw aggregate --
    otherwise this endpoint would quietly leak exactly what that opt-in exists
    to protect.

    Cached: three counts over the whole `Image` table on every page load is a
    sequential scan nobody needs to be current to the second.
    """
    from tirtha.leaderboard import PERIOD_ALL, public_board

    cached = cache.get(_STATS_CACHE_KEY)
    if cached is None:
        board = public_board(PERIOD_ALL, limit=5)
        cached = {
            "version": API_VERSION,
            "total_sites": _visible_sites().count(),
            "total_models": _published_runs().count(),
            # Contributors who have actually had an image accepted. The bare
            # row count includes anyone who ever signed in.
            "total_contributors": Contributor.objects.filter(
                contributions__images__isnull=False,
                contributions__finalized_at__isnull=False,
            )
            .distinct()
            .count(),
            "total_images": Image.objects.filter(
                contribution__site__hidden=False,
                contribution__finalized_at__isnull=False,
            ).count(),
            "top_contributors": [
                {
                    "handle": entry.contributor.handle,
                    "name": entry.contributor.public_name,
                    "rank": entry.rank,
                    "images": entry.images_accepted,
                }
                for entry in board
            ],
        }
        cache.set(_STATS_CACHE_KEY, cached, _STATS_CACHE_SECONDS)

    return _conditional(request, cached)
