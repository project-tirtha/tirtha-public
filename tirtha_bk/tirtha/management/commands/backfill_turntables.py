"""
Render a turntable preview for sites that already have a published model.

Nothing produces these automatically yet: wiring a stage into `_run_order` is a
change to the reconstruction pipeline, and the maintainer asked to hear the
pipeline review before any of it lands. This command is the whole feature for
now. Idempotent, skips what is already there, and never lets one bad model stop
the rest -- the same shape as `backfill_thumbnails`.

**This writes into MEDIA_ROOT, which on both the demo and beta is production's
media tree.** Nothing here runs on import and nothing runs without being asked;
`--dry-run` reports exactly what would be written, and the maintainer decides.
That is not boilerplate caution: this file exists because the last backfill was
run without asking, which is recorded in internal design notes.
"""

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from tirtha.models import Site
from tirtha.turntable import (
    FRAME_COUNT,
    RENDERABLE_SUFFIXES,
    TurntableUnavailable,
    camera_for_run,
    find_turntable,
    probe_camera,
    render_turntable,
    software_adapter,
    turntable_path,
)


def published_artifact(run) -> Path | None:
    """
    The file the viewer serves for this run, if it is on disk.

    Named `<siteID>_<runID>.<ext>` under the site's `published/` directory.
    `.sog` is what current runs publish; `.splat` is what older ones did, and
    both render.
    """
    directory = Path(settings.STATIC_ROOT) / "models" / str(run.site.ID) / "published"
    if not directory.is_dir():
        return None
    for suffix in RENDERABLE_SUFFIXES:
        candidate = directory / f"{run.site.ID}_{run.ID}{suffix}"
        if candidate.is_file():
            return candidate
    return None


class Command(BaseCommand):
    help = "Render turntable previews for sites with a published model."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be rendered, write nothing.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-render sites that already have a preview.",
        )
        parser.add_argument("--frames", type=int, default=FRAME_COUNT)
        parser.add_argument("--site", help="Render one site by ID.")
        parser.add_argument("--limit", type=int, help="Stop after this many sites.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        try:
            adapter = software_adapter()
        except TurntableUnavailable as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return
        self.stdout.write(f"Software adapter: {adapter}")

        sites = Site.objects.exclude(thumbnail="").order_by("ID")
        if options["site"]:
            sites = sites.filter(ID=options["site"])

        written = rendered_bytes = skipped = failed = 0
        started = time.monotonic()

        for site in sites:
            if options["limit"] and written >= options["limit"]:
                break

            destination_name = turntable_path(site.thumbnail.name)
            # `find_turntable`, not the `.mp4` name: a curator may have uploaded
            # a GIF or a WebM by hand, and this checked only for the file the
            # renderer writes. So the backfill re-rendered over a hand-made
            # preview and `find_turntable` -- which tries `.mp4` first -- then
            # served the render instead. That is precisely the case the upload
            # feature exists for.
            if not options["force"] and find_turntable(
                site.thumbnail.name, default_storage
            ):
                skipped += 1
                continue

            # Newest first: the preview should show the current model, and a
            # site's later runs are the ones that improved it.
            run, source = None, None
            for candidate in site.runs.order_by("-started_at"):
                found = published_artifact(candidate)
                if found:
                    run, source = candidate, found
                    break
            if source is None:
                skipped += 1
                continue

            camera = camera_for_run(run)
            origin = "the run's own camera"
            if camera is None:
                # 49 of 81 runs hold migration 0021's `[0, 0, 5]` placeholder,
                # which frames the inside of the temple.
                # No second chance when a *stored* camera fails the gate.
                # Probing after one does technically pass -- the border is
                # empty, the coverage is in band -- and both sites it rescued
                # came out as white haze rather than a monument. A stored
                # camera that frames nothing is evidence about the scene, not
                # about the camera, and the still image is the better answer.
                #
                # Not `camera_from_stats` alone: nothing in the data says
                # which way is up, and guessing wrong publishes a confident
                # video of nothing. `probe_camera` renders one frame per
                # candidate axis and keeps the one that actually shows the
                # monument, or refuses.
                probe_dir = Path(tempfile.mkdtemp(prefix=f"probe-{site.ID}-"))
                try:
                    camera = probe_camera(source, probe_dir, adapter)
                    origin = "framed by probing"
                except (TurntableUnavailable, OSError, KeyError) as exc:
                    self.stderr.write(f"  {site.ID}: no camera ({exc})")
                    failed += 1
                    continue
                finally:
                    shutil.rmtree(probe_dir, ignore_errors=True)

            if dry_run:
                self.stdout.write(
                    f"  would render {site.ID} ({site.verbose_id}) "
                    f"from {source.name}, {origin}"
                )
                written += 1
                continue

            workdir = Path(tempfile.mkdtemp(prefix=f"turntable-{site.ID}-"))
            try:
                destination = Path(settings.MEDIA_ROOT) / destination_name
                destination.parent.mkdir(parents=True, exist_ok=True)
                size = render_turntable(
                    source,
                    destination,
                    camera,
                    workdir,
                    frames=options["frames"],
                    adapter=adapter,
                )
                written += 1
                rendered_bytes += size
                self.stdout.write(f"  {site.ID}: {size:,} bytes, {origin}")
            except (TurntableUnavailable, OSError, subprocess.SubprocessError) as exc:
                # A model this cannot frame keeps its still image. One bad
                # artifact must not stop the other fifty-eight -- and
                # `CalledProcessError`/`TimeoutExpired` are `SubprocessError`,
                # not `OSError`, so without that third class a single failed
                # splat-transform unwound out of `handle()` and abandoned every
                # site after it. `turntable.render_turntable` already gets this
                # right; the command did not.
                failed += 1
                self.stderr.write(f"  {site.ID}: {exc}")
            finally:
                shutil.rmtree(workdir, ignore_errors=True)

        elapsed = time.monotonic() - started
        verb = "would write" if dry_run else "wrote"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {written} previews ({rendered_bytes:,} bytes), "
                f"skipped {skipped}, failed {failed}, in {elapsed:.0f}s"
            )
        )
