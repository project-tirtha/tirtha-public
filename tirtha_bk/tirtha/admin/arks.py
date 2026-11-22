"""Admin for `ARK`."""

from django.contrib import admin
from django.db.models import OuterRef

# Local imports
from ..models import (
    ARK,
    Run,
)
from .common import _count


@admin.register(ARK)
class ARKAdmin(admin.ModelAdmin):
    def site_id_verbose(self, obj):
        return obj.run.site.verbose_id

    site_id_verbose.short_description = "Site ID (Verbose)"

    def get_run(self, obj):
        return obj.run

    get_run.short_description = "Run"

    @admin.display(description="Total Image Count", ordering="_image_count")
    def image_count(self, obj):
        count = getattr(obj, "_image_count", None)
        return obj.run.images.count() if count is None else count

    def get_queryset(self, request):
        # `site_id_verbose` walks ark -> run -> site and the count walked run
        # -> images: three queries a row.
        return (
            super()
            .get_queryset(request)
            .select_related("run__site")
            .annotate(
                _image_count=_count(
                    Run.images.through.objects.filter(run__ark=OuterRef("pk")),
                    "run",
                )
            )
        )

    readonly_fields = (
        "ark",
        "get_run",
        "site_id_verbose",
        "image_count",
        "naan",
        "shoulder",
        "assigned_name",
        "created_at",
        "url",
        "metadata",
    )
    fieldsets = (
        (
            "ARK Details",
            {
                "fields": (
                    "ark",
                    "url",
                    "created_at",
                    "get_run",
                    "site_id_verbose",
                    "image_count",
                    ("naan", "shoulder", "assigned_name"),
                    "metadata",
                    "commitment",
                )
            },
        ),
    )
    list_display = ("ark", "site_id_verbose", "get_run", "created_at", "image_count")
    list_per_page = 50
