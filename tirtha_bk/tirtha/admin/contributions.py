"""Admin for `Contribution`, and the Contribution inlines."""

import logging

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import OuterRef
from django.http import Http404
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import ngettext

# Local imports
from ..models import (
    Contribution,
    Image,
)
from ..tasks import (
    mark_ingest_enqueued,
    post_save_contrib_imageops,
    recon_runner_task,
)
from .common import _count
from .downloads import _image_files, _sanitize, zip_response
from .images import ImageInlineContribution
from .permissions import _may
from .runs import RunStageInlineContribution


class ContributionsInline(admin.TabularInline):
    def contribution_ts(self, obj):
        return obj.contributed_at

    contribution_ts.short_description = "Contribution Timestamp"

    def contribution_link(self, obj):
        url = reverse("admin:tirtha_contribution_change", args=[obj.ID])
        return format_html('<a href="{}">{}</a>', url, obj.ID)

    contribution_link.short_description = "Contribution Link"

    model = Contribution
    readonly_fields = (
        "contribution_ts",
        "contribution_link",
    )  # FIXME: Add "processed"
    fields = ("ID", "contribution_ts", "contribution_link", "processed")
    extra = 0
    max_num = 0
    can_delete = True


class ContributionInlineMesh(ContributionsInline):
    def get_queryset(self, request):
        return super().get_queryset(request).select_related("contributor")

    def contributor_email(self, obj):
        return obj.contributor.email

    contributor_email.short_description = "Contributor Email"

    readonly_fields = ContributionsInline.readonly_fields + ("contributor_email",)
    fields = ContributionsInline.fields + ("contributor_email",)


class ContributionInlineContributor(ContributionsInline):
    def get_queryset(self, request):
        return super().get_queryset(request).select_related("site")

    def site_id(self, obj):
        return obj.site.verbose_id

    site_id.short_description = "Site ID (Verbose)"

    readonly_fields = ContributionsInline.readonly_fields + ("site_id",)
    fields = ContributionsInline.fields + ("site_id",)


class UploadStateFilter(admin.SimpleListFilter):
    """
    Open (a batched upload still arriving, `finalized_at` NULL) vs finalized.

    An open contribution is invisible to ImageOps and the scheduler until its
    client finishes it or `finalize_abandoned_uploads_task` closes it.
    """

    title = "upload state"
    parameter_name = "upload_state"

    def lookups(self, request, model_admin):
        return (("open", "Open (still uploading)"), ("finalized", "Finalized"))

    def queryset(self, request, queryset):
        if self.value() == "open":
            return queryset.filter(finalized_at__isnull=True)
        if self.value() == "finalized":
            return queryset.filter(finalized_at__isnull=False)
        return queryset


@admin.register(Contribution)
class ContributionAdmin(admin.ModelAdmin):
    def site_id_verbose(self, obj):
        return obj.site.verbose_id

    site_id_verbose.short_description = "Site ID (Verbose)"

    def site_name(self, obj):
        return obj.site.name

    site_name.short_description = "Site Name"

    @admin.display(description="Image Count", ordering="_image_count")
    def image_count(self, obj):
        count = getattr(obj, "_image_count", None)
        return obj.images.count() if count is None else count

    @admin.display(description="Good Image Count", ordering="_good_count")
    def images_good_count(self, obj):
        count = getattr(obj, "_good_count", None)
        if count is None:
            count = (
                obj.images.exclude(safety_status__in=("rejected", "flagged"))
                .exclude(admin_verdict="reject")
                .count()
            )
        return count

    def get_queryset(self, request):
        # `site_name` and `contributor` read a related row, and the two counts
        # were a query each: 3 per row x 50 rows.
        images = Image.objects.filter(contribution=OuterRef("pk"))
        return (
            super()
            .get_queryset(request)
            .select_related("site", "contributor")
            .annotate(
                _image_count=_count(images, "contribution"),
                _good_count=_count(
                    images.exclude(safety_status__in=("rejected", "flagged")).exclude(
                        admin_verdict="reject"
                    ),
                    "contribution",
                ),
            )
        )

    def has_trigger_permission(self, request, obj=None):
        """Queues GPU work; see `_may`."""
        return self.has_change_permission(request, obj) and _may(request, "trigger_run")

    def get_urls(self):
        from django.urls import path

        urls = super().get_urls()
        my_urls = [
            path(
                "<str:object_id>/download_images/",
                self.admin_site.admin_view(self.download_images_view),
                name="tirtha_contribution_download_images",
            )
        ]
        return my_urls + urls

    def download_images_view(self, request, object_id):
        # Single contribution download via a per-object admin link
        try:
            contrib = Contribution.objects.get(ID=object_id)
        except (Contribution.DoesNotExist, ValueError, ValidationError):
            raise Http404("Contribution not found")
        if not self.has_view_permission(request, contrib):
            raise PermissionDenied

        return self.download_images_zip(
            request, Contribution.objects.filter(ID=contrib.ID)
        )

    def download_link(self, obj):
        try:
            url = reverse("admin:tirtha_contribution_download_images", args=[obj.ID])
            return format_html('<a class="button" href="{}">Download ZIP</a>', url)
        except Exception:
            return ""

    download_link.short_description = "Download"

    @admin.action(
        description="Mark selected contributions as processed", permissions=["change"]
    )
    def mark_processed(self, request, queryset):
        updated = queryset.update(processed=True)
        self.message_user(
            request,
            ngettext(
                "%d contribution was successfully marked as processed.",
                "%d contributions were successfully marked as processed.",
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    @admin.action(
        description="Re-run ImageOps (image checks only) for selected contributions",
        permissions=["trigger"],
    )
    def trigger_imageops(self, request, queryset):
        """
        Re-run the ingest checks. **Only** the checks.

        This was labelled "Trigger ImageOps & all reconstructions", and the
        message said so too. Ingest stopped triggering reconstruction when
        10.3.1 moved that decision to the nightly scheduler
        (`schedule_reconstructions_task`), so an operator using this to
        rebuild a model got a success message and no run. Use the aVOps/GSOps
        actions for that.
        """
        updated = queryset.update(processed=False)
        for obj in queryset:
            # `recheck=True`: without it the task now looks only at images never
            # checked, so an operator's re-run would do nothing at all. A
            # moderator's decision still stands -- `ImageOps.check_image` never
            # rescores the safety of an image with `safety_moderated_at` set.
            post_save_contrib_imageops.delay(
                str(obj.ID), recons_type="all", recheck=True
            )
            mark_ingest_enqueued(obj.ID)
            logging.info(f"ADMIN -- ImageOps successfully triggered for {obj.ID}.")
        self.message_user(
            request,
            ngettext(
                "ImageOps triggered for %d contribution. This does not start a "
                "reconstruction; use the aVOps or GSOps action for that.",
                "ImageOps triggered for %d contributions. This does not start a "
                "reconstruction; use the aVOps or GSOps action for that.",
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    @admin.action(
        description="Trigger aVOps for selected contributions", permissions=["trigger"]
    )
    def trigger_aVOps(self, request, queryset):
        count = queryset.count()
        for obj in queryset:
            recon_runner_task.delay(str(obj.ID), recons_type="aV")
            logging.info(f"ADMIN -- aVOps successfully triggered for {obj.ID}.")
        self.message_user(
            request,
            ngettext(
                "aVOps successfully triggered for %d contribution.",
                "aVOps successfully triggered for %d contributions.",
                count,
            )
            % count,
            messages.SUCCESS,
        )

    @admin.action(
        description="Trigger GSOps for selected contributions", permissions=["trigger"]
    )
    def trigger_GSOps(self, request, queryset):
        count = queryset.count()
        for obj in queryset:
            recon_runner_task.delay(str(obj.ID), recons_type="GS")
            logging.info(f"ADMIN -- GSOps successfully triggered for {obj.ID}.")
        self.message_user(
            request,
            ngettext(
                "GSOps successfully triggered for %d contribution.",
                "GSOps successfully triggered for %d contributions.",
                count,
            )
            % count,
            messages.SUCCESS,
        )

    @admin.action(
        description="Download images as ZIP for selected contribution",
        permissions=["view"],
    )
    def download_images_zip(self, request, queryset):
        contribs = list(queryset.select_related("site"))

        if not contribs:
            self.message_user(
                request,
                "No contributions selected.",
                level=messages.WARNING,
            )
            return

        # Name the archive after the site, or a fallback when it spans several.
        sites = {c.site_id: c.site for c in contribs}
        if len(sites) == 1:
            site = next(iter(sites.values()))
            site_name, verboseid = site.name, site.verbose_id
        else:
            site_name, verboseid = "multiple_sites", "multiple"

        site_name_s = _sanitize(site_name)[:100]
        verboseid_s = _sanitize(verboseid)[:50]

        if len(contribs) == 1:
            fname = f"{verboseid_s}_{str(contribs[0].ID)[:8]}.zip"
        else:
            fname = f"{verboseid_s}_{'_'.join(str(c.ID)[:4] for c in contribs)}.zip"

        return zip_response(
            _image_files(contribs, prefix_site=len(sites) > 1),
            fname,
            f"ADMIN ZIP DOWNLOAD by {request.user} - site={site_name_s} "
            f"verboseid={verboseid_s} contribs={[str(c.ID) for c in contribs]} "
            f"filename={fname}",
        )

    actions = [
        mark_processed,
        trigger_imageops,
        trigger_aVOps,
        trigger_GSOps,
        download_images_zip,
    ]
    readonly_fields = (
        "ID",
        "site",
        "contributor",
        "contributed_at",
        # "processed",
        "processed_at",
        "finalized_at",
        "last_batch_at",
        "download_link",
        "image_count",
        "images_good_count",
    )
    fields = (
        "ID",
        "contributed_at",
        "finalized_at",
        "last_batch_at",
        "site",
        "contributor",
        "processed",
        "processed_at",
        "download_link",
        "image_count",
        "images_good_count",
    )
    list_filter = (
        UploadStateFilter,
        "processed",
        "site",
    )
    list_display = (
        "ID",
        "contributed_at",
        "finalized_at",
        "site_name",
        "contributor",
        "image_count",
        "images_good_count",
        "processed_at",
        "processed",
        "download_link",
    )
    list_per_page = 50
    inlines = [
        RunStageInlineContribution,
        ImageInlineContribution,
    ]
