"""
Populate the EXIF columns from the files already on disk (internal design notes).

2.6 added `date_taken`, `camera_make` and the rest; nothing ever filled them for
the existing archive, so every legacy row reads null. §2.9's ordering rule --
"order legacy rows by EXIF DateTime, falling back to (created_at, ID)" -- was
therefore taking the fallback for all 19,321 images.

A management command rather than a migration: this reads ~19,000 files, and a
migration that does file I/O turns cutover into an unbounded wait and cannot be
resumed if it stops. Re-runnable and interruptible instead.

    python manage.py backfill_exif [--site <id>] [--limit N] [--dry-run]

Header reads only, no decode. Run `manage.py recompute_image_seq` afterwards to
reorder `seq` by the recovered capture times.

**Not** by re-running migrations. This once advised `migrate tirtha 0019 &&
migrate tirtha 0020`, which was safe when 0020 was the tip of the chain and
became destructive as it grew: migrating back to 0019 now reverses ten
migrations, dropping the masks, site-request and leaderboard tables and the
geo seeding along with them.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from tirtha.exif import read_exif


class Command(BaseCommand):
    help = "Populate EXIF columns on existing Image rows from the files on disk."

    def add_arguments(self, parser):
        parser.add_argument("--site", help="Limit to one Site ID.")
        parser.add_argument("--limit", type=int, help="Stop after N images.")
        parser.add_argument(
            "--all", action="store_true", help="Re-read rows already populated."
        )
        parser.add_argument(
            "--include-gps",
            action="store_true",
            help=(
                "Also record GPS coordinates. Off by default: contributors were "
                "never asked to share location (internal design notes)."
            ),
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        from tirtha.models import Image

        media = Path(settings.MEDIA_ROOT)
        queryset = Image.objects.all()
        if options["site"]:
            queryset = queryset.filter(contribution__site__ID=options["site"])
        if not options["all"]:
            queryset = queryset.filter(date_taken__isnull=True)
        if options["limit"]:
            queryset = queryset[: options["limit"]]

        total = queryset.count() if not options["limit"] else len(queryset)
        self.stdout.write(f"Reading EXIF from {total} image(s)...")

        if not options["include_gps"]:
            self.stdout.write(
                "GPS is not being recorded (pass --include-gps to change that); "
                "see internal design notes."
            )
        updated, missing, empty = [], 0, 0
        for index, img in enumerate(queryset.iterator(chunk_size=500), start=1):
            path = media / img.image.name
            if not path.exists():
                missing += 1
                continue
            data = read_exif(path, include_gps=options["include_gps"])
            if not data:
                empty += 1
                continue
            for field, value in data.items():
                setattr(img, field, value)
            updated.append(img)

            if len(updated) >= 500 and not options["dry_run"]:
                self._flush(Image, updated)
                self.stdout.write(f"  {index}/{total}...")

        if updated and not options["dry_run"]:
            self._flush(Image, updated)

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {total - missing - empty} with EXIF, {empty} without, "
                f"{missing} file(s) missing."
            )
        )
        if not options["dry_run"]:
            self.stdout.write(
                "Run `manage.py recompute_image_seq` to reorder `seq` by the "
                "recovered capture times."
            )

    @staticmethod
    def _flush(model, rows):
        model.objects.bulk_update(
            rows,
            [
                "camera_make",
                "camera_model",
                "date_taken",
                "focal_length",
                "gps_latitude",
                "gps_longitude",
            ],
            batch_size=500,
        )
        rows.clear()
