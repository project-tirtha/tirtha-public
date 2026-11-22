"""
Write a `.compressed.ply` beside every published splat.

PlayCanvas's chunked, quantised PLY is the one compressed format the shipped
viewer already reads: `PlyParserUtils.determineHeaderFormatFromHeaderText` looks
for `element chunk` in the header and dispatches to
`PlayCanvasCompressedPlyParser`, which is in the 0.4.7 bundle. So this needs no
viewer change -- unlike `.sog`, which nothing in the shipped viewer can read.

Measured on six published models between 1 MB and 288 MB: a uniform **50%** of
the `.splat`, with no size-dependence. Across the 60 published splats that is
about 2,750 MB against 1,375 MB.

`run_postprocess` does this for every model published from now on. This is for
the ones already on disk.

    python manage.py backfill_compressed_ply [--dry-run] [--limit N] [--force]

Converts from the `.sog` where there is one -- it is the smaller input and the
faster read -- and from the `.splat` otherwise. Skips anything already current,
so it is cheap to re-run and safe to interrupt.
"""

import subprocess
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Write a .compressed.ply beside every published .splat."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        from tirtha_recon.splat_transform import SplatTransform

        root = Path(settings.STATIC_ROOT) / "models"
        if not root.is_dir():
            self.stderr.write(f"No models directory at {root}.")
            return

        st = SplatTransform(
            executable=str(settings.SPLAT_TRANSFORM_PATH),
            gpu=settings.SPLAT_TRANSFORM_GPU,
        )

        done = failed = skipped = 0
        splat_bytes = ply_bytes = 0

        for splat in sorted(root.rglob("*.splat")):
            target = splat.with_suffix(".compressed.ply")
            if not options["force"] and target.exists():
                if target.stat().st_mtime >= splat.stat().st_mtime:
                    skipped += 1
                    splat_bytes += splat.stat().st_size
                    ply_bytes += target.stat().st_size
                    continue

            # The `.sog` is the smaller input and the faster read; either is a
            # supported input and both describe the same reconstruction.
            sog = splat.with_suffix(".sog")
            source = sog if sog.is_file() else splat

            if options["dry_run"]:
                done += 1
                self.stdout.write(f"  would convert {splat.name}")
                if options["limit"] and done >= options["limit"]:
                    break
                continue

            # Written to a staging name and renamed, so an interrupted run never
            # leaves a truncated PLY that `api._artifact_url` would then offer a
            # viewer as though it were a whole model.
            # `<stem>.partial.compressed.ply`, not `<...>.compressed.ply.partial`.
            #
            # splat-transform picks its writer from the output extension, and
            # `.partial` is not one it knows -- so staging that way failed every
            # conversion with exit 1 before writing a byte. The suffix has to
            # stay intact and the marker goes in front of it.
            staging = target.with_name(
                target.name.replace(".compressed.ply", ".partial.compressed.ply")
            )
            started = time.monotonic()
            try:
                subprocess.run(
                    st.to_compressed_ply(source, staging),
                    check=True,
                    capture_output=True,
                    timeout=3600,
                )
                staging.replace(target)
            except (subprocess.SubprocessError, OSError) as exc:
                failed += 1
                staging.unlink(missing_ok=True)
                detail = getattr(exc, "stderr", b"") or b""
                self.stderr.write(
                    f"  {splat.name}: {exc}\n"
                    f"      {detail.decode('utf-8', 'replace').strip()[-400:]}"
                )
                # Counted against the limit like a success: a run that fails
                # sixty times should stop at the limit, not walk the archive.
                if options["limit"] and done + failed >= options["limit"]:
                    break
                continue

            raw = splat.stat().st_size
            small = target.stat().st_size
            splat_bytes += raw
            ply_bytes += small
            done += 1
            self.stdout.write(
                f"  {splat.name}: {raw // 1048576} MB -> {small // 1048576} MB "
                f"({small * 100 // max(raw, 1)}%) in {time.monotonic() - started:.0f}s"
            )
            if options["limit"] and done >= options["limit"]:
                break

        verb = "would convert" if options["dry_run"] else "converted"
        saved = splat_bytes - ply_bytes
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {verb} {done}, skipped {skipped}, failed {failed}. "
                f"{splat_bytes // 1048576} MB of splats against "
                f"{ply_bytes // 1048576} MB of compressed PLY "
                f"({saved // 1048576} MB less to send per full pass)."
            )
        )
