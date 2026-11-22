"""Runs: `run_detail`, `serialize_run`, and which model file to offer (`_artifact`)."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.db.models import Count
from django.views.decorators.http import require_GET

from tirtha.models import ARK, Run

from .common import _conditional, _not_found, _visible_runs


def _sibling(url: str, want: str, have: str) -> str | None:
    """
    A `want`-format file sitting beside a `have`-format one, and its size.

    Returns `(url, bytes)` or `None`. The size comes back with the URL because
    the caller chooses between candidates by size and would otherwise re-stat
    what this has already stat-ed. It is deliberately *not* cached: a module
    level cache never expires, and `run_gaussian_cut(force=True)` republishes
    under the same filename, so a cached size outlives the file it measured.

    Unit 5.4 changed the published GS format to SOG and 6.4 replaced the viewer
    with one that reads `.glb` and `.sog`. Neither unit converted what was
    already on disk: at the 2026-08-29 snapshot, 56 of 101 ARKs pointed at
    `.splat` and no `.sog` existed anywhere, so most of the archive rendered an
    empty canvas. `splat-transform` converts them in place -- `.splat` is a
    supported input, so the original PLYs are not needed.

    **The ARK's own URL is never rewritten in the database.** An ARK is a
    persistence commitment recorded on the row; it keeps resolving to exactly
    the file it always named. This only changes what the *API* offers a viewer,
    which is what this function has always been for.

    The existence check runs against `ARTIFACT_ROOT` -- the directory that
    actually backs the `/static/` half of an ARK URL on this deployment. It is
    not `STATIC_ROOT` by default and must not be assumed to be: ARK URLs carry
    the prefix of whichever deployment minted them, so a beta or demo instance
    can hold URLs whose files live under a different root entirely. Left unset,
    nothing is rewritten and the ARK is served verbatim.
    """
    if not url.lower().endswith(have):
        return None
    root = getattr(settings, "ARTIFACT_ROOT", "")
    if not root:
        return None
    marker = "/static/"
    if marker not in url:
        return None
    relative = url.split(marker, 1)[1]
    base = Path(root).resolve()
    try:
        stem = (
            relative[: -len(have)]
            if relative.lower().endswith(have)
            else relative.rsplit(".", 1)[0]
        )
        candidate = (base / (stem + want)).resolve()
        # The URL comes out of the database, so it is input, not fact. Without
        # this a `..` in a stored ARK would reach outside the artifact tree --
        # it happens to find nothing today, which is luck rather than a rule.
        if not candidate.is_relative_to(base) or not candidate.is_file():
            return None
        # Inside the `try`: the file can be replaced between `is_file` and
        # `stat`, and `run_gaussian_cut(force=True)` republishes under the same
        # names, so this is a real window rather than a theoretical one.
        size = candidate.stat().st_size
    except (OSError, ValueError):
        return None
    stem = url[: -len(have)] if url.lower().endswith(have) else url.rsplit(".", 1)[0]
    return stem + want, size


# The formats the deployed viewer can draw, in preference order.
#
# One fact, in one place. It was previously split between `_artifact_url`'s
# hardcoded `.splat` -> `.sog` direction and the demo's rewrite list, which is
# how the two came to disagree -- and a single "which format" string could not
# express the real situation either, because the archive holds two: 56 legacy
# `.splat` files, and `.sog` + `.spz` for everything reconstructed since 5.4.
#
# The default is what `@mkkellogg/gaussian-splats-3d` reads (internal design notes
# 15.1.0.1) plus the photogrammetry path's `.glb`. A deployment that moves to a
# SOG viewer sets `(".sog", ".glb")` and needs no re-encode of anything.
# `.spz` is deliberately absent. The viewer has an SPZ loader, but it accepts
# only versions 1-2 while splat-transform writes 4 and refuses below 3 -- so
# every `.spz` this pipeline could produce is one the viewer rejects, and
# offering it turns "no model" into "failed to load".
VIEWER_FORMATS = (".splat", ".ply", ".glb")


def _viewer_formats() -> tuple[str, ...]:
    configured = getattr(settings, "VIEWER_ARTIFACT_FORMATS", None)
    if configured:
        return tuple(configured)

    # The older single-value setting, kept working.
    legacy = getattr(settings, "VIEWER_ARTIFACT_FORMAT", "")
    if legacy:
        return (f".{legacy.lstrip('.')}", ".glb")
    return VIEWER_FORMATS


# The Gaussian-splat encodings. Every one of these describes the same kind of
# reconstruction, so swapping between them changes only the file size; a `.glb`
# is a mesh from the AliceVision path and is not interchangeable with any of
# them, even when both sit in one directory.
_SPLAT_FORMATS = (".compressed.ply", ".splat", ".ply", ".sog", ".spz")


def _artifact_url(url: str) -> str:
    """The URL half of `_artifact`, for callers that do not need the size."""
    return _artifact(url)[0]


def _artifact(url: str) -> tuple[str, int | None]:
    """
    Offer whichever format the deployed viewer can actually read, and its size.

    The size is the `st_size` of the very file the URL names, measured by the
    `_sibling` stat that picked it -- so `model_bytes` costs no extra syscall
    and cannot describe a different file from `file_url`. `None` when it could
    not be measured (no `ARTIFACT_ROOT`, a URL outside the tree, a rewrite).

    The archive holds more than one. Legacy runs published `.splat`; 5.4 made
    SOG the published format and 6.4 targeted a viewer that reads it; the React
    UI now shipping reads `.splat`, `.spz` and `.ply` but not `.sog`, so
    `run_postprocess` publishes an `.spz` beside every `.sog` (internal design notes
    15.1.0.1). Which of those a given deployment should be handed is a fact
    about its viewer, so that is what is configured -- not a rewrite direction,
    which cannot express "reads two of the four".

    An ARK already naming a readable format is served untouched; otherwise the
    first readable sibling on disk wins. If there is none the ARK is served
    verbatim: better a file the viewer cannot draw, with the download link
    beside it, than a URL that 404s.

    **The ARK's own URL is never rewritten in the database.** An ARK is a
    persistence commitment recorded on the row; it keeps resolving to exactly
    the file it always named. This only changes what the *API* offers a viewer.

    The explicit per-URL list stays first, so a deployment holding copies
    somewhere unrelated can override the general rule.
    """
    for old, new in getattr(settings, "DEMO_ARTIFACT_REWRITES", []):
        if url == old:
            return new, None

    formats = _viewer_formats()
    lowered = url.lower()
    # Longest match first: `.compressed.ply` must not be read as `.ply`, which
    # would make every sibling path be built from the wrong stem.
    have = next(
        (
            f
            for f in sorted(_SPLAT_FORMATS + (".glb",), key=len, reverse=True)
            if lowered.endswith(f)
        ),
        "." + lowered.rsplit(".", 1)[-1] if "." in lowered else "",
    )
    if not have:
        return url, None

    # In preference order, within the same family.
    #
    # This returned the URL untouched the moment it ended in *any* readable
    # format, so `VIEWER_ARTIFACT_FORMATS` ordered the fallbacks and nothing
    # else: an ARK naming a 288 MB `.splat` was served that `.splat` even with a
    # 146 MB `.compressed.ply` of the same run sitting beside it. The list is
    # what the viewer can read, best first, so the best one that *exists* should
    # be what it is handed.
    #
    # **Family, because a mesh is not a splat.** `.glb` comes from the
    # AliceVision path and `.splat`/`.compressed.ply`/`.sog` from Gaussian
    # splatting; a site can have both, published by different runs, sitting in
    # the same directory. Preferring by size alone would answer a `.glb` ARK
    # with a `.splat` -- a different reconstruction of the same site, offered as
    # though it were the one the identifier names. Only the encoding may
    # improve, never the thing encoded.
    #
    # **Smallest wins, not first in the list.** The ordering above was a proxy
    # for size, and on 2026-09-12 it inverted: `.compressed.ply` is documented
    # as "half the size of `.splat`", measured across six models from 1 MB to
    # 288 MB -- but at 1.5M Gaussians with full SH bands it came out at 91.9 MB
    # against the `.splat`'s 48.0 MB, and being first in the list meant the
    # viewer was handed the larger file. A fixed order encodes a size
    # relationship that holds only for the models it was measured on; measuring
    # cannot invert.
    #
    # The list still decides what is *eligible* -- it is what the viewer can
    # read -- and the family rule still decides what is comparable. Size only
    # breaks the tie among formats that pass both.
    family = _SPLAT_FORMATS if have in _SPLAT_FORMATS else None
    candidates: list[tuple[int, int, str]] = []
    # The old fixed-order answer, kept as the fallback for when nothing can be
    # measured -- no `ARTIFACT_ROOT`, or an ARK pointing outside the tree.
    # Falling back to *this* rather than to `url` is what keeps a legacy `.splat`
    # ARK whose own file is missing resolving to the sibling that does exist.
    first_eligible = None
    for rank, want in enumerate(formats):
        if family is not None and want not in family:
            continue
        if family is None and want != have:
            continue
        # `want == have`, not `lowered.endswith(want)`.
        #
        # `.compressed.ply` ends with `.ply`, so the `.ply` iteration claimed a
        # `.compressed.ply` URL as "its own format", took `offered = url`, then
        # failed to measure it -- `_sibling` builds `<stem>.ply`, a different
        # file that does not exist -- and returned the URL unchanged, throwing
        # away the smaller candidates already collected. Measured on
        # Te5iC7TLNVpnkntm: a 432,327 B `.compressed.ply` served while a
        # 224,800 B `.splat` sat beside it, which is precisely the inversion
        # this function was rewritten to end.
        if want == have:
            resolved = _sibling(url, want, have)
            offered, size = (url, resolved[1]) if resolved else (url, None)
        else:
            resolved = _sibling(url, want, have)
            offered, size = resolved if resolved else (None, None)
        if not offered:
            continue
        if first_eligible is None:
            first_eligible = offered
        if size is None:
            # Unmeasurable. Not a candidate -- but `first_eligible` remembers
            # it, so an unmeasurable format is still offered when no measurable
            # one exists. Previously this returned immediately and discarded
            # every smaller candidate already found.
            continue
        # `rank` breaks exact ties, so the configured order still expresses a
        # preference where the sizes say nothing.
        candidates.append((size, rank, offered))
    if candidates:
        size, _, offered = min(candidates)
        return offered, size
    return first_eligible or url, None


# Longest first, so `.compressed.ply` is not reported as `ply`.
_MODEL_FORMATS = sorted(_SPLAT_FORMATS + (".glb", ".gltf"), key=len, reverse=True)


def _model_format(url: str | None) -> str | None:
    """
    Which loader the client needs: `splat`, `compressed.ply`, `ply`, `sog`,
    `spz`, `glb` or `gltf` -- the extension of the file actually offered, without the
    dot. `None` for no model or an extension the API does not know.
    """
    if not url:
        return None
    lowered = url.lower().split("?", 1)[0]
    for fmt in _MODEL_FORMATS:
        if lowered.endswith(fmt):
            return fmt[1:]
    return None


# Below this, the two timestamps did not measure a reconstruction.
#
# `manage.py create_manual_run` registers a model that was built elsewhere: it
# creates the Run and sets `ended_at` a few lines later, so the pair is
# milliseconds apart. 15 of the 82 runs carrying both timestamps are under a
# minute and every one of them is `status="Manual"`; the fastest is 0.05 s.
#
# The site panel showed those as "1 minute" -- the client floored anything
# sub-minute with `Math.max(total, 1)` -- so 11 of the 31 sites that display the
# card told every visitor their model took a minute to build. A real
# reconstruction on this hardware is minutes to hours; the median is 22 minutes.
#
# Filtered here rather than by excluding `status="Manual"` from the candidate.
# That was the obvious fix and it is worse: `Mw82t2fxdN8VdteA` publishes
# [GS/Manual 0.4 s, aV/Archived 2924.8 s], so dropping Manual makes the card
# report 49 minutes for a photogrammetry run while the viewer is showing the
# Manual Gaussian splat. A confident wrong number is worse than none.
_MIN_RECONSTRUCTION_SECONDS = 60


def _reconstruction_seconds(run) -> float | None:
    """How long a run took, or None when the figure would not mean that."""
    if not (run and run.started_at and run.ended_at):
        return None
    seconds = (run.ended_at - run.started_at).total_seconds()
    return seconds if seconds >= _MIN_RECONSTRUCTION_SECONDS else None


def serialize_run(run: Run, request) -> dict:
    ark: ARK | None = getattr(run, "ark", None)
    file_url, model_bytes = _artifact(ark.url) if ark else (None, None)
    return {
        "id": run.ID,
        "kind": run.kind,
        "status": run.status,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        # How long it took, computed here rather than in the client.
        #
        # Both timestamps were already exposed and nothing ever subtracted them,
        # so "how long did this take" was answerable only by someone willing to
        # parse two ISO strings and handle the null. All 82 runs carry both;
        # the median is 22 minutes and the longest is 30.4 hours, which is a
        # range worth telling a contributor about.
        "duration_seconds": _reconstruction_seconds(run),
        "ark": ark.ark if ark else None,
        # ARK.url points at the published model file itself -- that is what the
        # old viewer templates loaded, and what the React viewer needs. The
        # extension tells the client which loader to use: .glb for
        # photogrammetry, .sog for Gaussian splats since 5.4.
        "file_url": file_url,
        # The size of `file_url`'s file in bytes, so a phone can ask "load
        # 288 MB?" before fetching it (review 2026-11-22 6). `None` when it
        # could not be measured -- never a guess.
        "model_bytes": model_bytes,
        # The loader `file_url` needs, so the client does not parse extensions.
        "model_format": _model_format(file_url),
        # From the annotation when the caller provided one; only a lone
        # `run_detail` pays for its own count.
        "image_count": getattr(run, "_image_count", None)
        if getattr(run, "_image_count", None) is not None
        else run.images.count(),
        # One camera model for both formats (§20.2). The viewer-specific
        # legacy fields are not exposed: a client that had to choose between
        # `rotaX` and `initCamPos*` depending on run kind is the duplication
        # this replaces.
        "camera": {
            "position": run.cam_position or None,
            "target": run.cam_target or None,
            "up": run.cam_up or [0.0, 1.0, 0.0],
            "rotation": run.model_rotation or [0.0, 0.0, 0.0],
            "fov": run.cam_fov,
            "antialiased": run.antialiased,
        },
        "contributors": sorted({c.public_name for c in run.contributors.all()}),
    }


@require_GET
def run_detail(request, run_id: str):
    run = (
        _visible_runs()
        .select_related("site", "ark")
        .prefetch_related("contributors")
        .annotate(_image_count=Count("images"))
        .filter(ID=run_id)
        .first()
    )
    if run is None:
        return _not_found("run", run_id)
    data = serialize_run(run, request)
    data["site"] = {
        "id": run.site.ID,
        "name": run.site.name,
        "slug": run.site.verbose_id,
    }
    return _conditional(request, data)
