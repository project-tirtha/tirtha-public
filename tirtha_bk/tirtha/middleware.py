import secrets

from django.conf import settings
from django.utils import translation

# Origins the new front end actually loads from, kept here rather than inlined
# so the policy reads as a list of decisions (internal design notes).
#
# Raster basemaps are the only third-party fetches on the whole page. They are
# in `connect-src` as well as `img-src` because MapLibre fetches tiles with
# `fetch()` where it can, rather than assigning to `Image.src`.
TILE_ORIGINS = (
    "https://tile.openstreetmap.org",
    "https://server.arcgisonline.com",
    "https://tiles.openfreemap.org",
)

# Google, as a **navigation target only** (`form-action`).
#
# It used to be named in `script-src`, `connect-src` and `frame-src` as well,
# for a Google Identity Services flow the contribute modal used to run: load
# `accounts.google.com/gsi/client`, render Google's button in an iframe, POST
# the id_token back. That flow never worked here -- the endpoint it posted to
# does not exist on this backend, which signs in by server-side redirect through
# authlib -- and it is gone. The allowances outlived it, so the page was still
# permitting a third-party script origin, an iframe origin and an XHR origin for
# code that is no longer there.
#
# The redirect itself is a navigation, which is what `form-action` governs.
GOOGLE_SIGNIN = "https://accounts.google.com"

# Reverse geocoding for the site-request pin drop.
NOMINATIM = "https://nominatim.openstreetmap.org"

# **The Google Fonts allowance is gone.** internal design notes self-hosts fonts
# precisely so a visitor's IP is not disclosed to a third party on every page
# load, which matters for the DPDP obligations in 11.2, and this was recorded
# there as a defect rather than a decision.
#
# It was allowed for the Material Icons font -- which the deployed page never
# actually loaded: the <link> lived in Vite's `index.html`, and Django serves
# `shell.html`. So the origin was permitted for a request that was never made,
# while all 33 icons rendered their ligature name as text. They are inline SVG
# now (`components/Icon.jsx`), so nothing needs either origin.

_CSP = {
    "default-src": ["'self'"],
    # 'wasm-unsafe-eval' and not 'unsafe-eval': PlayCanvas compiles WebAssembly
    # in the viewer, which needs the narrow permission; the broad one would also
    # re-enable `eval` for everything else on the page.
    "script-src": ["'self'", "'wasm-unsafe-eval'"],
    # MapLibre runs its tile parser in a worker built from a blob URL.
    "worker-src": ["'self'", "blob:"],
    "img-src": ["'self'", "data:", "blob:", *TILE_ORIGINS],
    # `blob:` because a worker spawned from a blob URL fetches through
    # `connect-src`, not through the directive that allowed the worker.
    # `worker-src` already permits blob workers for MapLibre; the KTX2
    # transcoder that decodes the published meshes' textures then made 138
    # blocked requests per model load -- "Fetch API cannot load blob:..." --
    # and the mesh rendered untextured with no error the page could show.
    "connect-src": ["'self'", "blob:", *TILE_ORIGINS, NOMINATIM],
    "font-src": ["'self'", "data:"],
    # React writes `style` attributes (the cluster pie is a computed
    # conic-gradient), which `style-src-attr` governs. Stylesheets themselves
    # stay locked to 'self' -- Vite emits real .css files, so nothing needs an
    # inline <style>.
    "style-src": ["'self'"],
    "style-src-attr": ["'unsafe-inline'"],
    "frame-src": ["'none'"],
    "object-src": ["'none'"],
    "base-uri": ["'self'"],
    # The OAuth flow is a server-side redirect to Google (authlib
    # `authorize_redirect`), so this is a navigation target, not a script host.
    "form-action": ["'self'", GOOGLE_SIGNIN],
    "frame-ancestors": ["'none'"],
}


def _policy(**overrides) -> str:
    directives = {**_CSP, **overrides}
    return "; ".join(f"{k} {' '.join(v)}" for k, v in directives.items())


def _nonce() -> str:
    """
    A fresh nonce per response.

    Two Django-rendered pages have a `<style>` block of their own: the embed's
    attribution strip (12.2) and the canonical document pages. `style-src` is
    `'self'`, so both were blocked -- the embed rendered as black text on white
    with the credit line scrolled off the bottom, taking the model's attribution
    with it, which is the one thing that feature exists to keep.

    A nonce rather than `'unsafe-inline'`: the latter would let *any* injected
    `<style>` run, and an injected stylesheet is a real exfiltration channel --
    attribute selectors with `background-image` URLs read out form values one
    character at a time. `token_urlsafe` is CSPRNG-backed; a guessable nonce is
    the same as no nonce.
    """
    return secrets.token_urlsafe(16)


class SecurityHeadersMiddleware:
    """
    Security headers, with a deliberate hole for the embed route.

    `Cross-Origin-Resource-Policy: same-origin` blocks cross-origin loading of
    the model assets themselves, and `Cross-Origin-Opener-Policy: same-origin`
    breaks an embedded document -- so an embed served under these headers
    renders an empty viewer in someone else's page (internal design notes).

    The relaxation is **scoped to the embed paths and the media/static they
    pull**, not applied globally. Everything else keeps the strict policy,
    which is the point of having it.

    It also sets the Content-Security-Policy, in two modes: enforced on the
    React front end and the API, report-only everywhere else. See
    `_is_new_frontend` for why that split exists rather than one global policy.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _is_new_frontend(request) -> bool:
        """
        The paths the React front end serves, which is where CSP is *enforced*.

        Everything else -- the legacy jQuery templates and the Django admin --
        gets the same policy in report-only mode. Those pages load jQuery,
        three.js and model-viewer from unpkg, declare an import map, and run
        inline scripts; the admin adds its own inline handlers. A single
        enforced policy would either break them or be loosened until it stopped
        meaning anything. They are on their way out with the map cutover
        (internal design notes), so the policy is written for what survives and reports
        on what does not.
        """
        prefix = "/" + settings.PRE_URL
        if not request.path.startswith(prefix):
            return False

        # Matched on the **view**, not on the path.
        #
        # This used to test path shapes -- `app/`, then `tail.startswith("app/")`
        # for the language-prefixed routes, then a route-name check bolted on for
        # the documents, each added after the previous spelling was found to have
        # shipped report-only. Moving the shell from `app/` to the deployment
        # root broke all of it at once: every React page fell out of scope and
        # served no enforced policy at all.
        #
        # Everything `tirtha.shell` serves is a React-era page -- the app, the
        # canonical documents, the legacy `/app/` redirect -- so asking which
        # view is about to answer is both narrower and immune to the next move.
        view = getattr(getattr(request, "resolver_match", None), "func", None)
        if getattr(view, "__module__", "").endswith("tirtha.shell"):
            return True

        # The API and the embeds are not shell views but are equally React-era.
        rest = request.path[len(prefix) :]
        return rest.startswith(("embed/", "oembed", "api/")) or rest == "oembed"

    @staticmethod
    def _is_embeddable(request) -> bool:
        prefix = "/" + settings.PRE_URL
        return request.path.startswith(
            (
                f"{prefix}embed/",
                f"{prefix}oembed",
                settings.MEDIA_URL,
                settings.STATIC_URL,
            )
        )

    def __call__(self, request):
        # Set before the view runs, so a template can render it.
        request.csp_nonce = _nonce()

        response = self.get_response(request)
        response["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response["Cross-Origin-Embedder-Policy"] = "credentialless"

        # CSP governs documents, so it goes on documents. Sending it with every
        # tile and every one of ~19,300 media files would add a few hundred
        # bytes to responses no browser applies it to.
        if response.headers.get("Content-Type", "").startswith("text/html"):
            # An embed is framed by definition, so `frame-ancestors 'none'`
            # would undo the whole feature. The rest of the policy still holds.
            embeddable = self._is_embeddable(request)
            overrides = {
                "style-src": [*_CSP["style-src"], f"'nonce-{request.csp_nonce}'"]
            }
            if embeddable:
                overrides["frame-ancestors"] = ["*"]
            csp = _policy(**overrides)
            if self._is_new_frontend(request) or embeddable:
                response["Content-Security-Policy"] = csp
            else:
                response["Content-Security-Policy-Report-Only"] = csp

        if self._is_embeddable(request):
            # cross-origin, not same-origin: a third-party page must be able to
            # fetch the model. COOP is dropped rather than weakened -- an
            # embedded document has no opener to isolate.
            response["Cross-Origin-Resource-Policy"] = "cross-origin"
            response.headers.pop("Cross-Origin-Opener-Policy", None)
        else:
            response["Cross-Origin-Opener-Policy"] = "same-origin"
            response["Cross-Origin-Resource-Policy"] = "same-origin"

        return response


class ReadOnlyMiddleware:
    """
    Blocks every write on a read-only deployment (internal design notes).

    The public demo shares beta's database, which is only safe because nothing
    on it can write. Enforced in middleware rather than by disabling individual
    views: a new write endpoint added later is covered automatically, whereas a
    list of exempt views is a list somebody forgets to update.

    **The HTTP method is not the whole story**, which this got wrong. The whole
    OAuth flow is GET, and `views._authenticate_user` writes on GET --
    `Contributor.objects.get_or_create`, plus a `save()` when the name has
    changed. So a demo visitor could sign in, creating a real `Contributor` row
    in beta's database, while the docstring above claimed sessions were blocked.
    And then could not sign out, because that is a POST: the account is created,
    the session is set, and the only way out is to clear cookies by hand.

    So there are two named exceptions, and they run in opposite directions:

    * `signin` is refused. It writes, whatever its method says.
    * `api-sign-out` is allowed. It only removes state, and refusing it strands
      whoever is already signed in.

    Both are matched on the URL name via `process_view`, which runs after
    resolution -- a path prefix comparison would have to know `PRE_URL` and the
    language segment, and would silently stop matching if either changed.
    """

    SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

    # Writes that arrive as a GET.
    WRITING_READS = frozenset({"signin", "verifyToken"})

    # Removes state; refusing it would strand a signed-in visitor.
    ALLOWED_WRITES = frozenset({"api-sign-out"})

    def __init__(self, get_response):
        self.get_response = get_response

    def _refusal(self):
        from django.http import JsonResponse

        return JsonResponse(
            {
                "error": (
                    "This is a read-only demo. Contributions go to the live "
                    "site at https://smlab.niser.ac.in/project/tirtha/"
                )
            },
            status=405,
        )

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not getattr(settings, "DEMO_READ_ONLY", False):
            return None

        url_name = getattr(request.resolver_match, "url_name", None)

        if url_name in self.ALLOWED_WRITES:
            return None
        if url_name in self.WRITING_READS:
            return self._refusal()
        if request.method not in self.SAFE_METHODS:
            return self._refusal()
        return None

    def __call__(self, request):
        return self.get_response(request)


class ResetLanguageMiddleware:
    """
    Put the thread's language back after every response.

    `shell.shell` and `shell.document` call `translation.activate()` to render a
    page in the language its URL names, and never deactivate. Under gunicorn a
    worker is a long-lived thread, and `translation.activate` sets thread-local
    state -- so the language a visitor chose stayed active for **every
    subsequent request that worker handled**, whoever made it.

    That is not cosmetic. `Site.name` and `Site.description` are registered with
    modeltranslation, which rewrites `name__icontains` and `order_by("name")` to
    the *active* language's column, and only `name_en` is populated. Measured
    against the live demo: `/api/v1/sites/?q=Temple` returns 28, then 25 visits
    to `/or/app/` by anyone at all make the same URL return **0**, because it is
    now searching an all-NULL `name_or`. The site list's ordering goes arbitrary
    for the same reason.

    Django's own `LocaleMiddleware` deactivates in its response phase; this does
    only that half. It deliberately does **not** select a language -- the URL
    prefix does that, and `LocaleMiddleware` would additionally consult
    `Accept-Language` and set a `Vary: Accept-Language` on every response, which
    would fragment the cache and override the explicit choice a `/or/` link
    encodes.

    The test suite is structurally blind to this: tests use
    `translation.override`, which restores on exit, so no test could ever
    observe the leak.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            return self.get_response(request)
        finally:
            # `finally`, so an exception mid-render cannot leave the worker
            # pinned to a language either.
            translation.deactivate()
