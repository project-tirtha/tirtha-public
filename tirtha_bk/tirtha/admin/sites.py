"""Admin for `Site`."""

import json
import os
from pathlib import Path

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.db.models import OuterRef
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone as dj_timezone
from django.utils.html import format_html, mark_safe
from django.utils.translation import ngettext

# Local imports
from ..models import (
    Contribution,
    Image,
    Site,
)
from ..overpass import OverpassClient, OverpassUnavailable
from ..turntable import TURNTABLE_SUFFIXES, find_turntable, turntable_path
from .common import _count, _file_url, _site_thumb_url, _thumb
from .contributions import ContributionInlineMesh
from .downloads import _image_files, _sanitize, zip_response
from .permissions import _may


class SiteAdminForm(forms.ModelForm):
    """
    Adds a hand-uploaded map preview to the Site form.

    `backfill_turntables` renders these from the published model, and for three
    sites it refuses to -- the reconstruction is too poor to frame, and the
    honest output is nothing. Those are exactly the sites a curator might want
    to hand a short clip made in a viewer, so there has to be a way in that is
    not a management command.

    Not a model field. The preview is addressed by convention from the
    thumbnail's own path (`turntable.find_turntable`), the same as the two
    thumbnail derivatives, so adding one needs no migration and there is no
    second source of truth about where the file is.
    """

    turntable_upload = forms.FileField(
        required=False,
        label="Map preview",
        help_text=(
            "MP4, WebM, GIF or animated WebP. Shown when someone hovers the "
            "site's pin. Square, and small -- the rendered ones are 160x160 "
            "and about 15 KB. Replaces whatever is there now."
        ),
    )
    turntable_clear = forms.BooleanField(
        required=False,
        label="Remove the current preview",
        help_text="The site falls back to its still thumbnail.",
    )

    class Meta:
        model = Site
        fields = "__all__"

    # Big enough for a few seconds of 160px video, small enough that nobody
    # uploads a phone clip by accident.
    MAX_UPLOAD = 4 * 1024 * 1024

    def clean_turntable_upload(self):
        upload = self.cleaned_data.get("turntable_upload")
        if not upload:
            return upload

        suffix = Path(upload.name).suffix.lower()
        if suffix not in TURNTABLE_SUFFIXES:
            raise forms.ValidationError(
                f"{suffix or 'That'} is not a format the map can show. "
                f"Use one of: {', '.join(TURNTABLE_SUFFIXES)}."
            )
        if upload.size > self.MAX_UPLOAD:
            raise forms.ValidationError(
                f"{upload.size // 1024} KB is too large for a hover preview "
                f"(limit {self.MAX_UPLOAD // 1024} KB). The rendered ones are "
                "about 15 KB."
            )
        return upload

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("turntable_upload") and cleaned.get("turntable_clear"):
            raise forms.ValidationError(
                "Choose one: upload a new preview, or remove the current one."
            )
        if cleaned.get("turntable_upload") and not self.instance.thumbnail:
            # The path is derived from the thumbnail's, so there is nowhere to
            # put it until the site has one.
            raise forms.ValidationError(
                "Give the site a thumbnail first -- the preview is stored "
                "beside it, and its name is derived from the thumbnail's."
            )
        return cleaned


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    form = SiteAdminForm

    def get_turntable(self, obj):
        """The preview as it will actually appear, playing, or why there isn't one."""
        if not obj or not obj.thumbnail:
            return "No thumbnail yet, so there is nowhere to store a preview."

        name = find_turntable(obj.thumbnail.name, default_storage)
        if not name:
            return format_html(
                '<span style="color:#888">No preview. The map shows the still '
                "thumbnail. Render one with <code>manage.py "
                "backfill_turntables --site {}</code>, or upload one below.</span>",
                obj.ID,
            )

        url = default_storage.url(name)
        size = ""
        try:
            size = f" &middot; {default_storage.size(name) // 1024} KB"
        except (NotImplementedError, OSError):
            pass

        if name.endswith((".mp4", ".webm")):
            tag = format_html(
                '<video src="{}" width="160" height="160" autoplay muted loop '
                'playsinline style="border-radius:4px;background:#111"></video>',
                url,
            )
        else:
            tag = format_html(
                '<img src="{}" width="160" height="160" alt="" '
                'style="border-radius:4px;background:#111">',
                url,
            )
        return format_html(
            '<div>{}<div style="font:11px monospace;color:#888;margin-top:4px">{}{}</div></div>',
            tag,
            name.rsplit("/", 1)[-1],
            mark_safe(size),
        )

    get_turntable.short_description = "Map preview (hover)"

    def save_model(self, request, obj, form, change):
        """
        Write or remove the hand-uploaded preview alongside the save.

        After `super()`, because the path is derived from `obj.thumbnail.name`
        and a thumbnail changed in the same submission has to be saved first or
        the preview lands beside the old name.
        """
        super().save_model(request, obj, form, change)

        upload = form.cleaned_data.get("turntable_upload")
        clear = form.cleaned_data.get("turntable_clear")
        if not upload and not clear:
            return

        existing = find_turntable(obj.thumbnail.name, default_storage)

        if clear:
            if existing:
                try:
                    default_storage.delete(existing)
                except (NotImplementedError, OSError) as exc:
                    self.message_user(
                        request, f"Could not remove {existing}: {exc}", messages.ERROR
                    )
                    return
            self.message_user(
                request,
                f"{obj.name} falls back to its still thumbnail on the map.",
                messages.SUCCESS,
            )
            return

        suffix = Path(upload.name).suffix.lower()
        target = turntable_path(obj.thumbnail.name).replace(".mp4", suffix)

        """
        Write, then swap. The old preview is not removed until the new one is
        on disk.

        This used to delete first, so a `default_storage.save` that then failed
        left the site with neither -- it lost a working preview to store a
        broken one. `turntable.assemble` already writes to a temporary name and
        `os.replace`s it for the same reason.

        The incoming file goes to a scratch name (storage may rename that; it
        does not matter, nothing looks for it) and is moved into place with
        `os.replace`, which is atomic within a filesystem. That needs a local
        storage backend, which is what `MEDIA_ROOT` is here and what the rest of
        this module already assumes.
        """
        try:
            scratch = default_storage.save(f"{target}.incoming", upload)
        except (NotImplementedError, OSError) as exc:
            self.message_user(
                request, f"Could not store the preview: {exc}", messages.ERROR
            )
            return

        try:
            os.replace(default_storage.path(scratch), default_storage.path(target))
        except (NotImplementedError, OSError) as exc:
            try:
                default_storage.delete(scratch)
            except (NotImplementedError, OSError):
                pass
            self.message_user(
                request, f"Could not store the preview: {exc}", messages.ERROR
            )
            return

        # Only now, with the new file in place. A different extension from last
        # time leaves the old one behind otherwise, and `find_turntable` -- which
        # tries `.mp4` first -- would go on serving it.
        if existing and existing != target:
            try:
                default_storage.delete(existing)
            except (NotImplementedError, OSError) as exc:
                self.message_user(
                    request,
                    f"Stored the new preview, but could not remove {existing}: {exc}",
                    messages.WARNING,
                )

        self.message_user(
            request,
            f"{obj.name} now shows this preview when its pin is hovered.",
            messages.SUCCESS,
        )

    def get_preview(self, obj):
        return _thumb(_file_url(obj.preview), obj.verbose_id, size=400)

    get_preview.short_description = "Preview"

    def get_thumbnail(self, obj):
        return _thumb(_file_url(obj.thumbnail), obj.verbose_id, size=400)

    get_thumbnail.short_description = "Thumbnail"

    @admin.display(description="Thumbnail")
    def list_thumbnail(self, obj):
        return _thumb(_site_thumb_url(obj), obj.verbose_id)

    def site_id_verbose(self, obj):
        return obj.verbose_id

    site_id_verbose.short_description = "ID (Verbose)"

    def external_links(self, obj):
        """
        The OSM element and Wikidata item this site was matched to.

        Clickable because verifying a seeded coordinate means *looking* at the
        thing it was matched to, and copying an ID into a URL by hand is how
        that check stops happening (internal design notes).
        """
        links = []
        if obj.osm_type and obj.osm_id:
            url = f"https://www.openstreetmap.org/{obj.osm_type}/{obj.osm_id}"
            version = f" (v{obj.osm_version})" if obj.osm_version else ""
            links.append(
                format_html(
                    '<a href="{}" target="_blank">OSM {}/{}</a>{}',
                    url,
                    obj.osm_type,
                    obj.osm_id,
                    version,
                )
            )
        if obj.wikidata_qid:
            url = f"https://www.wikidata.org/wiki/{obj.wikidata_qid}"
            links.append(
                format_html(
                    '<a href="{}" target="_blank">{}</a>', url, obj.wikidata_qid
                )
            )
        # Each part is already escaped by format_html above; the separator is a
        # constant. Joining SafeStrings yields a plain str, so it needs
        # re-marking -- this is not the unescaped mark_safe(join(f"...")) shape.
        return mark_safe(" · ".join(links)) if links else "--"

    external_links.short_description = "External links"

    @admin.display(description="Contribution Count", ordering="_contrib_count")
    def contrib_count(self, obj):
        count = getattr(obj, "_contrib_count", None)
        return obj.contributions.count() if count is None else count

    @admin.display(description="Total Image Count", ordering="_image_count")
    def image_count(self, obj):
        count = getattr(obj, "_image_count", None)
        if count is None:
            count = Image.objects.filter(contribution__site=obj).count()
        return count

    def get_queryset(self, request):
        # Both counts were a query per row: 2 x 25 on every changelist load.
        return (
            super()
            .get_queryset(request)
            .annotate(
                _contrib_count=_count(
                    Contribution.objects.filter(site=OuterRef("pk")), "site"
                ),
                _image_count=_count(
                    Image.objects.filter(contribution__site=OuterRef("pk")),
                    "contribution__site",
                ),
            )
        )

    def has_publish_permission(self, request, obj=None):
        """Hiding or unhiding a site changes what the public map shows."""
        return self.has_change_permission(request, obj) and _may(
            request, "publish_site"
        )

    @admin.action(
        description="Mark selected sites as completed", permissions=["change"]
    )
    def mark_completed(self, request, queryset):
        updated = queryset.update(completed=True, updated_at=dj_timezone.now())
        self.message_user(
            request,
            ngettext(
                "%d site was successfully marked as completed.",
                "%d sites were successfully marked as completed.",
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    @admin.action(
        description="Mark selected sites as incomplete", permissions=["change"]
    )
    def mark_incomplete(self, request, queryset):
        # `updated_at` by hand: `update()` skips `auto_now`, and the public
        # API's cache is keyed on it (`api._catalogue_version`).
        updated = queryset.update(completed=False, updated_at=dj_timezone.now())
        self.message_user(
            request,
            ngettext(
                "%d site was successfully marked as incomplete.",
                "%d sites were successfully marked as incomplete.",
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    @admin.action(description="Mark selected sites as hidden", permissions=["publish"])
    def mark_hidden(self, request, queryset):
        updated = queryset.update(hidden=True, updated_at=dj_timezone.now())
        self.message_user(
            request,
            ngettext(
                "%d site was successfully marked as hidden.",
                "%d sites were successfully marked as hidden.",
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    @admin.action(
        description="Mark selected sites as not hidden", permissions=["publish"]
    )
    def mark_not_hidden(self, request, queryset):
        updated = queryset.update(hidden=False, updated_at=dj_timezone.now())
        self.message_user(
            request,
            ngettext(
                "%d site was successfully marked as not hidden.",
                "%d sites were successfully marked as not hidden.",
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    @admin.action(
        description="Find on OpenStreetMap (seed coordinates)", permissions=["change"]
    )
    def seed_from_overpass(self, request, queryset):
        """
        Resolve selected sites against the Overpass mirror, then let a human
        confirm each one.

        Deliberately a two-step action. Auto-applying the top-ranked hit would
        be wrong often enough to matter -- two distinct `Gopinath Temple` rows
        in different districts are indistinguishable to a scorer, and a
        confidently wrong coordinate is worse than an empty one because nothing
        downstream will question it (internal design notes).
        """
        client = OverpassClient()
        if not client.configured:
            self.message_user(
                request,
                "OVERPASS_URL is not set. Overpass is optional infrastructure -- "
                "see build/setup-overpass.sh to stand the mirror up.",
                messages.WARNING,
            )
            return None

        if request.POST.get("apply"):
            return self._apply_overpass_choices(request, queryset)

        results, errors = [], []
        for site in queryset:
            try:
                candidates = client.find_site(site)
            except OverpassUnavailable as exc:
                # One unreachable mirror should not lose the operator the work
                # already done on the other sites in the batch.
                errors.append(f"{site.name}: {exc}")
                break
            results.append((site, candidates))

        for err in errors:
            self.message_user(request, err, messages.ERROR)
        if not results:
            return None

        return render(
            request,
            "admin/tirtha/site/overpass_seed.html",
            {
                **self.admin_site.each_context(request),
                "title": "Seed coordinates from OpenStreetMap",
                "results": results,
                "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
                "media": self.media,
            },
        )

    def _apply_overpass_choices(self, request, queryset):
        """Persist the operator's confirmations."""
        by_id = {str(s.pk): s for s in queryset}
        applied, skipped = 0, 0
        for key, value in request.POST.items():
            if not key.startswith("choice-"):
                continue
            site = by_id.get(key[len("choice-") :])
            if site is None or not value:
                continue
            if value == "skip":
                skipped += 1
                continue
            # "way/123456789/7" -- type, id, and the version we resolved at.
            try:
                osm_type, osm_id, version, lat, lon = value.split("/")
            except ValueError:
                continue
            site.osm_type = osm_type
            site.osm_id = int(osm_id)
            # No version means the mirror was imported without OVERPASS_META=yes,
            # so drift detection cannot work for this row. Record the absence
            # rather than a zero (internal design notes).
            site.osm_version = int(version) if version not in ("", "None") else None
            site.latitude = float(lat)
            site.longitude = float(lon)
            site.location_source = "osm"
            site.location_verified_at = dj_timezone.now()
            qid = request.POST.get(f"qid-{site.pk}", "")
            if qid:
                site.wikidata_qid = qid
            tags = request.POST.get(f"tags-{site.pk}", "")
            if tags:
                try:
                    site.osm_tags = json.loads(tags)
                except (ValueError, TypeError):
                    pass
            site.save()
            applied += 1

        if applied:
            self.message_user(
                request,
                ngettext(
                    "%d site located. Coordinates are from OpenStreetMap (ODbL) -- "
                    "share-alike attaches to published datasets containing them.",
                    "%d sites located. Coordinates are from OpenStreetMap (ODbL) -- "
                    "share-alike attaches to published datasets containing them.",
                    applied,
                )
                % applied,
                messages.SUCCESS,
            )
        if skipped:
            self.message_user(request, f"{skipped} skipped.", messages.INFO)
        return None

    @admin.display(description="Consecutive failed runs")
    def recon_failure_state(self, obj):
        """The scheduler's own count, and when its cool-down ends."""
        from ..scheduler import _max_consecutive_failures, recon_failure_state

        if obj is None or obj.pk is None:
            return "-"
        state = recon_failure_state(obj)
        limit = _max_consecutive_failures()
        if limit <= 0:
            return f"{state.failures} (no limit)"
        if state.cooldown_until is None:
            return f"{state.failures} (limit {limit})"
        until = dj_timezone.localtime(state.cooldown_until).strftime(
            "%Y-%m-%d %H:%M %Z"
        )
        if state.cooling_down():
            return (
                f"{state.failures} (backed off at {limit}; the scheduler skips "
                f"this site until {until}, or until it is reset)"
            )
        return (
            f"{state.failures} (backed off at {limit}; cool-down ended {until}, "
            "so the scheduler may try it again)"
        )

    @admin.action(description="Reset reconstruction back-off", permissions=["change"])
    def reset_recon_backoff(self, request, queryset):
        """
        Let the scheduler retry sites now, rather than when their cool-down
        after repeated failures ends.
        """
        # Not `updated_at`: nothing the public API serves changes.
        updated = queryset.update(recon_failures_reset_at=dj_timezone.now())
        self.message_user(
            request,
            ngettext(
                "Reconstruction back-off reset for %d site.",
                "Reconstruction back-off reset for %d sites.",
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    actions = [
        "mark_completed",
        "mark_incomplete",
        "mark_hidden",
        "mark_not_hidden",
        "download_images_zip_meshes",
        "seed_from_overpass",
        "reset_recon_backoff",
    ]
    readonly_fields = (
        "ID",
        "download_link",
        "upload_masks_link",
        # A display method named in a fieldset has to be declared read-only,
        # or `modelform_factory` raises `Unknown field(s)` and the add and
        # change pages 500. `external_links` was listed under Location without
        # it; `test_mask_admin.TheAdminPagesStillRender` renders both pages.
        "external_links",
        "verbose_id",
        "created_at",
        "updated_at",
        "reconstructed_at",
        "get_preview",
        "get_thumbnail",
        "get_turntable",
        "recon_queued_at",
        "recon_failure_state",
        "recon_failures_reset_at",
    )
    fieldsets = (
        (
            "Site Details",
            {
                "fields": (
                    (
                        "ID",
                        "verbose_id",
                        "download_link",
                        "upload_masks_link",
                    ),
                    ("created_at", "updated_at", "reconstructed_at"),
                    ("status", "completed", "hidden"),
                    ("name", "country", "state", "district"),
                    "description",
                    ("center_image", "denoise"),
                    (
                        "rotaZ",
                        "rotaX",
                        "rotaY",
                        "minObsAng",
                        "orientMesh",
                    ),  # Mimicking <model-viewer> attributes (ZXY)
                    ("thumbnail", "get_thumbnail"),
                    ("preview", "get_preview"),
                    ("get_turntable", "turntable_upload", "turntable_clear"),
                )
            },
        ),
        (
            "Location",
            {
                # The stored coordinate is what renders. Overpass is a seeding
                # tool, never the runtime source -- a mirror syncs upstream
                # diffs, so an unpinned marker could move because someone
                # retagged a way last week (internal design notes).
                "description": (
                    "Select sites on the list page and run <em>Find on "
                    "OpenStreetMap</em> to fill these in. Coordinates taken "
                    "from OSM are ODbL -- share-alike attaches to any dataset "
                    "we publish that includes them (internal design notes)."
                ),
                "fields": (
                    ("latitude", "longitude"),
                    ("location_source", "location_verified_at"),
                    ("osm_type", "osm_id", "osm_version"),
                    "wikidata_qid",
                    "external_links",
                    ("default_zoom", "default_bearing", "default_pitch"),
                ),
            },
        ),
        (
            "Reconstruction scheduling",
            {
                # Read-only: the scheduler owns these. The back-off is reset
                # with the "Reset reconstruction back-off" action.
                "fields": (
                    "recon_queued_at",
                    "recon_failure_state",
                    "recon_failures_reset_at",
                ),
            },
        ),
        (
            "Classification",
            {
                "fields": ("categories", "osm_tags"),
                "classes": ("collapse",),
            },
        ),
    )
    filter_horizontal = ("categories",)
    list_filter = (
        "status",
        "completed",
        "hidden",
        "location_source",
    )
    list_display = (
        "ID",
        "site_id_verbose",
        "name",
        "reconstructed_at",
        "status",
        "completed",
        "hidden",
        "contrib_count",
        "image_count",
        "list_thumbnail",
        "download_link",
    )
    list_per_page = 25
    inlines = [ContributionInlineMesh]  # , RunInlineMesh FIXME: Error while saving

    def get_urls(self):
        from django.urls import path

        urls = super().get_urls()
        my_urls = [
            path(
                "<str:object_id>/download_images/",
                self.admin_site.admin_view(self.download_images_view),
                name="tirtha_mesh_download_images",
            ),
            path(
                "<str:object_id>/upload_masks/",
                self.admin_site.admin_view(self.upload_masks_view),
                name="tirtha_site_upload_masks",
            ),
        ]
        return my_urls + urls

    def download_images_view(self, request, object_id):
        # Per-site download via admin link: contribid/<disposition>/file
        site = Site.objects.filter(ID=object_id).first()
        if site is None:
            raise Http404("Site not found")
        if not self.has_view_permission(request, site):
            raise PermissionDenied

        verboseid_s = _sanitize(site.verbose_id)[:50]
        fname = f"{verboseid_s}.zip"
        return zip_response(
            _image_files(site.contributions.all(), prefix_site=False),
            fname,
            f"ADMIN MESH ZIP DOWNLOAD by {request.user} - site={verboseid_s} "
            f"filename={fname}",
        )

    def download_link(self, obj):
        try:
            url = reverse("admin:tirtha_mesh_download_images", args=[obj.ID])
            return format_html(
                '<a class="button" href="{}">Download All Contributions</a>', url
            )
        except Exception:
            return ""

    download_link.short_description = "Download"

    def upload_masks_link(self, obj):
        """
        The door to `upload_masks_view`, with the number that says whether it
        is worth opening. Two counts on a single change page, not annotated:
        this is deliberately kept off the changelist, where it would be one
        more per-row query.
        """
        # `Site.ID` defaults to a UUID, so an unsaved site on the add page has a
        # pk; filtering on it would raise, and there is nothing to mask anyway.
        if not obj or obj._state.adding:
            return "--"
        images = Image.objects.filter(contribution__site=obj)
        return format_html(
            '<a class="button" href="{}">Upload masks</a> '
            '<span style="color:#888">{} of {} images masked</span>',
            reverse("admin:tirtha_site_upload_masks", args=[obj.ID]),
            images.exclude(mask="").count(),
            images.count(),
        )

    upload_masks_link.short_description = "Masks"

    def upload_masks_view(self, request, object_id):
        """
        Attach masks to a site's images in bulk, paired by filename.

        The Image change form takes one mask at a time. A site has hundreds of
        images, and an operator who has just run a segmenter over them has a
        folder of PNGs named after them -- this is the folder-sized entrance.
        The pairing rules are `attach_masks`' own and are spelled out on the
        page, because a mask that matches nothing is reported, not stored, and
        the operator has to know what name would have matched.

        `scope=None`, deliberately. The contributor path through `/upload/`
        scopes the search to the contributor's own contribution: attaching a
        mask to a stranger's photograph is an annotation they never agreed to.
        An operator is curating the site, not contributing to it, and masking
        another contributor's image is exactly the job. Widening the search
        does not weaken the guarantee that matters -- two images sharing a name
        are still refused rather than guessed -- it only lets the operator
        reach any image at the site, by its ID if need be.
        """
        try:
            site = Site.objects.get(ID=object_id)
        except Site.DoesNotExist:
            raise Http404("Site not found")
        if not self.has_change_permission(request, site):
            raise PermissionDenied

        change_url = reverse("admin:tirtha_site_change", args=[site.ID])

        if request.method == "POST":
            files = request.FILES.getlist("masks")
            if not files:
                self.message_user(request, "No files were selected.", messages.WARNING)
                return redirect(request.path)

            # Imported where it is used, as `trigger_gaussian_cut` does with its
            # task: `views` drags the whole upload path in behind it, and the
            # admin should not pay for that at URL-load time.
            from ..views import attach_masks

            report = attach_masks(site, files, scope=None, source="admin")
            attached = report["attached"]
            if attached:
                self.message_user(
                    request,
                    ngettext(
                        "%d mask was attached.", "%d masks were attached.", attached
                    )
                    % attached,
                    messages.SUCCESS,
                )
            for name, reason in report["rejected"].items():
                self.message_user(request, f"{name}: {reason}", messages.WARNING)
            return redirect(change_url)

        images = Image.objects.filter(contribution__site=site)
        return render(
            request,
            "admin/tirtha/site/upload_masks.html",
            {
                **self.admin_site.each_context(request),
                "title": f"Upload masks: {site.name}",
                "opts": self.model._meta,
                "original": site,
                "change_url": change_url,
                "image_count": images.count(),
                "masked_count": images.exclude(mask="").count(),
                "media": self.media,
            },
        )

    @admin.action(
        description="Download images as ZIP for selected sites", permissions=["view"]
    )
    def download_images_zip_meshes(self, request, queryset):
        sites = list(queryset)

        if not sites:
            self.message_user(request, "No sites selected.", level=messages.WARNING)
            return

        verboseids = [_sanitize(m.verbose_id)[:50] for m in sites]
        if len(sites) == 1:
            fname = f"{verboseids[0]}.zip"
        else:
            fname = f"multiple_meshes_{'_'.join(v[:4] for v in verboseids)}.zip"

        return zip_response(
            _image_files(
                Contribution.objects.filter(site__in=sites), prefix_site=len(sites) > 1
            ),
            fname,
            f"ADMIN MESH ZIP DOWNLOAD by {request.user} - "
            f"sites={[str(m.ID) for m in sites]} filename={fname}",
        )
