"""Admin for `Run` and `RunStage`, and their inlines."""

import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, OuterRef, Q
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html, format_html_join, mark_safe

# Local imports
from ..models import (
    Run,
    RunStage,
)
from .common import _count
from .downloads import (
    _models_root,
    _published_files,
    _run_dir,
    _sanitize,
    _walk,
    zip_response,
)
from .permissions import _may


class RunInlineMesh(admin.TabularInline):
    model = Run
    readonly_fields = ("ID", "ark", "status", "started_at", "ended_at")
    fields = ("ID", "ark", "status", "started_at", "ended_at")
    extra = 0
    max_num = 0
    can_delete = False


class _StageInline(admin.TabularInline):
    """
    The stage list for a Run or a Contribution.

    Read-only on purpose: these rows are the record of what the pipeline did.
    An operator editing them turns the audit trail into a guess, and there is
    nothing here that is useful to fix by hand -- a wrong row means the pipeline
    reported wrongly, which is a code bug (internal design notes).
    """

    model = RunStage
    extra = 0
    can_delete = False
    show_change_link = True

    def took(self, obj):
        if obj.duration_s is None:
            return "--"
        secs = obj.duration_s
        if secs < 90:
            return f"{secs:.1f}s"
        if secs < 5400:
            return f"{secs / 60:.1f}m"
        return f"{secs / 3600:.2f}h"

    took.short_description = "Duration"

    def measurements(self, obj):
        """The metrics blob, flattened -- an operator should not read JSON."""
        if not obj.metrics:
            return "--"
        return format_html_join(
            mark_safe("<br>"), "{}: {}", sorted(obj.metrics.items())
        )

    measurements.short_description = "Metrics"

    fields = (
        "ordinal",
        "name",
        "attempt",
        "status",
        "started_at",
        "took",
        "exit_code",
        "measurements",
        "log_path",
    )
    readonly_fields = fields
    ordering = ("ordinal", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


class RunStageInlineRun(_StageInline):
    fk_name = "run"
    verbose_name_plural = "Reconstruction stages"


class RunStageInlineContribution(_StageInline):
    fk_name = "contribution"
    verbose_name_plural = "Ingest stages"


class ImageInlineRun(admin.TabularInline):
    """
    Shows images in the Run admin page

    """

    # def get_image(self, obj): # FIXME:
    #     return mark_safe(
    #         f'<img src="{obj.url}" style="width: 400px; height: 400px">'
    #     )

    # get_image.short_description = "Preview"

    def image_link(self, obj):
        url = reverse("admin:tirtha_image_change", args=[obj.image.ID])
        return format_html('<a href="{}">{}</a>', url, obj.image.ID)

    image_link.short_description = "Image Link"

    model = Run.images.through
    readonly_fields = ("image_link",)
    fields = ("image_link",)
    extra = 0
    can_delete = False


class ContributorInlineRun(admin.TabularInline):
    """
    Shows contributors in the Run admin page

    """

    model = Run.contributors.through
    extra = 0


class RunReplaceForm(forms.ModelForm):
    replacement = forms.FileField(
        required=False,
        label="Replace final output",
        help_text=(
            "Upload .gltf/.glb for MeshOps (aV) or .splat for GSOps. Needs the "
            "publish permission on runs (superusers have it)."
        ),
    )

    class Meta:
        model = Run
        fields = []


@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    form = RunReplaceForm

    def site_id_verbose(self, obj):
        return obj.site.verbose_id

    site_id_verbose.short_description = "Site ID (Verbose)"

    def progress(self, obj):
        """
        How far this run got, from the stage rows.

        The changelist is where an operator looks first, and "Processing" alone
        does not distinguish a run three minutes in from one wedged for six
        hours. Names the failed stage directly, since that is the next thing
        they would go looking for (internal design notes).
        """
        stages = list(obj.stages.all())
        if not stages:
            # Runs predating unit 5.6's wiring have no stage rows at all.
            return "--"
        done = sum(
            1
            for s in stages
            if s.status in (RunStage.Status.COMPLETED, RunStage.Status.SKIPPED)
        )
        failed = next((s for s in stages if s.status == RunStage.Status.FAILED), None)
        if failed is not None:
            return format_html(
                '<span style="color:#b00">{}/{} -- failed at {}</span>',
                done,
                len(stages),
                failed.name,
            )
        running = next((s for s in stages if s.status == RunStage.Status.RUNNING), None)
        if running is not None:
            return f"{done}/{len(stages)} -- {running.name}"
        return f"{done}/{len(stages)}"

    progress.short_description = "Stages"

    @admin.display(description="Image Count", ordering="_image_count")
    def image_count(self, obj):
        count = getattr(obj, "_image_count", None)
        return obj.images.count() if count is None else count

    def has_publish_permission(self, request, obj=None):
        """Replacing a published output changes what an ARK resolves to."""
        return self.has_change_permission(request, obj) and _may(request, "publish_run")

    def has_trigger_permission(self, request, obj=None):
        """Queues GPU work; see `_may`."""
        return self.has_change_permission(request, obj) and _may(request, "trigger_run")

    readonly_fields = (
        "ID",
        "ark",
        "run_links",
        "site_id_verbose",
        "kind",
        "started_at",
        "ended_at",
        "image_count",
        "status",
        "directory",
        "notes",
        "download_link",
    )
    fieldsets = (
        (
            "Run Metadata",
            {
                "fields": (
                    ("ID", "ark"),
                    "site_id_verbose",
                    "kind",
                    ("started_at", "ended_at"),
                    "directory",
                    "image_count",
                    (
                        "status",
                        "hidden",
                    ),
                    "notes",
                )
            },
        ),
        (
            "Editable / Viewer Settings",
            {
                "fields": (
                    (
                        "download_link",
                        "run_links",
                        "replacement",
                    ),
                    # <model-viewer>'s orientation
                    (
                        "rotaZ",
                        "rotaX",
                        "rotaY",
                    ),
                    # GS Viewer Settings
                    (
                        "initCamPosX",
                        "initCamPosY",
                        "initCamPosZ",
                    ),
                    (
                        "initCamLookAtX",
                        "initCamLookAtY",
                        "initCamLookAtZ",
                    ),
                    (
                        "camUpX",
                        "camUpY",
                        "camUpZ",
                    ),
                    (
                        "sphDegree",
                        "focalAdjustment",
                        "antialiased",
                    ),
                )
            },
        ),
    )
    list_filter = ("status", "kind")

    @admin.display(description="Masks", ordering="site_mask_count")
    def masks_available(self, obj):
        """
        How many of this site's images carry a mask.

        The answer to "is there anything to cut here?", which is otherwise only
        discoverable by opening the Image changelist and filtering. Annotated
        rather than counted per row -- `RunAdmin` is the one admin that already
        avoids an N+1 and it should stay that way.
        """
        count = getattr(obj, "site_mask_count", 0)
        return count or "\u2014"

    @admin.action(
        description="Run the Gaussian cut on selected runs", permissions=["trigger"]
    )
    def trigger_gaussian_cut(self, request, queryset):
        """
        Cut a published reconstruction down to what the masks describe.

        Queued, never inline: it rewrites the artifacts the site is served from
        and belongs behind the recon queue's single slot, so it cannot run while
        the same site is being reconstructed.

        Only GS runs. There are no Gaussians in an aV mesh to cut, and firing
        this at one would spend a queue slot to fail.
        """
        from ..tasks import gaussian_cut_task

        queued, skipped = 0, []
        for run in queryset:
            if run.kind != "GS":
                skipped.append(f"{run.ID} is a {run.kind} run")
                continue
            ply = (
                Path(settings.STATIC_ROOT)
                / "models"
                / run.directory
                / "output"
                / "reconstruction.ply"
            )
            if not ply.is_file():
                # Nothing to cut. Says which file, because the usual cause is a
                # run that failed before FRGS wrote anything.
                skipped.append(f"{run.ID} has no {ply.name}")
                continue
            gaussian_cut_task.delay(str(run.ID))
            queued += 1

        if queued:
            self.message_user(
                request,
                f"Queued a Gaussian cut for {queued} run(s). They will run one at "
                "a time on the recon queue; watch the stage rows for progress.",
                messages.SUCCESS,
            )
        for reason in skipped:
            self.message_user(request, f"Skipped: {reason}.", messages.WARNING)

    list_display = (
        "ID",
        "site_id_verbose",
        "kind",
        "image_count",
        "status",
        "progress",
        "masks_available",
        "started_at",
        "ark",
        "download_link",
    )
    list_per_page = 50

    def get_queryset(self, request):
        # `progress` reads every row's stages; without this the changelist
        # issues one query per Run. `site_mask_count` is annotated for the same
        # reason -- counting masks per row would undo it.
        # `site` and `ark` are read on every row (`site_id_verbose`, the `ark`
        # column), and the image count is a subquery rather than a second
        # `Count` -- over the same join as `site_mask_count` it would multiply
        # run images by site images.
        return (
            super()
            .get_queryset(request)
            .select_related("site", "ark")
            .prefetch_related("stages")
            .annotate(
                site_mask_count=Count(
                    "site__contributions__images",
                    filter=~Q(site__contributions__images__mask=""),
                    distinct=True,
                ),
                _image_count=_count(
                    Run.images.through.objects.filter(run=OuterRef("pk")), "run"
                ),
            )
        )

    inlines = [
        RunStageInlineRun,
        ContributorInlineRun,
    ]  # ImageInlineRun, FIXME: Too many images lead to 400 (Bad Request)

    def get_urls(self):
        from django.urls import path

        urls = super().get_urls()
        my_urls = [
            path(
                "<str:object_id>/download_output/",
                self.admin_site.admin_view(self.download_output_view),
                name="tirtha_run_download_output",
            ),
            path(
                "<str:object_id>/download_run_full/",
                self.admin_site.admin_view(self.download_run_full_view),
                name="tirtha_run_download_full",
            ),
            path(
                "<str:object_id>/replace_output/",
                self.admin_site.admin_view(self.replace_output_view),
                name="tirtha_run_replace_output",
            ),
        ]
        return my_urls + urls

    def download_output_view(self, request, object_id):
        # Download only the final published output for the run (published folder)
        run = Run.objects.select_related("site").filter(ID=object_id).first()
        if run is None:
            raise Http404("Run not found")
        if not self.has_view_permission(request, run):
            raise PermissionDenied

        # Only Archived runs have a settled published output to hand out.
        if run.status != "Archived":
            self.message_user(
                request,
                "Published output can be downloaded only for Archived runs.",
                level=messages.ERROR,
            )
            raise PermissionDenied

        verboseid_s = _sanitize(run.site.verbose_id)[:50]
        files = _published_files(run)
        if not files:
            self.message_user(
                request,
                "No final published output found for this run.",
                level=messages.WARNING,
            )
            raise Http404("Published output not found")

        static_models = _models_root()
        fname = f"{verboseid_s}_{str(run.ID)[:8]}.zip"
        return zip_response(
            [(fp, os.path.relpath(fp, static_models)) for fp in files],
            fname,
            f"ADMIN RUN PUBLISHED DOWNLOAD by {request.user} - run={run.ID} "
            f"site={verboseid_s} filename={fname}",
        )

    def download_run_full_view(self, request, object_id):
        # Download the entire run directory (archived rundir)
        run = Run.objects.select_related("site").filter(ID=object_id).first()
        if run is None:
            raise Http404("Run not found")
        if not self.has_view_permission(request, run):
            raise PermissionDenied

        verboseid_s = _sanitize(run.site.verbose_id)[:50]
        run_dir = _run_dir(run)
        if not run_dir or not os.path.isdir(run_dir):
            self.message_user(
                request, "Run directory not found for this run.", level=messages.WARNING
            )
            raise Http404("Run directory not found")

        fname = f"{verboseid_s}_full_{str(run.ID)[:8]}.zip"
        return zip_response(
            ((fp, os.path.relpath(fp, run_dir)) for fp in _walk(run_dir)),
            fname,
            f"ADMIN RUN FULL DOWNLOAD by {request.user} - run={run.ID} "
            f"site={verboseid_s} filename={fname}",
        )

    def download_link(self, obj):
        try:
            url1 = reverse("admin:tirtha_run_download_output", args=[obj.ID])
            url2 = reverse("admin:tirtha_run_download_full", args=[obj.ID])
            return format_html(
                '<a class="button" href="{}">Download Output</a>&nbsp;'
                '<a class="button" href="{}">Download Full Run</a>',
                url1,
                url2,
            )
        except Exception:
            return ""

    download_link.short_description = "Download Output"

    def run_links(self, obj):
        """
        Render links for Runs.

        - "Open on Site": shown for `GS` and `aV` runs; links to the Run (via `BASE_URL/<ark>`).
        - "Edit in SuperSplat": shown only for `GS` runs; links to the .splat file.
        Returns "N.A." for other run kinds.
        """
        try:
            kind = getattr(obj, "kind", None)
            if kind not in ("GS", "aV"):
                return "N.A."

            site = obj.site
            # Use BASE_URL from settings as site base
            base = (
                settings.BASE_URL.rstrip("/")
                if getattr(settings, "BASE_URL", None)
                else ""
            )

            # Build run URL using the run's ARK
            run_ark = getattr(obj, "ark", None)
            run_url = (
                f"{base}/{run_ark}"
                if (base and run_ark)
                else (f"/{run_ark}" if run_ark else "")
            )

            links = []
            if run_url:
                links.append(
                    format_html(
                        '<a class="button" href="{}" target="_blank" '
                        'rel="noopener">Open on Site</a>',
                        run_url,
                    )
                )

            # Add SuperSplat edit link only for GS runs
            if kind == "GS":
                ext = ".splat"
                filename = f"{site.ID}_{obj.ID}{ext}"
                splat_url = (
                    f"{base}/static/models/{site.ID}/published/{filename}"
                    if base
                    else f"/static/models/{site.ID}/published/{filename}"
                )
                ss_url = f"https://superspl.at/editor?load={splat_url}"
                links.append(
                    format_html(
                        '<a class="button" href="{}" target="_blank" '
                        'rel="noopener">Edit in SuperSplat</a>',
                        ss_url,
                    )
                )

            if not links:
                return ""
            # As in external_links: the parts are format_html output.
            return mark_safe("&nbsp;".join(links))
        except Exception:
            return ""

    run_links.short_description = "View / Edit Output"

    # Replacement is handled via the change form
    def change_view(self, request, object_id, form_url="", extra_context=None):
        # If a replacement file was submitted via the change form, delegate
        # to the existing replace_output_view which performs permission checks
        # and the atomic replacement logic.
        try:
            if request.method == "POST" and request.FILES.get("replacement"):
                return self.replace_output_view(request, object_id)
        except PermissionDenied:
            # A refusal is an answer, not something to fall through past: the
            # catch-all below would otherwise save the rest of the form and
            # report success to someone who was just refused.
            raise
        except Exception:
            # Let replace_output_view handle messaging/logging; fall through to normal handling
            pass

        return super().change_view(request, object_id, form_url, extra_context)

    def get_fieldsets(self, request, obj=None):
        """Return fieldsets but hide the `replacement` input unless the Run is Archived."""
        try:
            # Work on a shallow copy of the configured fieldsets
            fsets = [
                (title, dict(options))
                if isinstance(options, dict)
                else (title, options)
                for (title, options) in self.fieldsets
            ]
            if obj is None or getattr(obj, "status", None) != "Archived":
                # Modify the first fieldset's fields tuple to remove 'replacement' where present
                title, opts = fsets[0]
                fields = list(opts.get("fields", ()))
                if fields:
                    first = list(fields[0])
                    if "replacement" in first:
                        first = [f for f in first if f != "replacement"]
                        fields[0] = tuple(first)
                        opts["fields"] = tuple(fields)
                        fsets[0] = (title, opts)
            # Return as tuple in the same structure
            return tuple(fsets)
        except Exception:
            return self.fieldsets

    @admin.action(
        description="Download final published outputs (uncompressed ZIP) for selected runs",
        permissions=["view"],
    )
    def download_final_outputs(self, request, queryset):
        runs = list(queryset.select_related("site"))
        if not runs:
            self.message_user(request, "No runs selected.", level=messages.WARNING)
            return

        site_ids = {r.site_id for r in runs}
        verboseid = (
            _sanitize(runs[0].site.verbose_id)[:50]
            if len(site_ids) == 1
            else "multiple"
        )
        if len(runs) == 1:
            fname = f"{verboseid}_{str(runs[0].ID)[:8]}.zip"
        else:
            fname = f"{verboseid}_{'_'.join(str(r.ID)[:4] for r in runs)}.zip"

        static_models = _models_root()
        file_tuples = []
        for run in runs:
            for fp in _published_files(run):
                parts = [str(run.ID), os.path.relpath(fp, static_models)]
                if len(site_ids) > 1:
                    parts.insert(0, _sanitize(run.site.verbose_id)[:50])
                file_tuples.append((fp, os.path.join(*parts)))

        return zip_response(
            file_tuples,
            fname,
            f"ADMIN RUNS PUBLISHED DOWNLOAD by {request.user} - "
            f"runs={[str(r.ID) for r in runs]} filename={fname}",
        )

    def replace_output_view(self, request, object_id):
        """
        Admin view to replace the final published output file for a Run.
        Strictly enforces allowed upload types: .gltf/.glb for MeshOps and .splat for GSOps.
        Replacement is performed by writing the upload to a temp file, moving the original
        file to a timestamped .bak, then atomically moving the temp file to the original filename.
        Run.notes is updated only on success.

        Needs `has_publish_permission`, not merely staff: `admin_view` already
        guarantees staff, so the old `is_staff` re-check here allowed any
        staff account, view-only included, to replace what an ARK serves.

        """
        if not self.has_publish_permission(request):
            self.message_user(
                request,
                "Permission denied: replacing a published output needs the "
                "publish permission on runs.",
                level=messages.ERROR,
            )
            raise PermissionDenied

        try:
            run = Run.objects.get(ID=object_id)
        except Run.DoesNotExist:
            raise Http404("Run not found")

        site = run.site
        static_models = os.path.join(settings.STATIC_ROOT, "models")
        pub_dir = os.path.join(static_models, str(site.ID), "published")

        # Find existing final output file for this run
        existing_file = None
        if os.path.isdir(pub_dir):
            # Prefer exact filename with expected extension (workers.py writes .glb for aV, .splat for GS)
            base_name = f"{site.ID}_{run.ID}"
            candidates = []
            if run.kind == "aV":
                candidates = [f"{base_name}.glb", f"{base_name}.gltf"]
            else:
                candidates = [f"{base_name}.splat"]

            for cand in candidates:
                cand_path = os.path.join(pub_dir, cand)
                if os.path.isfile(cand_path):
                    existing_file = cand
                    break

        if not existing_file:
            self.message_user(
                request,
                "Published final output not found for this run.",
                level=messages.ERROR,
            )
            raise Http404("Published output not found")

        if request.method == "GET":
            form_html = (
                f"<h2>Replace final output for Run {run.ID}</h2>"
                f"<p>Existing file: <strong>{existing_file}</strong></p>"
                '<form method="post" enctype="multipart/form-data">'
                "%s"
                '<input type="file" name="replacement" required>'
                '<input type="submit" value="Upload & Replace">'
                "</form>"
            )
            from django.middleware.csrf import get_token

            csrf = f'<input type="hidden" name="csrfmiddlewaretoken" value="{get_token(request)}">'
            return HttpResponse(form_html % csrf)

        # Process uploaded file
        uploaded = request.FILES.get("replacement")
        if not uploaded:
            self.message_user(request, "No file uploaded.", level=messages.ERROR)
            return HttpResponse(status=400)

        fname = uploaded.name
        lower = fname.lower()
        kind = run.kind
        # Strict file type enforcement
        if kind == "aV":
            if not (lower.endswith(".glb") or lower.endswith(".gltf")):
                self.message_user(
                    request,
                    "For MeshOps runs, only .glb/.gltf uploads are allowed.",
                    level=messages.ERROR,
                )
                return HttpResponse(status=400)
        else:
            if not lower.endswith(".splat"):
                self.message_user(
                    request,
                    "For GSOps runs, only .splat uploads are allowed.",
                    level=messages.ERROR,
                )
                return HttpResponse(status=400)

        orig_path = os.path.join(pub_dir, existing_file)
        if not os.path.isfile(orig_path):
            self.message_user(
                request, "Original final output file missing.", level=messages.ERROR
            )
            raise Http404("Original file missing")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

        # Write uploaded file to a temp file in the same directory
        try:
            fd, tmp_path = tempfile.mkstemp(dir=pub_dir)
            with os.fdopen(fd, "wb") as tmpf:
                for chunk in uploaded.chunks():
                    tmpf.write(chunk)
                tmpf.flush()
                os.fsync(tmpf.fileno())
        except Exception:
            logging.exception(f"Error saving uploaded temp file for run {run.ID}")
            self.message_user(
                request, "Error saving uploaded file.", level=messages.ERROR
            )
            return HttpResponse(status=500)

        backup_name = f"{existing_file}.{timestamp}.bak"
        backup_path = os.path.join(pub_dir, backup_name)

        try:
            # Move original to backup, then atomically move temp to original path
            shutil.move(orig_path, backup_path)
            # Restrict backup file permissions to 0600
            try:
                os.chmod(backup_path, 0o600)
            except Exception:
                logging.exception(f"Failed to chmod backup file {backup_path}")

            os.replace(tmp_path, orig_path)
            # Ensure the replaced final output is world-readable/writeable by owner/group (0664)
            try:
                os.chmod(orig_path, 0o664)
            except Exception:
                logging.exception(f"Failed to chmod replaced file {orig_path}")
        except Exception:
            logging.exception(f"Error replacing final output for run {run.ID}")
            # Ensure temp file removed if exists
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            self.message_user(
                request,
                "Error performing replacement; original preserved as backup.",
                level=messages.ERROR,
            )
            return HttpResponse(status=500)

        # Update Run.notes and log the admin action
        try:
            note = f"Final output replaced by admin {request.user.username} at {datetime.now(timezone.utc).isoformat()} UTC. Original file moved to {backup_name}."
            if run.notes:
                run.notes = run.notes + "\n" + note
            else:
                run.notes = note
            run.save()
            logging.info(
                f"ADMIN RUN OUTPUT REPLACED by {request.user} - run={run.ID} original={existing_file} backup={backup_name} time={timestamp}"
            )
        except Exception:
            logging.exception(
                f"Error updating notes for run {run.ID} after replacement"
            )

        self.message_user(
            request, "Final output successfully replaced.", level=messages.SUCCESS
        )
        # Redirect back to change page
        change_url = reverse("admin:tirtha_run_change", args=[run.ID])

        return redirect(change_url)

    @admin.action(
        description="Download full run directories (uncompressed ZIP) for selected runs",
        permissions=["view"],
    )
    def download_full_runs(self, request, queryset):
        runs = list(queryset.select_related("site"))
        if not runs:
            self.message_user(request, "No runs selected.", level=messages.WARNING)
            return

        site_ids = {r.site_id for r in runs}
        verboseid = (
            _sanitize(runs[0].site.verbose_id)[:50]
            if len(site_ids) == 1
            else "multiple"
        )
        if len(runs) == 1:
            fname = f"{verboseid}_full_{str(runs[0].ID)[:8]}.zip"
        else:
            fname = f"{verboseid}_full_{'_'.join(str(r.ID)[:4] for r in runs)}.zip"

        static_models = _models_root()
        file_tuples = []
        for run in runs:
            run_dir = _run_dir(run)
            if not run_dir or not os.path.isdir(run_dir):
                continue
            base = run_dir if run.status == "Archived" else static_models
            for fp in _walk(run_dir):
                parts = [str(run.ID), os.path.relpath(fp, base)]
                if len(site_ids) > 1:
                    parts.insert(0, _sanitize(run.site.verbose_id)[:50])
                file_tuples.append((fp, os.path.join(*parts)))

        return zip_response(
            file_tuples,
            fname,
            f"ADMIN RUNS FULL DOWNLOAD by {request.user} - "
            f"runs={[str(r.ID) for r in runs]} filename={fname}",
        )

    actions = ["download_final_outputs", "download_full_runs", "trigger_gaussian_cut"]


@admin.register(RunStage)
class RunStageAdmin(admin.ModelAdmin):
    """
    Stages across every run and contribution.

    The inlines answer "what happened in this run". This answers the questions
    the inlines cannot: how long `mapper` takes as image counts grow, which
    stage fails most often, what the archive says about scaling (internal design notes
    10.4). Read-only for the same reason the inlines are.
    """

    def owner_link(self, obj):
        if obj.run_id:
            url = reverse("admin:tirtha_run_change", args=[obj.run_id])
            return format_html('<a href="{}">Run {}</a>', url, obj.run_id)
        url = reverse("admin:tirtha_contribution_change", args=[obj.contribution_id])
        return format_html('<a href="{}">Contribution {}</a>', url, obj.contribution_id)

    owner_link.short_description = "Belongs to"

    def scope(self, obj):
        return obj.scope

    scope.short_description = "Scope"

    def took(self, obj):
        return "--" if obj.duration_s is None else f"{obj.duration_s:.1f}s"

    took.short_description = "Duration"

    list_display = (
        "name",
        "scope",
        "owner_link",
        "attempt",
        "status",
        "started_at",
        "took",
        "exit_code",
    )
    list_filter = ("status", "name")
    search_fields = ("name", "run__ID", "contribution__ID", "command", "notes")
    list_select_related = ("run", "contribution")
    list_per_page = 100
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]
