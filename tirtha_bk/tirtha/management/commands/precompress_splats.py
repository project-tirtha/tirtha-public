"""
Write `<file>.splat.gz` beside every published `.splat`, for nginx `gzip_static`.

A `.splat` is raw quantised Gaussians and compresses to about 60% -- measured at
288 MB to 171 MB on the largest one in this archive. It is deliberately absent
from nginx's `gzip_types`, because compressing a file that size on every request
would cost far more CPU than the bandwidth is worth. Compressed once, on disk,
it costs none: `gzip_static` serves the `.gz` to a browser that accepts gzip and
the original to one that does not.

`run_cleanup` does this for every model published from now on. This is for the
ones already on disk.

    python manage.py precompress_splats [--dry-run] [--force]

Skips a `.gz` that is already newer than its source, so it is cheap to re-run.
"""

import gzip
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

# A `.gz` is kept only if it is smaller than this fraction of its source.
# At 0.9 exactly one of the 60 published splats qualifies, which is the
# honest answer: the format does not compress.
KEEP_BELOW = 0.9


class Command(BaseCommand):
    help = "Pre-compress published .splat files for nginx gzip_static."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Rewrite even if the .gz looks current.",
        )

    def handle(self, *args, **options):
        root = Path(settings.STATIC_ROOT) / "models"
        if not root.is_dir():
            self.stderr.write(f"No models directory at {root}.")
            return

        raw_total = gz_total = 0
        written = skipped = 0

        for source in sorted(root.rglob("*.splat")):
            target = source.with_name(source.name + ".gz")
            if (
                not options["force"]
                and target.exists()
                and target.stat().st_mtime >= source.stat().st_mtime
            ):
                skipped += 1
                raw_total += source.stat().st_size
                gz_total += target.stat().st_size
                continue

            raw = source.stat().st_size
            raw_total += raw
            if options["dry_run"]:
                written += 1
                self.stdout.write(
                    f"  would compress {source.name} ({raw // 1048576} MB)"
                )
                continue

            # Written beside the source and renamed into place, so a run
            # interrupted here never leaves a truncated `.gz` for nginx to
            # serve as though it were the whole model.
            staging = target.with_suffix(".gz.partial")
            try:
                with (
                    source.open("rb") as fh,
                    gzip.open(staging, "wb", compresslevel=6) as out,
                ):
                    shutil.copyfileobj(fh, out, length=1024 * 1024)
                staging.replace(target)
            except OSError as exc:
                self.stderr.write(f"  {source.name}: {exc}")
                staging.unlink(missing_ok=True)
                continue

            # Kept only if it earns its place.
            #
            # Measured across all 60 published splats: 59 of them compress to
            # 93-95%, because a `.splat` is already quantised binary and there
            # is nothing left for gzip to find. One compresses to 60%. Holding
            # 2.5 GB of `.gz` to save 252 MB of transfer -- 117 MB of it from
            # that single file -- is a worse trade than not holding it, and a
            # `.gz` that is 94% of its source costs a second file on disk, a
            # second thing to keep in step, and saves a rounding error.
            #
            # So: write it, measure it, and delete it unless it beats the
            # threshold. The one file that does keeps its 117 MB saving.
            compressed = target.stat().st_size
            if compressed > raw * KEEP_BELOW:
                target.unlink(missing_ok=True)
                skipped += 1
                gz_total += raw
                self.stdout.write(
                    f"  {source.name}: {compressed * 100 // raw}% of raw, not kept"
                )
                continue

            gz_total += compressed
            written += 1
            self.stdout.write(
                f"  {source.name}: {raw // 1048576} MB -> {compressed // 1048576} MB "
                f"({compressed * 100 // raw}%)"
            )

        verb = "would write" if options["dry_run"] else "wrote"
        saved = raw_total - gz_total
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {verb} {written}, skipped {skipped}. "
                f"{raw_total // 1048576} MB -> {gz_total // 1048576} MB "
                f"({saved // 1048576} MB less to send per full pass)."
            )
        )
