"""
The catch-all view that serves the React shell (internal design notes).

Its job is the meta tags. React renders the page; this renders what a crawler
sees, per route, before any JavaScript runs -- which is why a client-only SPA
was ruled out for this project rather than merely disfavoured.

Resolution is deliberately tolerant: an unknown or malformed route still
returns the shell with site-level defaults rather than a 404, because React
owns routing and knows about paths Django does not.
"""

from __future__ import annotations

from urllib.parse import quote

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.middleware.csrf import get_token
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import translation
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from tirtha.models import Run, Site
from tirtha.vite import entry_assets

from .display import display_config_json

DEFAULT_TITLE = "Project Tirtha"
DEFAULT_DESCRIPTION = (
    "Crowdsourced 3D models of cultural heritage sites, built from images "
    "contributed by the public."
)

# Long enough to be useful in an unfurl card, short enough that the services
# do not truncate mid-word.
DESCRIPTION_LIMIT = 200

# `Site.description` defaults to this string rather than to empty, so treating
# it as real text would put "Placeholder Text" into every unfurl card for a
# site nobody has written a description for yet.
PLACEHOLDER_DESCRIPTION = "Placeholder Text"


def _truncate(text: str, limit: int = DESCRIPTION_LIMIT) -> str:
    text = " ".join(strip_tags(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _without_segment(path: str, segment: str) -> str:
    """
    The same path with a leading `<segment>/` removed, whatever it is.

    `_localised_path` only strips a segment it recognises as a language, which
    is right for building alternates and wrong for a redirect: redirecting
    `/fr/app/` through it produced `/fr/app/` again -- a permanent redirect to
    itself, which a browser reports as ERR_TOO_MANY_REDIRECTS. The route matched
    the segment, so the view knows exactly what to remove.
    """
    prefix = "/" + settings.PRE_URL
    rest = path[len(prefix) :] if path.startswith(prefix) else path.lstrip("/")
    parts = rest.split("/")
    if parts and parts[0] == segment:
        parts = parts[1:]
    return prefix + "/".join(parts).lstrip("/")


def _localised_path(path: str, language: str) -> str:
    """
    Swap the language segment of a path, or insert one.

    **English carries no segment.** The unprefixed routes are the English ones
    (internal design notes) and `<prefix>/en/app/` is not a URL this deployment is meant
    to answer -- but `<str:lang>` matched it, Django served it 200, and the
    client router (which deliberately excludes `en` from its basename) then
    matched nothing and rendered a blank page.

    That URL was on every page, as the `hreflang="en"` alternate: the tag that
    tells a crawler where the English version of this page is. So the one link
    guaranteed to be followed by a machine pointed at a blank 200, and English
    was split into two competing URLs, one of which shows nothing.
    """
    prefix = "/" + settings.PRE_URL
    rest = path[len(prefix) :] if path.startswith(prefix) else path.lstrip("/")
    parts = rest.split("/")
    if parts and parts[0] in SUPPORTED_LANGUAGES:
        parts = parts[1:]
    tail = "/".join(parts).lstrip("/")
    if language == DEFAULT_LANGUAGE:
        return prefix + tail
    return prefix + language + "/" + tail


def _site_meta(request, site: Site) -> dict:
    where = ", ".join(p for p in (site.district, site.state, site.country) if p)
    raw = (site.description or "").strip()
    if raw == PLACEHOLDER_DESCRIPTION:
        raw = ""
    description = _truncate(raw) or (
        f"A 3D model of {site.name}{f', {where}' if where else ''}, built from "
        "contributed photographs."
    )
    image = None
    for candidate in (site.preview, site.thumbnail):
        if candidate:
            try:
                image = request.build_absolute_uri(candidate.url)
                break
            except ValueError:
                continue
    return {
        "og_title": f"{site.name} · Project Tirtha",
        "og_description": description,
        "og_image": image,
        "og_type": "article",
    }


SUPPORTED_LANGUAGES = ("en", "or", "hi")
DEFAULT_LANGUAGE = "en"
# The ones that appear as a path segment. English is served unprefixed, so
# `/en/app/` is not one of ours (internal design notes).
LOCALISED_LANGUAGES = tuple(x for x in SUPPORTED_LANGUAGES if x != DEFAULT_LANGUAGE)


def _leading_segment(path: str) -> str:
    prefix = "/" + settings.PRE_URL
    rest = path[len(prefix) :] if path.startswith(prefix) else path.lstrip("/")
    return rest.split("/", 1)[0]


def _looks_like_a_language(segment: str) -> bool:
    """
    Two lowercase letters: the shape of a language segment.

    Distinguishes `/fr/app/…` -- someone asking for a language we do not serve,
    which should be corrected -- from `/app/…`, where the leading segment is
    part of the route. Without the distinction the catch-all would try to strip
    `app` itself.
    """
    return len(segment) == 2 and segment.isalpha() and segment.islower()


def shell(
    request,
    site_id: str = None,
    slug: str = None,
    runid: str = None,
    lang: str = None,
):
    """
    Render the shell with whatever meta the route can be resolved to.

    **The language comes from the path** (internal design notes). `Accept-Language`
    negotiation alone cannot give per-language unfurls or shareable localised
    links: a crawler sends no useful Accept-Language, and a link someone shares
    must open in the language they were reading. Django activates it here so
    the meta tags a crawler sees match the page a person gets.

    **A segment that is not a language we serve is redirected, not rendered.**
    `<str:lang>` matches any two-letter string, so `/en/app/`, `/fr/app/` and a
    typo like `/od/app/` were all served 200 -- and then rendered blank, because
    the client router only recognises `or` and `hi` and computed a basename that
    did not match the path. A 200 that renders nothing is indistinguishable from
    a 404 to whoever is looking at it, and much harder to find; a redirect to the
    canonical English URL is both truthful and useful.
    """
    if lang is not None and lang not in LOCALISED_LANGUAGES:
        # Permanent: `/en/app/` is not a URL this deployment has ever meant to
        # serve, so a crawler that has one should replace it.
        return redirect(_without_segment(request.path, lang), permanent=True)

    if lang is None:
        # The catch-all route passes no `lang`, so read it back off the path --
        # otherwise a not-found page under /or/ renders its meta tags in English.
        #
        # And apply the same rule to it. The four named route shapes redirect an
        # unserved segment above; the catch-all did not, so
        # `/fr/app/nothing-here` and `/en/app/x/y/` were still 200s that the
        # client router could not match -- blank pages, which is the whole
        # failure this is here to prevent.
        segment = _leading_segment(request.path)
        if segment and _looks_like_a_language(segment):
            if segment not in LOCALISED_LANGUAGES:
                return redirect(_without_segment(request.path, segment), permanent=True)
            lang = segment
        else:
            lang = DEFAULT_LANGUAGE

    language = lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
    translation.activate(language)
    request.LANGUAGE_CODE = language
    meta = {
        "og_title": DEFAULT_TITLE,
        "og_description": DEFAULT_DESCRIPTION,
        "og_image": None,
        "og_type": "website",
    }

    if site_id:
        site = Site.objects.exclude(hidden=True).filter(ID=site_id).first()
        if site is not None:
            meta = _site_meta(request, site)
            if runid:
                run = (
                    Run.objects.exclude(hidden=True).filter(ID=runid, site=site).first()
                )
                if run is not None:
                    kind = "photogrammetry" if run.kind == "aV" else "Gaussian splat"
                    meta["og_title"] = f"{site.name} · {kind} · Project Tirtha"

    assets = entry_assets()
    # `no-cache`, and only on the shell.
    #
    # The asset filenames carry a content hash and are served
    # `max-age=2592000`, which is right: they are immutable, so a returning
    # visitor should reuse them. But the shell is the document that *names*
    # which hashed files to fetch, and it was sent with no `Cache-Control` at
    # all -- so whether somebody reloading after a deploy got the new bundle or
    # a stale document pointing at chunks that no longer exist was left to each
    # browser's heuristic freshness guess.
    #
    # `no-cache` is "revalidate before reuse", not "never store": the browser
    # still keeps the document and a conditional request can come back 304 and
    # empty. A plain reload is enough after a deploy, and nobody has to be told
    # to hard-reload.
    response = render(
        request,
        "tirtha/shell.html",
        {
            **meta,
            "language": language,
            # The React bundle POSTs -- a site request, a contribution -- and
            # `CsrfViewMiddleware` rejects those without a token. Django only
            # sets the `csrftoken` cookie once something calls `get_token`, and
            # nothing on this page did, so the front end read the cookie, found
            # nothing, and every POST from the SPA was a 403. Calling it here
            # both sets the cookie and gives the template a value to emit, which
            # keeps working if `CSRF_COOKIE_HTTPONLY` is ever turned on.
            "csrf_token_value": get_token(request),
            # hreflang alternates, so a search engine or crawler knows the same
            # page exists in the other languages rather than treating them as
            # duplicates.
            "alternates": [
                {
                    "lang": code,
                    "url": request.build_absolute_uri(
                        _localised_path(request.path, code)
                    ),
                }
                for code in SUPPORTED_LANGUAGES
            ],
            "canonical_url": request.build_absolute_uri(request.path),
            "url_prefix": "/" + settings.PRE_URL,
            "display_config": display_config_json(),
            "oembed_url": request.build_absolute_uri(
                f"/{settings.PRE_URL}oembed/?url="
                + quote(request.build_absolute_uri(request.path), safe="")
            ),
            "vite_js": assets["js"],
            "vite_css": assets["css"],
            "vite_preload": assets["preload"],
            # The four error templates have linked this since 2023; the shell
            # that serves every real page did not, so every visit asked for
            # `/favicon.ico` at the site root and took a 404. Harmless, but it
            # is the only console error on a clean load, which makes every
            # other one harder to notice.
            "favicon_url": staticfiles_storage.url("favicon.ico"),
        },
    )
    response["Cache-Control"] = "no-cache"
    return response


# The canonical documents, and the label each page carries.
def _opened(template: str, request) -> str:
    """
    The document's own markup, unfolded.

    Every one of these is a `<details>` **without `open`** -- they were written
    for the legacy index, where they are an accordion. Included as-is on a page
    of their own, the wrapper showed a heading and nothing else: the text is in
    the HTML, so a grep-style test sees it, but a reader does not. The CSS
    workaround (`details { display: block }`) does not unfold a closed
    `<details>`, and the hidden `::marker` removed even the hint that it could be
    clicked.

    `render_to_string` rather than an edit to the shared file: the legacy page
    needs them folded, and one copy on disk is the point of this wrapper.
    """
    markup = render_to_string(template, request=request)
    return mark_safe(markup.replace("<details", "<details open", 1))  # noqa: S308


DOCUMENTS = {
    "terms": ("tirtha/terms.html", _("Terms of Use")),
    "privacy": ("tirtha/privacy.html", _("Privacy Policy")),
    "acknowledgment": ("tirtha/acknowledgment.html", _("Acknowledgment")),
}


def document(request, name: str, lang: str = None):
    """
    One of the site's canonical documents, on a URL of its own (unit 6.9).

    These existed only as `<details>` blocks folded into the legacy index page,
    reachable at `/#terms`. That is not a URL anyone can cite, an unfurl can
    describe, or the SPA can link to from a language-prefixed route -- so the
    React sidebar grew its own paraphrases of the Terms and the Privacy Policy,
    which the request form then asked people to consent to. A rewritten legal
    document presented as the original is a second document nobody wrote.

    The templates are included unchanged: this is a wrapper, not a copy. They are
    authored as `<details>` for the legacy index's accordion, so on a page of
    their own the `open` attribute is added here -- adding it to the shared file
    would unfold the legacy page's accordion instead.
    """
    template, title = DOCUMENTS[name]

    # Same as the shell: `/en/terms/` and `/fr/terms/` are not URLs this
    # deployment serves, and rendering them would give the same document three
    # addresses for a crawler to treat as duplicates.
    if lang is not None and lang not in LOCALISED_LANGUAGES:
        return redirect(_without_segment(request.path, lang), permanent=True)

    language = lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
    translation.activate(language)

    prefix = f"/{settings.PRE_URL}"
    app_url = prefix + ("" if language == DEFAULT_LANGUAGE else f"{language}/")

    return render(
        request,
        "tirtha/document.html",
        {
            "document_template": template,
            "document_body": _opened(template, request),
            "title": title,
            "language": language,
            "app_url": app_url,
            # The documents themselves are English -- they are legal text
            # nobody has reviewed a translation of -- but the way *out* of them
            # should not be. Someone who arrived from the Odia app has to be
            # able to find their way back.
            "back_label": _("Back to Project Tirtha"),
            "canonical_url": request.build_absolute_uri(),
        },
    )


@require_GET
def redirect_from_app(request, rest: str):
    """
    `/app/<anything>` -> `/<anything>`, permanently.

    The React app was served under `app/` while the Django templates still owned
    the deployment root. It owns the root now, and this keeps every link written
    in the meantime working: bookmarks, the demo's own URLs, and the site pages
    people were given while the two coexisted.

    301, not 302: the move is permanent, and a search engine that has indexed
    `/app/site/<id>/` should replace it rather than keep both.
    """
    prefix = f"/{settings.PRE_URL}"
    segment = _leading_segment(request.path)
    language = f"{segment}/" if segment in LOCALISED_LANGUAGES else ""
    return redirect(f"{prefix}{language}{rest}", permanent=True)
