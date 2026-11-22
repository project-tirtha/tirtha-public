"""
Materialise a site's images into a COLMAP working directory.

The bridge between the database and the Django-free reconstruction layer: this
command knows about models, `tirtha_recon.dataset` knows about paths, and
neither needs to know the other's business (internal design notes).

Used by the reconstruction pipeline and by the Lab, which consumes the
directories this produces without needing a database of its own.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from tirtha_recon.dataset import ImageRef, colmap_dir, materialise

from tirtha.models import Image, Site


class Command(BaseCommand):
    help = "Materialise a site's images into a COLMAP working directory."

    def add_arguments(self, parser):
        parser.add_argument("--site", required=True, help="Site/Site ID or verbose_id.")
        parser.add_argument(
            "--out", required=True, help="Working directory to build or refresh."
        )
        parser.add_argument(
            "--contribution",
            action="append",
            default=None,
            help="Restrict to these contribution IDs. Repeatable.",
        )
        parser.add_argument(
            "--include-rejected",
            action="store_true",
            help="Include images that were rejected or flagged. Off by default.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be linked without touching the filesystem.",
        )

    def handle(self, *args, **opts):
        site = self._resolve_site(opts["site"])
        images = self._ordered_images(
            site, opts["contribution"], opts["include_rejected"]
        )
        if not images:
            raise CommandError(f"No images found for site {site.ID}.")

        media_root = Path(settings.MEDIA_ROOT)
        refs = [
            ImageRef(seq=seq, image_id=str(img.ID), source=media_root / img.image.name)
            for seq, img in enumerate(images, start=1)
        ]

        if opts["dry_run"]:
            self.stdout.write(f"{site.verbose_id}: {len(refs)} images")
            for ref in refs[:5]:
                self.stdout.write(f"  {ref.link_name}  <- {ref.source}")
            if len(refs) > 5:
                self.stdout.write(f"  ... and {len(refs) - 5} more")
            return

        out = Path(opts["out"])
        # strict=False: a missing source is worth reporting in full rather than
        # aborting on the first one, so an operator sees the whole picture.
        result = materialise(refs, out, strict=False)
        colmap_dir(out)

        self.stdout.write(
            self.style.SUCCESS(
                f"{site.verbose_id}: {result.linked} linked, "
                f"{result.already_present} already present -> {result.images_dir}"
            )
        )
        if result.missing_sources:
            self.stderr.write(
                self.style.WARNING(
                    f"{len(result.missing_sources)} source file(s) missing from "
                    f"{media_root}; the first is {result.missing_sources[0]}"
                )
            )

    def _resolve_site(self, ident):
        try:
            return Site.objects.get(ID=ident)
        except Site.DoesNotExist:
            pass
        try:
            return Site.objects.get(verbose_id=ident)
        except Site.DoesNotExist as exc:
            raise CommandError(f"No site with ID or verbose_id {ident!r}.") from exc

    @staticmethod
    def _ordered_images(site, contributions, include_rejected):
        """
        Order images so that sorted link names equal capture order.

        Each contribution gets a contiguous block in arrival order, ordered
        internally by capture time -- never globally by timestamp, because EXIF
        clocks are not comparable across contributors (internal design notes).

        Phase 2.7 adds a stored `seq` column and EXIF `date_taken`; until then
        this derives the same ordering from `created_at`, which is upload order
        within a contribution. Ties are broken on ID so the result is stable.
        """
        qs = Image.objects.filter(contribution__site=site)
        if contributions:
            qs = qs.filter(contribution__ID__in=contributions)
        if not include_rejected:
            # Evidence, not a stored verdict (internal design notes): drop anything a
            # moderator rejected or flagged, and anything an operator rejected.
            qs = qs.exclude(safety_status__in=("rejected", "flagged")).exclude(
                admin_verdict="reject"
            )
        return list(
            qs.select_related("contribution").order_by(
                "contribution__contributed_at", "created_at", "ID"
            )
        )
