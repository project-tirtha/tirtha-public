from django.conf import settings
from django.contrib import admin
from django.urls import path, re_path, register_converter
from django.views.generic import RedirectView

from . import api, embed, shell, views

admin.site.site_url = "/" + settings.PRE_URL
admin.site.site_header = "Project Tirtha Admin"
admin.site.site_title = "Project Tirtha"
admin.site.index_title = "Admin Portal"
# Adds "Sign in with Google" and hides the password form when
# `ADMIN_PASSWORD_LOGIN` is off (tirtha/admin_auth.py).
admin.site.login_template = "admin/tirtha_login.html"

# NOTE: The `pre` variable is used to prefix the URLs with a common string.
pre = settings.PRE_URL


class LanguageConverter:
    """
    A URL segment that is one of the languages actually served.

    `<str:lang>` matches any string, which was harmless while the language
    routes lived under `app/` -- `<prefix>/<lang>/app/` could only ever be a
    language. Anchored at the deployment root it matches *every* single-segment
    path: `/terms/`, `/howto/`, `/signin/`, `/search/` all resolved to the shell
    with `lang="terms"` and so on, because these routes are declared before
    theirs and Django takes the first match.

    Constraining the converter fixes it at the source rather than by reordering,
    which would work until someone adds a route in the wrong place.
    """

    regex = "|".join(shell.LOCALISED_LANGUAGES)

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


register_converter(LanguageConverter, "lang")


urlpatterns = [
    # Under PRE_URL, not at the domain root. `/robots.txt` is domain-wide, and
    # this app shares smlab.niser.ac.in with the rest of the lab -- serving a
    # `Disallow: /` there would block crawling of every other project on the
    # host. The root file gets a scoped `Disallow:` line instead; see
    # build/robots-root-snippet.txt.
    path(pre + "robots.txt", views.robots_txt, name="robots"),
    path(pre + "llms.txt", views.llms_txt, name="llms"),
    # JSON API (internal design notes). Versioned from the first commit: /api/v1/
    # costs nothing now and is the only way a breaking change ships later
    # without a same-second frontend deploy.
    path(pre + "api/v1/sites/", api.sites, name="api-sites"),
    path(pre + "api/v1/sites/<str:site_id>/", api.site_detail, name="api-site"),
    path(
        pre + "api/v1/sites/<str:site_id>/stats/",
        api.site_stats,
        name="api-site-stats",
    ),
    path(pre + "api/v1/runs/<str:run_id>/", api.run_detail, name="api-run"),
    path(pre + "api/v1/categories/", api.categories, name="api-categories"),
    # The same documents `shell.document` serves as pages, as markup the app
    # can put in a dialog. Both reach one copy on disk (unit 6.9).
    path(
        pre + "api/v1/documents/<str:name>/",
        api.document,
        name="api-document",
    ),
    path(pre + "api/v1/site-requests/", api.request_site, name="api-site-request"),
    path(pre + "api/v1/session/", api.session, name="api-session"),
    path(pre + "api/v1/sign-out/", api.sign_out, name="api-sign-out"),
    path(pre + "api/v1/stats/", api.stats, name="api-stats"),
    path(pre + "api/v1/leaderboard/", api.leaderboard, name="api-leaderboard"),
    path(pre + "api/v1/profiles/<slug:handle>/", api.profile, name="api-profile"),
    # The discovery layer (internal design notes). Django proxies the Overpass mirror,
    # which stays bound to 127.0.0.1:8003 -- this route is the only way to it,
    # and it answers a working empty layer when the mirror is unset, which is
    # the dev default.
    path(pre + "api/v1/discover/", api.discover, name="api-discover"),
    # Embeds (internal design notes). Outside the shell block: an embed must keep
    # working even if the React shell is switched off.
    path(pre + "embed/<str:site_id>/", embed.embed, name="embed-site"),
    path(pre + "embed/<str:site_id>/<str:run_id>/", embed.embed, name="embed-run"),
    path(pre + "oembed/", embed.oembed, name="oembed"),
    # The React shell, with per-route OG tags (internal design notes). Behind a flag
    # until the frontend exists (6.2); `index` still serves the Django
    # templates in the meantime.
    #
    # **Generated for both prefixes, not written twice.** The unprefixed routes
    # and the language-prefixed ones (25) were maintained as two hand-written
    # lists, and they drifted: English had `app/site/<id>/<slug>/` and the
    # `<runid>` route beneath it, the language-prefixed side stopped at
    # `<id>/`. So every Odia and Hindi share of a site page -- the slug form is
    # the one the app itself links to -- returned a Django 404, while the
    # English equivalent worked. The same shape of asymmetry put the CSP into
    # report-only on those routes (7.1). Deriving both from one list is what
    # stops it happening a third time.
    *(
        [
            route
            for prefix, suffix in (("", ""), ("<lang:lang>/", "-lang"))
            for route in (
                path(f"{pre}{prefix}", shell.shell, name=f"shell{suffix}"),
                path(
                    f"{pre}{prefix}site/<str:site_id>/",
                    shell.shell,
                    name=f"shell{suffix}-site",
                ),
                path(
                    f"{pre}{prefix}site/<str:site_id>/<str:slug>/",
                    shell.shell,
                    name=f"shell{suffix}-site-slug",
                ),
                path(
                    f"{pre}{prefix}site/<str:site_id>/<str:slug>/<str:runid>/",
                    shell.shell,
                    name=f"shell{suffix}-site-run",
                ),
                # `/app/` is where this used to live. Kept as a permanent
                # redirect rather than deleted: it is in bookmarks, in the
                # demo's links, and in every message written about this branch.
                path(
                    f"{pre}{prefix}app/",
                    RedirectView.as_view(url=f"/{pre}{prefix}", permanent=True),
                    name=f"shell{suffix}-app-legacy",
                ),
                re_path(
                    rf"^{pre}{prefix}app/(?P<rest>.*)$",
                    shell.redirect_from_app,
                    name=f"shell{suffix}-app-legacy-rest",
                ),
            )
        ]
        + [
            # Anything else the app might own, in any language. This one
            # renders the shell with default meta and lets the client router
            # show its not-found page, so a truncated share or a stale bookmark
            # reaches the app rather than Django's 404 page.
            #
            # **It is appended at the very end of `urlpatterns`**, not here.
            # When the shell lived under `app/` this pattern could sit early
            # because it could only ever match that subtree; anchored at the
            # deployment root it would swallow `api/`, `upload/`, `search/`,
            # `ark:/` and everything else. Django resolves in order, so being
            # last is the whole of its safety.
        ]
        if getattr(settings, "REACT_SHELL_ENABLED", True)
        else []
    ),
    # The canonical Terms, Privacy Policy and Acknowledgment, each on a URL of
    # its own and in each language's path (unit 6.9). They were `<details>`
    # blocks on the legacy index reachable only at `/#terms`, which is why the
    # React sidebar ended up carrying paraphrases of them.
    *[
        path(
            f"{pre}{prefix}{name}/",
            shell.document,
            {"name": name},
            name=f"document-{name}{suffix}",
        )
        for prefix, suffix in (("", ""), ("<lang:lang>/", "-lang"))
        for name in ("terms", "privacy", "acknowledgment")
    ],
    path(pre + "howto/", views.howto, name="howto"),
    path(pre + "signin/", views.signin, name="signin"),
    path(pre + "verifyToken/", views.verifyToken, name="verifyToken"),  # Auth Redirect
    path(pre + "preUpload/", views.pre_upload_check, name="preUpload"),
    path(pre + "upload/", views.upload, name="upload"),
    # Batched uploads (see `views._upload_batch`). Small JSON bodies.
    path(pre + "upload/finish/", views.upload_finish, name="upload_finish"),
    path(pre + "upload/discard/", views.upload_discard, name="upload_discard"),
    path(pre + "search/", views.search, name="search"),
    # TODO: Disabling in favour of redirect for GSRuns
    # TODO: Refactor or remove
    # path(pre + "loadMesh/", views.loadMesh, name="loadMesh"),
    # path(pre + "loadRun/", views.loadRun, name="loadRun"),
    # Canonical: routed on Site.ID, which never changes. The trailing slug is
    # decorative -- it is ignored for lookup, and a stale one 301s to the
    # current form (internal design notes).
    # These three keep their names, because `resolveARK` reverses `siteRun` and
    # `SiteSlugHistory` redirects reverse `siteSlug`. They point at the shell
    # now, so an ARK resolves into the app rather than into the retired
    # template -- which it did until this change, silently.
    path(pre + "site/<str:site_id>/", shell.shell, name="site"),
    path(pre + "site/<str:site_id>/<str:slug>/", shell.shell, name="siteSlug"),
    path(
        pre + "site/<str:site_id>/<str:slug>/<str:runid>/", shell.shell, name="siteRun"
    ),
    # Legacy: verbose_id-routed URLs, kept resolving via SiteSlugHistory.
    path(pre + "models/<str:vid>/", views.legacy_site_redirect, name="legacySite"),
    path(
        pre + "models/<str:vid>/<str:runid>/",
        views.legacy_site_redirect,
        name="legacySiteRun",
    ),
    re_path(
        rf"^{pre}(resolve/)?(?P<ark>ark:/?.*$)", views.resolveARK, name="resolveARK"
    ),  # LATE_EXP: Add support for `?info` and `??info` queries or something similar. Check ARK spec.
    path(f"{pre}", shell.shell, name="index"),
]

# The shell's catch-all, appended last so every route above wins.
#
# `api/`, `upload/`, `search/`, `ark:/`, the documents and the embeds are all
# declared before this point and resolve normally; only what nothing else
# claims reaches the app.
#
# **"Last" is only last within this file.** `tirtha_bk/urls.py` splices these
# patterns in where its `include("tirtha.urls")` appears, so anything mounted
# after that include sits *behind* this catch-all and can never be reached. That
# is how the Django admin disappeared: it was mounted on the line below the
# include, so `/project/tirtha/admin/` matched here and returned the SPA. Add
# nothing after the include in the project urlconf without checking it still
# resolves.
if getattr(settings, "REACT_SHELL_ENABLED", True):
    urlpatterns.append(
        re_path(rf"^{pre}(?:[a-z]{{2}}/)?.*$", shell.shell, name="shell-catchall")
    )
