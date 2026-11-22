"""Admin for `SiteSubmissionRequest`."""

from django.contrib import admin, messages
from django.utils import timezone as dj_timezone
from django.utils.html import format_html, mark_safe
from django.utils.translation import ngettext

# Local imports
from ..models import (
    SiteSubmissionRequest,
)


@admin.register(SiteSubmissionRequest)
class SiteSubmissionRequestAdmin(admin.ModelAdmin):
    """
    Review queue for public site requests (internal design notes).

    The two fields an operator most needs are the ones the old Google Form
    buried in a spreadsheet column: whether the area is access-restricted, and
    who has purview over it. Both are on the changelist for that reason.
    """

    def pin(self, obj):
        if not obj.has_pin:
            return "--"
        url = (
            f"https://www.openstreetmap.org/?mlat={obj.latitude}"
            f"&mlon={obj.longitude}#map=17/{obj.latitude}/{obj.longitude}"
        )
        return format_html(
            '<a href="{}" target="_blank">{}, {}</a>',
            url,
            f"{obj.latitude:.5f}",
            f"{obj.longitude:.5f}",
        )

    pin.short_description = "Dropped pin"

    def access(self, obj):
        # Three states, not two: "not answered" must not read as "no".
        if obj.restricted_access is None:
            return mark_safe('<span style="color:#a60">not answered</span>')
        if obj.restricted_access:
            return mark_safe('<span style="color:#b00">RESTRICTED</span>')
        return "open"

    access.short_description = "Access"

    @admin.action(description="Approve selected requests", permissions=["change"])
    def approve_requests(self, request, queryset):
        updated = queryset.update(status="approved", reviewed_at=dj_timezone.now())
        self.message_user(
            request,
            ngettext(
                "%d request approved. Create the Site from it -- approval does "
                "not create one automatically.",
                "%d requests approved. Create the Sites from them -- approval "
                "does not create them automatically.",
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    @admin.action(description="Reject selected requests", permissions=["change"])
    def reject_requests(self, request, queryset):
        updated = queryset.update(status="rejected", reviewed_at=dj_timezone.now())
        self.message_user(request, f"{updated} request(s) rejected.", messages.INFO)

    actions = [approve_requests, reject_requests]
    list_display = (
        "name",
        "district",
        "state",
        "status",
        "access",
        "purview",
        "pin",
        "created_at",
    )
    list_filter = ("status", "purview", "restricted_access", "country")
    search_fields = (
        "name",
        "description",
        "affiliation",
        "source_url",
        # An operator's most common question about a request is "who asked?",
        # and for an anonymous one these are the only answer there is.
        "requester_name",
        "requester_email",
    )
    readonly_fields = ("ID", "pin", "created_at", "updated_at")
    fieldsets = (
        (
            "Request",
            {
                "fields": (
                    "ID",
                    ("name", "country"),
                    ("state", "district"),
                    "description",
                    ("latitude", "longitude"),
                    "pin",
                )
            },
        ),
        (
            "Requester",
            {
                "fields": (
                    "requester",
                    "requester_name",
                    "requester_email",
                    "affiliation",
                    "source_url",
                )
            },
        ),
        (
            "Legality and purview",
            {
                "description": (
                    "Access restriction is a contributor-safety gate, not a "
                    "formality -- an unanswered value is not a 'no'."
                ),
                "fields": ("restricted_access", "purview"),
            },
        ),
        (
            "Consents",
            {
                "description": "Two separate acknowledgements, as collected.",
                "fields": ("accepted_terms", "accepted_privacy"),
            },
        ),
        (
            "Review",
            {"fields": ("status", "review_notes", "reviewed_at", "created_site")},
        ),
    )
    list_per_page = 50
