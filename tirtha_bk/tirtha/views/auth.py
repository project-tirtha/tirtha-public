"""
Sign-in: the Google OAuth client, `signin`, `verifyToken` and the contributor
lookup the upload endpoints share.
"""

import logging
import uuid

from authlib.integrations.django_client import OAuth
from django.conf import settings
from django.shortcuts import redirect
from django.utils.encoding import iri_to_uri
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET
from google.auth.transport import requests
from google.oauth2 import id_token

# Local imports
from tirtha_bk.views import handler403

from ..admin_auth import link_admin_user
from ..models import Contributor
from .common import ADMIN_MAIL, GOOGLE_LOGIN, OAUTH_CONF, PRE_URL

# OAuth setup
oauth = OAuth()
google = oauth.register(
    "google",
    client_id=OAUTH_CONF.get("OAUTH2_CLIENT_ID"),
    client_secret=OAUTH_CONF.get("OAUTH2_CLIENT_SECRET"),
    client_kwargs={"scope": OAUTH_CONF.get("OAUTH2_SCOPE")},
    server_metadata_url=f"{OAUTH_CONF.get('OAUTH2_META_URL')}",
    authorize_url="https://accounts.google.com/o/oauth2/auth",
    redirect_uri=OAUTH_CONF.get("OAUTH2_REDIRECT_URI"),
)


def _authenticate_user(user_info):
    """
    Authenticates and retrieves or creates a contributor.

    Returns:
        tuple: (output_message, contributor_object)

    """
    if not GOOGLE_LOGIN:
        # A development convenience, and only that. Without the `DEBUG` gate
        # this hands the admin account to *any* caller on a deployment that
        # forgot to switch `GOOGLE_LOGIN` on -- and the failure is silent,
        # because everybody appears signed in rather than the site appearing
        # broken. Refusing loudly is the safer way to be misconfigured.
        if not settings.DEBUG:
            logging.error(
                "GOOGLE_LOGIN is off on a non-DEBUG deployment; refusing to "
                "authenticate as the admin account."
            )
            return "Sign-in is not configured on this deployment.", None

        logging.info("Google login is disabled - using admin account")
        contrib = Contributor.objects.get(email=ADMIN_MAIL)
        return f"Signed-in as {ADMIN_MAIL}.", contrib

    if not user_info:
        return "Please sign in again.", None

    email = user_info.get("email")
    name = user_info.get("name")

    if not email:
        return "Invalid user information.", None

    # Get or create contributor (inactive by default)
    contrib, created = Contributor.objects.get_or_create(
        email=email, defaults={"active": False, "name": name}
    )

    # Update name if changed
    if name and name != contrib.name:
        logging.info(f"Updating name for {email} from {contrib.name} to {name}")
        contrib.name = name
        contrib.save()

    # Check status
    if contrib.banned:
        return f"{email} has been banned. Please contact the admin.", contrib

    if not contrib.active:
        return f"{email} is not active. Please contact the admin.", contrib

    return f"Signed-in as {email}.", contrib


def _safe_next(request) -> str | None:
    """
    The page to return to after signing in, if it is safe to return there.

    `url_has_allowed_host_and_scheme` is the whole point. Without it, this is an
    open redirect wearing Tirtha's domain: a link to
    `.../signin/?next=https://evil.example/` sends someone through a real Google
    consent screen on a real smlab.niser.ac.in URL and lands them somewhere
    else. `require_https` is tied to the current request rather than hardcoded
    so this stays correct on a plain-HTTP dev server.
    """
    candidate = request.GET.get("next")
    if not candidate:
        return None
    if url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return iri_to_uri(candidate)
    logging.warning("Rejected an off-site ?next= on signin")
    return None


@require_GET
def signin(request):
    """OAuth2.0 authorization redirect to Google."""
    new_state = str(uuid.uuid4())
    logging.info(f"OAuth signin initiated with state: {new_state}")

    request.session["auth_random_state"] = new_state
    # The SPA opens sign-in from wherever the visitor happens to be -- a site's
    # page, the map with a pin dropped -- and dumping them back at the index
    # loses that. Stored in the session rather than round-tripped through
    # Google, so the value cannot be swapped between the redirect and the
    # callback.
    request.session["auth_next"] = _safe_next(request)
    redirect_uri = request.build_absolute_uri("/" + PRE_URL + "verifyToken/")

    return google.authorize_redirect(request, redirect_uri, state=new_state)


@require_GET
def verifyToken(request):
    """OAuth2.0 callback - verifies token and sets user session."""
    request_state = request.session.get("auth_random_state")
    received_state = request.GET.get("state")

    # Validate state for security
    if not received_state or request_state != received_state:
        logging.error("OAuth state mismatch - potential CSRF attack")
        return handler403(request)

    # authlib's own state record is left alone.
    #
    # `signin` -> `authorize_redirect` stored `_state_google_<state>` with the
    # redirect URI and the OIDC `nonce` it sent to Google. This used to be
    # overwritten here with `{"state": ...}` alone, so `authorize_access_token`
    # found no nonce and skipped validating it -- the replay protection the
    # nonce exists for was silently off (review 2026-09-04 §2.9). With the
    # record intact, authlib checks the ID token's nonce against it, and a
    # callback whose record is missing or expired is refused (`OAuthError`,
    # handled below).
    try:
        # Get access token from Google
        authlib_token = google.authorize_access_token(request)
        google_token = authlib_token["id_token"]

        # Verify and decode the JWT token
        idinfo = id_token.verify_oauth2_token(
            google_token, requests.Request(), OAUTH_CONF.get("OAUTH2_CLIENT_ID")
        )

        # A new session id for the newly-authenticated session.
        #
        # The pre-auth session -- created by `signin` when it stored
        # `auth_random_state` -- otherwise carries its id straight into the
        # authenticated one. `django.contrib.auth.login` cycles for exactly this
        # reason; this flow does not use it, so it inherits nothing. It matters
        # more than usual on a shared host: session cookies are host-scoped, so
        # anything else on smlab.niser.ac.in that can set a cookie could plant a
        # known id and then use it once the victim signs in.
        request.session.cycle_key()

        # The state has done its job; a stale one must not stay valid for the
        # life of the session.
        request.session.pop("auth_random_state", None)

        # `email_verified`, because the email *is* the identity here.
        #
        # `Contributor` is keyed on the address and nothing else: it is what
        # attributes a contribution, what the leaderboard credits, and what
        # reconstruction mail is sent to. A token can carry an address Google
        # has not confirmed belongs to the holder, and accepting one lets
        # somebody claim an identity -- and receive its mail -- by asserting it.
        #
        # A genuine Google account signing in normally always has this true, so
        # this refuses nobody who should be here.
        if not idinfo.get("email_verified"):
            logging.error(
                "Sign-in refused: Google did not verify "
                f"{idinfo.get('email')!r} belongs to this account."
            )
            request.session["tirtha_user_info"] = None
            return handler403(request)

        # Store user info in session
        request.session["tirtha_user_info"] = {
            "email": idinfo.get("email"),
            "name": idinfo.get("name"),
            "picture": idinfo.get("picture"),
        }
        logging.info(f"User authenticated: {idinfo.get('email')}")

    except Exception as e:
        # `Exception`, not `ValueError`.
        #
        # authlib raises `OAuthError` -- whose MRO is (OAuthError,
        # AuthlibBaseError, Exception), with no ValueError anywhere -- as the
        # *first* statement of `authorize_access_token` whenever Google reports
        # a problem. The commonest is a visitor pressing Cancel on the consent
        # screen, which comes back as `?error=access_denied` with the state
        # echoed, so the state check above passes and this is reached. It also
        # covers a failed code exchange and a transient network error talking to
        # Google.
        #
        # Uncaught, that is a 500 page for somebody who simply changed their
        # mind, and it left the pre-auth state and destination sitting in their
        # session.
        logging.error(f"Token verification failed: {type(e).__name__}: {e}")
        request.session["tirtha_user_info"] = None
        # Cleared here too. Both are popped on the success path below, and
        # their own comments say a stale one must not stay valid for the life
        # of the session -- which was true only when nothing went wrong.
        request.session.pop("auth_random_state", None)
        request.session.pop("auth_next", None)
        return handler403(request)

    # `pop`, so a stale destination cannot be reused by a later sign-in.
    destination = request.session.pop("auth_next", None)

    # Django admin access (`ADMIN_GOOGLE_EMAILS`), after every check above has
    # passed. Logs in the one matching active staff User, or ends a Django
    # session held under a different address; the contributor session set
    # above is kept either way. See `admin_auth.py`.
    link_admin_user(request, idinfo, destination)

    # `redirect("index")`, by URL name, not `redirect(index)` by callable.
    # `views.index` stopped being routed at 08a9f6f -- the shell serves `/`
    # now and owns the name -- so `reverse()` on the function raises
    # NoReverseMatch. That is outside the `except ValueError` above, so a
    # sign-in with no usable `next` set the session, then returned a 500 and
    # mailed ADMINS: the visitor was signed in and shown an error.
    return redirect(destination) if destination else redirect("index")
