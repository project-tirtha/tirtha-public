"""Admin for `Contributor`."""

from django.contrib import admin, messages
from django.db.models import OuterRef
from django.utils.translation import ngettext

# Local imports
from ..models import (
    Contribution,
    Contributor,
    Image,
)
from .common import _count
from .contributions import ContributionInlineContributor
from .permissions import _may


@admin.register(Contributor)
class ContributorAdmin(admin.ModelAdmin):
    @admin.display(description="Contribution Count", ordering="_contrib_count")
    def contrib_count(self, obj):
        count = getattr(obj, "_contrib_count", None)
        return obj.contributions.count() if count is None else count

    @admin.display(description="Total Image Count", ordering="_image_count")
    def image_count(self, obj):
        count = getattr(obj, "_image_count", None)
        if count is None:
            count = Image.objects.filter(contribution__contributor=obj).count()
        return count

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _contrib_count=_count(
                    Contribution.objects.filter(contributor=OuterRef("pk")),
                    "contributor",
                ),
                _image_count=_count(
                    Image.objects.filter(contribution__contributor=OuterRef("pk")),
                    "contribution__contributor",
                ),
            )
        )

    def has_moderate_permission(self, request, obj=None):
        """Banning is a sanction on a person, not an edit to a row."""
        return self.has_change_permission(request, obj) and _may(
            request, "moderate_contributor"
        )

    @admin.action(description="Activate selected contributors", permissions=["change"])
    def activate_contributors(self, request, queryset):
        updated = queryset.update(active=True)
        self.message_user(
            request,
            ngettext(
                "%d contributor was successfully activated.",
                "%d contributors were successfully activated.",
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    @admin.action(
        description="Deactivate selected contributors", permissions=["change"]
    )
    def deactivate_contributors(self, request, queryset):
        updated = queryset.update(active=False)
        self.message_user(
            request,
            ngettext(
                "%d contributor was successfully deactivated.",
                "%d contributors were successfully deactivated.",
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    @admin.action(description="Ban selected contributors", permissions=["moderate"])
    def ban_contributors(self, request, queryset):
        updated = queryset.update(banned=True)
        self.message_user(
            request,
            ngettext(
                "%d contributor was successfully banned.",
                "%d contributors were successfully banned.",
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    @admin.action(description="Unban selected contributors", permissions=["moderate"])
    def unban_contributors(self, request, queryset):
        updated = queryset.update(banned=False)
        self.message_user(
            request,
            ngettext(
                "%d contributor was successfully unbanned.",
                "%d contributors were successfully unbanned.",
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    actions = [
        activate_contributors,
        deactivate_contributors,
        ban_contributors,
        unban_contributors,
    ]
    readonly_fields = (
        "ID",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            "Contributor Details",
            {
                "fields": (
                    "ID",
                    ("created_at", "updated_at"),
                    ("name", "email"),
                    "user",
                    "active",
                    "banned",
                    "ban_reason",
                )
            },
        ),
        (
            "Public profile",
            {
                # Opt-in, and the contributor's to opt into: an operator setting
                # this on someone's behalf publishes a name that was given for
                # sign-in, not for display (internal design notes).
                "description": (
                    "Off by default. These are the contributor's own settings -- "
                    "change them only at their request."
                ),
                "fields": ("profile_public", "display_name", "handle"),
                "classes": ("collapse",),
            },
        ),
    )
    autocomplete_fields = ("user",)
    inlines = [ContributionInlineContributor]
    list_filter = (
        "active",
        "banned",
        "profile_public",
    )
    list_display = (
        "ID",
        "name",
        "email",
        "created_at",
        "updated_at",
        "contrib_count",
        "image_count",
        "active",
        "banned",
    )
    list_per_page = 50
