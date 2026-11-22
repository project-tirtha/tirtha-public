"""
Who may reach the Django admin, and how they get there.

Three ways in, all ending in `django.contrib.auth.login` with `ModelBackend`:

1. **Google** -- the admin login page links to the contributor `signin` view
   with `next=` the admin URL. `views.verifyToken` runs its usual checks
   (state, authlib's nonce, the ID token's issuer and audience,
   `email_verified`) and then calls `link_admin_user`, which logs the existing
   staff `User` in when the address is in `ADMIN_GOOGLE_EMAILS`. Same OAuth
   client, same callback, so no new redirect URI is registered with Google.
2. **Password** -- Django's own admin form, while `ADMIN_PASSWORD_LOGIN` is
   True (the default, so dev, tests and beta behave as before). Production sets
   it False, and the form is then neither shown nor accepted.
3. **Emergency link** -- `manage.py admin_login_link <username>` prints a
   signed, ten-minute, single-use URL. It needs shell access to the server, and
   works with password login off and Google unreachable.

Users are only ever *linked*, never created: an allowlisted address with no
active staff `User` behind it gets no admin session.
"""

from __future__ import annotations

import logging
import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth import get_user_model, login, logout
from django.core import signing
from django.core.cache import cache
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET
from tirtha_bk.views import handler403

logger = logging.getLogger(__name__)

MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"

LINK_SALT = "tirtha.admin_auth.login-link"
LINK_MAX_AGE = 10 * 60  # seconds
_USED_KEY = "tirtha:admin-login-link:used:{}"


# ---------------------------------------------------------------- settings


def admin_google_emails() -> set[str]:
    """`ADMIN_GOOGLE_EMAILS`, lower-cased. Empty by default: nobody."""
    return {
        str(e).strip().lower()
        for e in (getattr(settings, "ADMIN_GOOGLE_EMAILS", None) or [])
        if str(e).strip()
    }


def password_login_enabled() -> bool:
    """`ADMIN_PASSWORD_LOGIN`, default True."""
    return bool(getattr(settings, "ADMIN_PASSWORD_LOGIN", True))


def _staff_user_for(email: str):
    """The single active staff User with this address, or None."""
    users = list(
        get_user_model().objects.filter(
            email__iexact=email, is_active=True, is_staff=True
        )[:2]
    )
    return users[0] if len(users) == 1 else None


# ------------------------------------------------------------ Google path


def link_admin_user(request, idinfo: dict, destination: str | None) -> None:
    """
    Called by `views.verifyToken` once the ID token has passed every check.

    Logs the matching staff User in, or makes sure no Django user stays logged
    in under a different address. The contributor session
    (`tirtha_user_info`) is preserved either way.
    """
    email = (idinfo.get("email") or "").strip()
    email_l = email.lower()
    wants_admin = bool(destination) and destination.startswith(reverse("admin:index"))

    current = getattr(request, "user", None)
    if (
        current is not None
        and current.is_authenticated
        and (current.email or "").lower() != email_l
    ):
        # `logout` flushes the whole session, contributor sign-in included.
        keep = request.session.get("tirtha_user_info")
        logger.info(
            "Admin session for %r ended: Google sign-in as %r", current.email, email
        )
        logout(request)
        request.session["tirtha_user_info"] = keep

    if not idinfo.get("email_verified") or not email:
        # verifyToken refuses these before getting here; belt and braces.
        logger.warning("Admin sign-in refused for %r: email not verified", email)
        return

    if email_l not in admin_google_emails():
        if wants_admin:
            logger.warning(
                "Admin sign-in refused for %r: not in ADMIN_GOOGLE_EMAILS", email
            )
            messages.error(request, f"{email} is not allowed to sign in to the admin.")
        return

    user = _staff_user_for(email)
    if user is None:
        logger.warning(
            "Admin sign-in refused for %r: allowlisted, but not exactly one "
            "active staff User has that email",
            email,
        )
        if wants_admin:
            messages.error(
                request, f"{email} has no active staff account on this site."
            )
        return

    # `login` cycles the session key and keeps the session's contents.
    login(request, user, backend=MODEL_BACKEND)
    logger.info("Admin sign-in via Google: %r as user %r", email, user.username)


def _google_signin_url(request) -> str:
    target = request.GET.get("next") or request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        target = ""
    target = target or reverse("admin:index")
    return reverse("signin") + "?" + urlencode({"next": target})


# --------------------------------------------------------- the login page


@never_cache
@csrf_protect
def admin_login(request, extra_context=None):
    """
    The admin login page, mounted over `admin:login` in `tirtha_bk/urls.py`.

    Always offers Google. With `ADMIN_PASSWORD_LOGIN` on it is otherwise
    Django's own view; with it off, the username/password form is not
    rendered and a POST to this URL is refused with a 403.
    """
    context = {
        **(extra_context or {}),
        "google_signin_url": _google_signin_url(request),
        "password_login": password_login_enabled(),
    }
    if password_login_enabled():
        return admin.site.login(request, extra_context=context)

    if request.method == "POST":
        logger.warning(
            "Admin password login refused (ADMIN_PASSWORD_LOGIN is off) "
            "for username %r",
            request.POST.get("username", ""),
        )
        status = 403
        context["password_refused"] = True
    else:
        status = 200
        if admin.site.has_permission(request):
            return redirect(reverse("admin:index"))

    context.update(
        {
            **admin.site.each_context(request),
            "title": "Log in",
            "subtitle": None,
            "app_path": request.get_full_path(),
            "username": request.user.get_username(),
            "next": request.GET.get("next", ""),
        }
    )
    request.current_app = admin.site.name
    return TemplateResponse(
        request,
        admin.site.login_template or "admin/login.html",
        context,
        status=status,
    )


# ------------------------------------------------------- emergency access


def make_login_link_token(user) -> str:
    """
    A signed, salted, timestamped token naming `user`.

    `last_login` is bound into it, so a token stops working once anybody logs
    in as that user -- including through the token itself, which makes it
    single-use even if the cache entry below is lost.
    """
    signer = signing.TimestampSigner(salt=LINK_SALT)
    return signer.sign_object(
        {
            "u": user.pk,
            "n": secrets.token_urlsafe(16),
            "l": user.last_login.isoformat() if user.last_login else "",
        }
    )


def _refuse_link(request, reason: str, detail: str = ""):
    logger.warning("Admin login link refused: %s %s", reason, detail)
    return handler403(request)


@never_cache
@require_GET
def admin_login_link(request, token: str):
    """The view `manage.py admin_login_link` points at."""
    signer = signing.TimestampSigner(salt=LINK_SALT)
    try:
        payload = signer.unsign_object(token, max_age=LINK_MAX_AGE)
    except signing.SignatureExpired:
        return _refuse_link(request, "expired")
    except signing.BadSignature:
        return _refuse_link(request, "bad signature")

    nonce = payload.get("n")
    if not nonce:
        return _refuse_link(request, "no nonce")
    # `add` is atomic on the database cache (unique key): only the first
    # request for a nonce wins. Kept a little longer than the token lives.
    if not cache.add(_USED_KEY.format(nonce), 1, LINK_MAX_AGE + 60):
        return _refuse_link(request, "already used", f"(user pk {payload.get('u')})")

    user = (
        get_user_model()
        .objects.filter(pk=payload.get("u"), is_active=True, is_staff=True)
        .first()
    )
    if user is None:
        return _refuse_link(request, "not an active staff user", str(payload.get("u")))
    current = user.last_login.isoformat() if user.last_login else ""
    if current != payload.get("l"):
        return _refuse_link(request, "superseded by a later login", user.username)

    login(request, user, backend=MODEL_BACKEND)
    logger.warning("Admin sign-in via emergency login link: user %r", user.username)
    return redirect(reverse("admin:index"))
