"""
Recompute `Image.seq` from the capture times, safely.

`seq` is a per-site monotonic counter that gives COLMAP's `sequential_matcher`
something meaningful to match consecutive images on: sorted symlink order equals
`seq` order equals capture order, as closely as the metadata allows.

Migration 0020 established it, ordering by `date_taken` and falling back to
upload order. But `date_taken` was null for the entire archive when it ran --
nothing had read the EXIF yet -- so every image took the fallback. Once
`backfill_exif` fills those columns the ordering can finally be what 0020
intended, and this recomputes it.

**Why a command and not `migrate tirtha 0019 && migrate tirtha 0020`**, which is
what `backfill_exif` used to advise: 0020 is not the latest migration. Migrating
back to 0019 reverses everything after it -- masks, site requests, the
leaderboard, the geo seeding -- dropping those tables and their data. The advice
was written when 0020 was the tip and became destructive as the chain grew.

The ordering rule is 0020's, unchanged. **Within a contribution, never globally
by timestamp**: EXIF clocks are not comparable across contributors -- drift,
timezones, cameras whose clock was never set -- so contributions are blocked in
arrival order and images ordered by capture time inside each block.

    python manage.py recompute_image_seq [--site <id>] [--dry-run]
"""

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Recompute Image.seq from date_taken, per site, without touching migrations."

    def add_arguments(self, parser):
        parser.add_argument("--site", help="Limit to one Site ID.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        from tirtha.models import Contribution, Image, Site

        sites = Site.objects.all()
        if options["site"]:
            sites = sites.filter(ID=options["site"])

        dry = options["dry_run"]
        total_changed = 0
        total_seen = 0

        for site in sites.iterator():
            seq = 0
            updates = []
            contributions = Contribution.objects.filter(site=site).order_by(
                "contributed_at", "ID"
            )
            for contribution in contributions:
                images = Image.objects.filter(contribution=contribution).order_by(
                    "date_taken", "created_at", "ID"
                )
                for img in images:
                    total_seen += 1
                    if img.seq != seq:
                        img.seq = seq
                        updates.append(img)
                    seq += 1
            if updates:
                total_changed += len(updates)
                self.stdout.write(
                    f"  {site.name}: {len(updates)} of {seq} image(s) reordered."
                )
                if not dry:
                    with transaction.atomic():
                        Image.objects.bulk_update(updates, ["seq"], batch_size=500)

        verb = "would change" if dry else "changed"
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {verb} {total_changed} of {total_seen} image(s)."
            )
        )
