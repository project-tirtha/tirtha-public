"""
Write the thumbnail derivatives for sites saved before they existed.

`Site.save()` writes them from now on; this is for the rows already in the
database. Idempotent, and safe to interrupt -- it skips a site whose files are
already there unless `--force` is given.

    python manage.py backfill_thumbnails --dry-run
    python manage.py backfill_thumbnails
"""

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from tirtha.models import (
    THUMBNAIL_DERIVATIVES,
    Site,
    derivative_path,
    write_thumbnail_derivatives,
)


class Command(BaseCommand):
    help = "Generate the small thumbnail copies used for hover previews and list rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be written and change nothing.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Rewrite derivatives that already exist.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]

        written = skipped = missing = failed = 0
        for site in Site.objects.exclude(thumbnail="").iterator():
            if not site.thumbnail:
                missing += 1
                continue

            existing = [
                kind
                for kind in THUMBNAIL_DERIVATIVES
                if default_storage.exists(derivative_path(site.thumbnail.name, kind))
            ]
            if len(existing) == len(THUMBNAIL_DERIVATIVES) and not force:
                skipped += 1
                continue

            if dry_run:
                self.stdout.write(f"  would write: {site.name}")
                written += 1
                continue

            produced = write_thumbnail_derivatives(site)
            if produced:
                written += 1
            else:
                # `write_thumbnail_derivatives` logs the reason; a thumbnail
                # Pillow cannot open is a data problem, not a reason to stop.
                failed += 1
                self.stderr.write(f"  could not derive: {site.name}")

        verb = "would write" if dry_run else "wrote"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {written}; skipped {skipped} already done; "
                f"{missing} without a thumbnail; {failed} failed"
            )
        )
