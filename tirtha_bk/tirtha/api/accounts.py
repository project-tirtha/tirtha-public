"""Accounts: the session, sign-out, the leaderboard and public profiles."""

from __future__ import annotations

from django.http import JsonResponse
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_GET, require_POST

from tirtha.models import Contributor

from .common import API_VERSION, _not_found


@require_GET
def session(request):
    """
    Who is signed in, if anyone (unit 6.8).

    The React bundle asked `/api/auth/current-contributor/` and, when that
    404'd, `/api/auth/google-login/` with a Google id_token. Neither is a route
    here, and the second one describes a different authentication design: this
    backend signs in by full-page redirect through authlib (`views.signin` ->
    Google -> `views.verifyToken`), which leaves the answer in the session.
    There is no token for the client to post, and adding an id_token endpoint
    would mean maintaining a second way to become authenticated -- a second
    place for that to be wrong. This just reports what the session says.

    `never_cache` is not decoration. A shared cache that stored one person's
    answer and served it to the next would be an account takeover by proxy.
    """
    user_info = request.session.get("tirtha_user_info")
    payload = {"version": API_VERSION, "authenticated": False, "contributor": None}

    if user_info:
        from tirtha.views import _authenticate_user

        message, contrib = _authenticate_user(user_info)
        if contrib is not None:
            payload = {
                "version": API_VERSION,
                "authenticated": True,
                "contributor": {
                    "name": contrib.name,
                    "email": contrib.email,
                    "handle": contrib.handle,
                    # The two reasons an upload can be refused before a byte is
                    # sent. Saying so up front beats letting someone select 200
                    # photographs and then rejecting the POST.
                    "active": contrib.active,
                    "banned": contrib.banned,
                    "picture": user_info.get("picture") or "",
                },
                "message": message,
            }

    response = JsonResponse(payload)
    add_never_cache_headers(response)
    return response


@require_POST
def sign_out(request):
    """
    End the session (unit 6.8).

    POST, not GET: a signed-in person should not be signed out by a link in an
    email or an `<img>` on another site, and `CsrfViewMiddleware` covers a POST.

    `flush()` rather than deleting the one key -- it cycles the session key as
    well, so a session id captured earlier is not still valid afterwards.
    """
    request.session.flush()
    return JsonResponse({"version": API_VERSION, "authenticated": False})


@require_GET
def leaderboard(request):
    """
    The public leaderboard (internal design notes).

    Only contributors who opted into a public profile appear (§11.2), and ranks
    are **not** renumbered after that filter -- a rank is a fact about the whole
    field, and renumbering would tell someone they placed higher than they did.
    """
    from tirtha.leaderboard import PERIOD_ALL, public_board

    period = request.GET.get("period", PERIOD_ALL)
    try:
        # Both ends, for the same reason as `page_size` above.
        limit = max(1, min(int(request.GET.get("limit", 50)), 200))
    except (TypeError, ValueError):
        return JsonResponse({"error": "limit must be an integer."}, status=400)

    entries = public_board(period, limit=limit)
    return JsonResponse(
        {
            "version": API_VERSION,
            "period": period,
            "results": [
                {
                    "rank": entry.rank,
                    "handle": entry.contributor.handle,
                    "name": entry.contributor.public_name,
                    "sites_unlocked": entry.sites_unlocked,
                    "images_accepted": entry.images_accepted,
                    "frames_accepted": entry.frames_accepted,
                    "score": round(entry.score, 2),
                }
                for entry in entries
            ],
        }
    )


@require_GET
def profile(request, handle: str):
    """
    A contributor's public profile.

    Private profiles 404 rather than returning an empty shell: an endpoint that
    distinguishes "no such handle" from "exists but private" leaks the
    existence of people who chose not to be listed (§11.2).
    """
    contributor = Contributor.objects.filter(handle=handle, profile_public=True).first()
    if contributor is None:
        return _not_found("profile", handle)

    from tirtha.leaderboard import PERIOD_ALL

    entry = contributor.leaderboard_entries.filter(period=PERIOD_ALL).first()
    return JsonResponse(
        {
            "handle": contributor.handle,
            "name": contributor.public_name,
            "joined": contributor.created_at,
            "badges": [
                {
                    "slug": award.badge.slug,
                    "name": award.badge.name,
                    "icon": award.badge.icon,
                    "awarded_at": award.awarded_at,
                }
                for award in contributor.badge_awards.select_related("badge")
            ],
            "stats": (
                {
                    "rank": entry.rank,
                    "sites_unlocked": entry.sites_unlocked,
                    "images_accepted": entry.images_accepted,
                    "frames_accepted": entry.frames_accepted,
                }
                if entry
                else None
            ),
        }
    )
