"""
Pages and redirects: search, ARK resolution, legacy site URLs, how-to,
robots.txt and llms.txt.
"""

import logging

from django.conf import settings
from django.db.models import Max, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

# Local imports
from tirtha_bk.views import handler404

from ..models import ARK, Run, Site, SiteSlugHistory
from ..utilsark import parse_ark
from .common import FALLBACK_ARK_RESOLVER


def _resolve_site(ident: str):
    """
    Resolve a site by its immutable ID, then by current slug, then by a retired
    one. Returns (site, canonical) -- canonical is False when the caller used a
    slug that is no longer current, so the view can redirect rather than serve
    two URLs for the same page.
    """
    site = Site.objects.filter(ID=ident).first()
    if site:
        return site, True
    site = Site.objects.filter(verbose_id=ident).first()
    if site:
        return site, False
    retired = SiteSlugHistory.objects.filter(slug=ident).select_related("site").first()
    if retired:
        return retired.site, False
    return None, False


@require_GET
def legacy_site_redirect(request, vid: str, runid: str = None):
    """
    Permanent redirect from the old verbose_id-routed URLs.

    Renaming a site changes its verbose_id, and Phase 3 renames 39 of 60. These
    URLs are public and may be bookmarked or linked, so they redirect to the
    canonical ID-routed form rather than 404ing.
    """
    site, _ = _resolve_site(vid)
    # `or site.hidden`: without it this redirect answered 301 for a hidden site
    # and 404 for an absent one, with no sign-in at all -- a better existence
    # oracle than the authenticated one, and the `Location` header disclosed the
    # immutable `Site.ID` as well. internal design notes records the invariant this
    # restores: a hidden resource and a non-existent one answer identically.
    # The check is here rather than in `_resolve_site`, whose other callers
    # resolve first and filter afterwards for their own reasons.
    if site is None or site.hidden:
        return handler404(request, exception=None)
    if runid:
        target = reverse("siteRun", args=[site.ID, site.verbose_id, runid])
    else:
        target = reverse("siteSlug", args=[site.ID, site.verbose_id])
    return redirect(target, permanent=True)


@require_GET
def search(request):
    """
    Search sites by name, country, state, or district

    """
    query = request.GET.get("query", "").strip()

    if not query:
        # Treat empty query as "return all" — leave query as empty string so icontains matches all
        query = ""

    # Search across multiple fields
    search_query = (
        Q(name__icontains=query)
        | Q(country__icontains=query)
        | Q(state__icontains=query)
        | Q(district__icontains=query)
    )

    sites = (
        Site.objects.filter(search_query)
        .exclude(hidden=True)
        .annotate(latest_run=Max("runs__ended_at"))
        .order_by("latest_run")
    )

    # Keyed by `verbose_id`, which is unique, not by `name`, which is not.
    # There are two sites called exactly "Gopinath Temple" -- one in Puri and
    # one in Khordha -- and keying by name silently dropped one of them from
    # every search result that matched both. The name travels as a field, and
    # the template reads it from there for the title.
    meshes_json = {}
    for site in sites:
        meshes_json[site.verbose_id] = {
            "name": site.name,
            "verbose_id": site.verbose_id,
            # A site with no thumbnail raises on `.url`, and this loop had no
            # guard -- so one thumbnail-less row turned every search that
            # matched it into a 500.
            "thumb_url": site.thumbnail.url if site.thumbnail else "",
            "completed_msg": "Closed" if site.completed else "Open",
            "completed_col": "firebrick" if site.completed else "forestgreen",
        }

    status = "Site found!" if sites else "Site not found!"
    return JsonResponse({"status": status, "meshes_json": meshes_json})


@require_GET
def resolveARK(request, ark: str):
    """
    Resolve ARK identifiers to site/run views.

    """
    try:
        naan, assigned_name = parse_ark(ark)
        ark_obj = ARK.objects.get(ark=f"{naan}/{assigned_name}")

        if ark_obj.run.hidden or ark_obj.run.site.hidden:
            logging.warning(f"Attempt to access hidden ARK: {ark}")
            raise ARK.DoesNotExist("ARK points to hidden content.")

        # `siteRun`, routed on `Site.ID`. This said `indexMesh` with
        # `vid=`/`runid=`, and `indexMesh` has not been a URL name since routing
        # moved onto `Site.ID` (internal design notes) -- `grep -rn indexMesh` returned
        # this line and nothing else. `redirect()` raises `NoReverseMatch`, which
        # is not in the `except` clause below, so every ARK that *resolved*
        # returned a 500 while every ARK that did not fell through correctly to
        # the global resolver. Exactly inverted, and nothing tested this view.
        #
        # The slug is decorative -- the shell ignores it for lookup -- but it
        # is emitted so the redirect lands on the canonical, readable URL rather
        # than one the site would immediately 301 away from.
        return redirect(
            "siteRun",
            site_id=ark_obj.run.site.ID,
            slug=ark_obj.run.site.verbose_id,
            runid=str(ark_obj.run.ID),
        )
    except (ValueError, ARK.DoesNotExist, Run.DoesNotExist) as e:
        # `Run.DoesNotExist` because `ark_obj.run` raises it for an ARK whose
        # run has been deleted. Those exist deliberately -- `post_del_run` keeps
        # the ARK rather than destroying an identifier.
        logging.error(f"ARK resolution failed for {ark}: {e}")

        # The global resolver is only for somebody else's NAAN.
        #
        # This bounced *everything* it could not resolve to n2t.net. NAAN 74218
        # is ours and is registered at n2t as resolving here, so n2t sent the
        # visitor straight back, and we sent them to n2t again: an identifier
        # that cannot resolve became a redirect loop rather than an answer.
        # 30 of the 102 ARKs minted so far land in this branch -- 20 whose run
        # has been deleted and 10 pointing at hidden content.
        #
        # Persistence means the identifier keeps *meaning* something, not that
        # it keeps redirecting. So an ARK under our own NAAN is answered here:
        # 410 for one we minted whose model has since been withdrawn, which is
        # the honest answer and what an ARK tombstone is for; 404 for anything
        # else, which is also what hidden content must look like, because
        # distinguishing "hidden" from "never existed" is an existence oracle.
        #
        # A foreign NAAN still goes to n2t, which is the one place that might
        # know, and cannot loop because it does not point back here.
        our_naan = str(getattr(settings, "ARK_NAAN", ""))
        # The view receives the whole label, `ark:/74218/t1abc` -- strip the
        # scheme before splitting, or the first segment is "ark:".
        naan_seen = ark.removeprefix("ark:").lstrip("/").split("/")[0]
        if our_naan and naan_seen != our_naan:
            return redirect(f"{FALLBACK_ARK_RESOLVER}/{ark}")

        if isinstance(e, Run.DoesNotExist):
            return HttpResponse(
                "This identifier was minted by Project Tirtha. The model it "
                "named has been withdrawn, and the identifier is retained so "
                "that it continues to mean this and not something else.",
                status=410,
                content_type="text/plain; charset=utf-8",
            )
        return handler404(request)


@require_GET
def howto(request):
    """
    How-to instructions page

    """
    return render(request, "tirtha/howto.html")


@require_GET
def robots_txt(request):
    """
    Crawl policy for this deployment.

    Scoped to `PRE_URL`, not `/`: this host is shared with the rest of the lab,
    and a domain-wide disallow served from here would block crawling of every
    other project on it. The domain-root `robots.txt` needs one scoped
    `Disallow:` line, which build/robots-root-snippet.txt provides.

    The models are CC BY-NC-SA / BY-NC-ND, which require attribution and forbid
    commercial use -- terms bulk ingestion cannot honour. The photographs were
    contributed to a heritage archive by named people, not to a training
    corpus, and the records carry contributor identities to which DPDP applies.
    """
    scope = "/" + settings.PRE_URL
    return render(
        request,
        "tirtha/robots.txt",
        {
            "deployment": getattr(settings, "DEPLOYMENT_NAME", "Project Tirtha"),
            "scope": scope,
            "llms_url": request.build_absolute_uri(scope + "llms.txt"),
        },
        content_type="text/plain; charset=utf-8",
    )


@require_GET
def llms_txt(request):
    """The same policy, stated for agents that read prose rather than robots.txt."""
    return render(
        request,
        "tirtha/llms.txt",
        {"deployment": getattr(settings, "DEPLOYMENT_NAME", "Project Tirtha")},
        content_type="text/markdown; charset=utf-8",
    )
