"""Admin for `Image`, and the Image inline."""

from django import forms
from django.contrib import admin, messages
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import ngettext

# Local imports
from ..models import (
    Image,
)
from .common import _file_url, _image_disposition, _thumb
from .permissions import _may


class ImageInlineContribution(admin.TabularInline):
    """
    Shows images in the Contribution admin page

    """

    def get_image(self, obj):
        return _thumb(_file_url(obj.image), size=160)

    get_image.short_description = "Preview"

    def image_link(self, obj):
        url = reverse("admin:tirtha_image_change", args=[obj.ID])
        return format_html('<a href="{}">{}</a>', url, obj.ID)

    image_link.short_description = "Image Link"

    def image_label(self, obj):
        return _image_disposition(obj).upper()

    image_label.short_description = "Label"

    model = Image
    readonly_fields = ("get_image", "image_link", "image_label")
    fields = (
        "image_link",
        "get_image",
        "image_label",
    )
    extra = 0
    max_num = 0
    can_delete = True


def _moderate(queryset, **fields):
    """
    Apply a moderation verdict row by row, so the signals actually run.

    Every one of these actions used to call `queryset.update(...)`, which issues
    a single UPDATE and fires no `pre_save`/`post_save` signals -- documented
    Django behaviour, and `mark_good` even carried a `FIXME` saying so. But the
    entire NSFW quarantine lives in `pre_save_image`: it is the signal that moves
    a rejected file out of the served tree into `images/nsfw/` and rewrites
    `image.name`. So a moderator selected an image, was told "1 image was
    successfully marked as NSFW", and the file stayed exactly where it was and
    kept being served.

    One save per row is slower than one UPDATE. These act on a hand-selected
    admin queryset, and the alternative is a safety control that reports success
    without doing anything.
    """
    updated = 0
    for image in queryset:
        for field, value in fields.items():
            setattr(image, field, value)
        # A full save, deliberately: `pre_save_image` moves the file and then
        # rewrites `instance.image.name` to the new path. With
        # `update_fields=["safety_status"]` that rewrite is never persisted, so
        # the file moves on disk while the row still points at where it used to
        # be -- a quarantined image that the database says is still in the
        # served tree. Narrowing the write here would silently undo the signal.
        image.save()
        updated += 1
    return updated


class ImageAdminForm(forms.ModelForm):
    """
    Lets an operator attach a segmentation mask to one image.

    `Image.mask` has been on the model since the Gaussian-cut work, and
    `attach_masks` pairs a whole batch by filename -- but no UI could supply
    one. `ImageAdmin.fieldsets` never listed the field, so production sat at
    19,623 images and 0 masks with the consuming half (`RunAdmin.masks_available`
    and `trigger_gaussian_cut`) fully built and nothing to consume.

    The upload goes through `uploads.prepare_mask`, the same gate the
    contributor path uses, and what is stored is that gate's *output*: a
    single-channel PNG named after the image. Storing the editor's file as it
    came would let two mistakes through that the gate exists to catch. An RGBA
    export whose shape lives in alpha over uniform white reads as "everything
    is subject" to anything that samples one channel; and a mask drawn on a
    downsized copy is skipped silently by the cut, so the operator sees a cut
    that removed nothing and never learns why.
    """

    class Meta:
        model = Image
        fields = "__all__"

    def clean_mask(self):
        upload = self.cleaned_data.get("mask")
        # `False` is the clear checkbox and a `FieldFile` is the unchanged
        # current value. Only a fresh upload has anything to validate.
        if not isinstance(upload, UploadedFile):
            return upload

        # Imported where it is used, as `trigger_gaussian_cut` does with its
        # task, so the intake modules stay off the admin's import path.
        from ..uploads import MaskRejected, prepare_mask

        try:
            expected = (self.instance.image.width, self.instance.image.height)
        except Exception as exc:  # noqa: BLE001
            raise forms.ValidationError(
                f"Could not read the image this mask is for ({exc}), so the "
                "mask's size cannot be checked against it."
            ) from exc

        try:
            data = prepare_mask(upload.read(), upload.name, expected_size=expected)
        except MaskRejected as exc:
            raise forms.ValidationError(str(exc)) from exc

        return ContentFile(data, name=f"{self.instance.ID}.png")


class HasMaskFilter(admin.SimpleListFilter):
    """
    "Which images still need a mask" is the question before firing the cut.

    The predicate is the one `RunAdmin.get_queryset` counts with (`~Q(mask="")`),
    so the number of rows this filter shows agrees with the Masks column on the
    run -- two different notions of "masked" would have the operator chasing a
    discrepancy that is not there.
    """

    title = "mask"
    parameter_name = "has_mask"

    def lookups(self, request, model_admin):
        return (("yes", "Has a mask"), ("no", "No mask"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.exclude(mask="")
        if self.value() == "no":
            return queryset.filter(mask="")
        return queryset


@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    form = ImageAdminForm

    def note(self, obj):
        # `Label` was dropped in migration 0016 (internal design notes); this still
        # told operators to use it.
        return (
            "PLEASE USE THE WEB INTERFACE TO ADD IMAGES.\nUSE THE MODERATION "
            "ACTIONS ON THE LIST PAGE (Good / Bad / NSFW / safe) FOR MANUAL "
            "MODERATION.\nADD A REMARK IN `Remark` IF YOU OVERRIDE A VERDICT."
        )

    def get_thumbnail(self, obj):
        # The change form: one image, so it can be large. Still lazy.
        return _thumb(_file_url(obj.image), size=400)

    get_thumbnail.short_description = "Preview"

    @admin.display(description="Preview")
    def list_thumbnail(self, obj):
        return _thumb(_file_url(obj.image))

    def get_mask_thumbnail(self, obj):
        if not obj or not obj.mask:
            return "No mask yet."
        # On a dark ground so a mostly-black mask is visibly a mask and not a
        # broken image.
        return _thumb(_file_url(obj.mask), size=400, background="#111")

    get_mask_thumbnail.short_description = "Mask preview"

    @admin.display(boolean=True, description="Mask", ordering="mask")
    def has_mask(self, obj):
        return bool(obj.mask)

    def save_model(self, request, obj, form, change):
        """
        Keep `mask_source` truthful, and the mask at its one canonical path.

        `mask_source` is read-only on the form because it is provenance, not a
        setting. A SAM3 mask an operator redraws by hand *is* an admin mask
        now, and a typed field would let the file and its attribution
        disagree. So the attribution follows the file: "admin" when one arrives
        through this form, empty when the clear box is ticked, and untouched by
        a save that never changed `mask` -- a contributor's attribution must
        survive a moderator editing the remark.

        The previous file goes before the save, not after. `set_mask` names
        every mask `<image ID>.png` and storage never overwrites, so with the
        old file still present the replacement lands at `<ID>_AbC123.png`.
        Everything downstream resolves the file through `mask.name`, so that
        still works -- but it breaks the promise `set_mask` makes, one mask at
        one predictable path per image, which is what an operator looking in
        `masks/` relies on.
        """
        if "mask" in form.changed_data:
            previous = (
                Image.objects.filter(pk=obj.pk).values_list("mask", flat=True).first()
                if change
                else ""
            )
            if previous:
                try:
                    default_storage.delete(previous)
                except (NotImplementedError, OSError) as exc:
                    # Not fatal: the new mask still saves, at a suffixed name.
                    self.message_user(
                        request,
                        f"Could not remove the previous mask {previous}: {exc}",
                        messages.WARNING,
                    )
            obj.mask_source = "admin" if obj.mask else ""
        super().save_model(request, obj, form, change)

    def get_site_id_verbose(self, obj):
        return obj.contribution.site.verbose_id

    get_site_id_verbose.short_description = "Site ID (Verbose)"

    def get_contributor_link(self, obj):
        url = reverse(
            "admin:tirtha_contributor_change", args=[obj.contribution.contributor.ID]
        )
        return format_html(
            '<a href="{}">{}</a>', url, obj.contribution.contributor.name
        )

    get_contributor_link.short_description = "Contributor Link"

    def has_moderate_permission(self, request, obj=None):
        """NSFW quarantine and its release decide what the public is served."""
        return self.has_change_permission(request, obj) and _may(
            request, "moderate_image"
        )

    @admin.action(description="Mark selected images as Good", permissions=["change"])
    def mark_good(self, request, queryset):
        updated = _moderate(queryset, admin_verdict="accept")
        self.message_user(
            request,
            ngettext(
                "%d image was successfully marked as Good.",
                "%d images were successfully marked as Good.",
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    @admin.action(description="Mark selected images as Bad", permissions=["change"])
    def mark_bad(self, request, queryset):
        updated = _moderate(queryset, admin_verdict="reject")
        self.message_user(
            request,
            ngettext(
                "%d image was successfully marked as Bad.",
                "%d images were successfully marked as Bad.",
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    @admin.action(description="Mark selected images as NSFW", permissions=["moderate"])
    def mark_nsfw(self, request, queryset):
        updated = _moderate(queryset, safety_status="rejected")
        self.message_user(
            request,
            ngettext(
                "%d image was successfully marked as NSFW.",
                "%d images were successfully marked as NSFW.",
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    @admin.action(
        description="Mark selected images as safe (release quarantine)",
        permissions=["moderate"],
    )
    def mark_safe(self, request, queryset):
        """
        The way back out of quarantine, which did not exist.

        `mark_nsfw` moves a file into `images/nsfw/` and there were only three
        actions, none of which cleared `safety_status` -- so once an image was
        rejected, nothing in the admin could release it. That is the wrong shape
        for a moderation control: a wrong call has to be reversible by the person
        who made it.

        Writes `safe`, not `pending`. `pending` means "not checked" and nothing
        ever re-checks it -- the NSFW pass runs once, at ingest. So releasing to
        `pending` moved the file back out of `images/nsfw/` and still left it
        outside `reconstructable_images()`, which filters on `safety_status
        ="safe"`: the moderator was told the image was released, and no
        reconstruction would ever use it again. A moderator clicking "mark as
        safe" is the check.
        """
        updated = _moderate(queryset, safety_status="safe")
        self.message_user(
            request,
            ngettext(
                "%d image was released from quarantine.",
                "%d images were released from quarantine.",
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    @admin.action(
        description="Remove the mask from selected images", permissions=["change"]
    )
    def clear_mask(self, request, queryset):
        """
        The way back out. A wrong mask is worse than none -- the cut votes with
        it -- so removing one has to be as easy as adding one, and it has to
        take the attribution with it: a row with no file and
        `mask_source="admin"` says something happened here that did not.
        """
        cleared = 0
        for image in queryset.exclude(mask=""):
            image.mask.delete(save=False)
            image.mask_source = ""
            # Narrow on purpose, unlike `_moderate`: `pre_save_image` only moves
            # a file when `safety_status` changes, and it does not here.
            image.save(update_fields=["mask", "mask_source"])
            cleared += 1
        self.message_user(
            request,
            ngettext(
                "%d mask was removed.",
                "%d masks were removed.",
                cleared,
            )
            % cleared,
            messages.SUCCESS,
        )

    actions = [mark_good, mark_bad, mark_nsfw, mark_safe, clear_mask]
    readonly_fields = (
        "ID",
        "contribution",
        "created_at",
        "image",
        "note",
        "get_thumbnail",
        "get_site_id_verbose",
        "get_contributor_link",
        "get_mask_thumbnail",
        "mask_source",
        "safety_moderated_at",
    )
    fieldsets = (
        (
            "Image Details",
            {
                "fields": (
                    ("note"),
                    ("ID"),
                    ("get_site_id_verbose"),
                    ("get_contributor_link"),
                    ("contribution"),
                    ("created_at"),
                    ("image", "get_thumbnail"),
                    ("safety_status", "safety_moderated_at"),
                    ("remark"),
                )
            },
        ),
        (
            "Segmentation mask",
            {
                "description": (
                    "A PNG with exactly this image's pixel dimensions: white "
                    "(or opaque) where the monument is, black (or transparent) "
                    "everywhere else. It is checked and normalised on save. To "
                    "mask many images at once, use <em>Upload masks</em> on "
                    "the site's page."
                ),
                "fields": (
                    ("mask", "get_mask_thumbnail"),
                    ("mask_source"),
                ),
            },
        ),
    )
    list_filter = ("safety_status", "admin_verdict", HasMaskFilter)
    list_display = (
        "ID",
        "created_at",
        "contribution",
        "safety_status",
        "admin_verdict",
        "has_mask",
        "list_thumbnail",
    )
    # 50, not 200: each row is an original photograph (see `_thumb`).
    list_per_page = 50
    # `contribution` is a FK column; `__str__` is the ID, but Django still
    # fetches the row to call it.
    list_select_related = ("contribution",)
