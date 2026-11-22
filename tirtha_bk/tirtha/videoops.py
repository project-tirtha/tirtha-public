"""
Video → frames (internal design notes).

Video suits COLMAP well: every frame shares one physical camera, so
`--ImageReader.single_camera 1` reduces calibration from "N unknown cameras" to
one — better conditioned than crowdsourced stills from dozens of phones. It also
settles the "are these sequential?" question the upload modal would otherwise
have to ask, because video is inherently sequential.

Three things this must get right, from §16.2:

- **HDR / Dolby Vision** is the default on recent iPhones. A naive frame dump
  gives washed-out, mis-toned frames — bad for reconstruction and worse for GS
  colour consistency. Detected from stream metadata and tonemapped explicitly.
- **Sharpness selection, not uniform sampling.** Video is full of motion blur;
  the sharpest frame per block is worth far more than an evenly spaced one.
- **Provenance.** Every frame records `source_video` and `frame_index`, because
  a ten-minute clip yielding 500 frames must not outrank 50 deliberate
  photographs on the leaderboard (§11.3), and because re-extraction with
  different settings has to remain possible.

Sharpness uses `imageops.measure_quality` — the *same* Tenengrad implementation
that scores uploaded stills. That identity is the point (§16.4): a photo and an
extracted frame must be comparable, and two implementations would drift.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from tirtha.imageops import measure_quality

# 150-300, not 500 (internal design notes). Five hundred frames from one upload
# collides with the MESHOPS_MAX_IMAGES OOM guard and with COLMAP's known poor
# scaling, and a two-minute walkaround at 300 frames is already heavily
# redundant.
DEFAULT_FRAME_BUDGET = 200

# Candidates scored per kept frame. Higher finds sharper frames and costs more
# decode; 3 is enough to skip a motion-blurred moment without decoding
# everything.
CANDIDATES_PER_FRAME = 3

ACCEPTED_SUFFIXES = frozenset({".mp4", ".mov"})

# Transfer characteristics that mean HDR. The container rarely says so; the
# video stream does.
_HDR_TRANSFERS = frozenset({"smpte2084", "arib-std-b67"})


class VideoUnavailable(RuntimeError):
    """ffmpeg/ffprobe missing, or the file is not decodable."""


# Limits on untrusted video (review 2026-11-22 §5). ffmpeg and ffprobe parse
# whatever a contributor uploads, on a worker that acks late, so each run is
# bounded in protocols, wall-clock time, duration and resolution.
#
# `file` only: a crafted playlist or reference movie must not make ffmpeg open
# a network URL or another protocol on our behalf.
PROTOCOL_WHITELIST = "file"


def _ffprobe_timeout() -> float:
    return float(getattr(settings, "VIDEO_FFPROBE_TIMEOUT", 120))


def _ffmpeg_timeout() -> float:
    return float(getattr(settings, "VIDEO_FFMPEG_TIMEOUT", 1800))


def _max_duration_s() -> float:
    """Only this much of a video is decoded; the rest is ignored."""
    return float(getattr(settings, "VIDEO_MAX_DURATION_S", 900))


def _max_pixels() -> int:
    """Width x height above which a video is refused. Default: 8K UHD."""
    return int(getattr(settings, "VIDEO_MAX_PIXELS", 7680 * 4320))


@dataclass(frozen=True)
class VideoInfo:
    duration_s: float | None
    frame_rate: float | None
    codec: str
    created_time: str | None
    is_hdr: bool
    width: int | None
    height: int | None


def _binary(name: str) -> str:
    path = getattr(settings, f"{name.upper()}_PATH", None) or shutil.which(name)
    if not path:
        raise VideoUnavailable(
            f"{name} is not installed. Video uploads cannot be processed "
            "without it (internal design notes)."
        )
    return str(path)


def probe(path: Path | str) -> VideoInfo:
    """
    Read stream metadata with ffprobe.

    MP4/MOV carry no EXIF -- they use QuickTime / ISO-BMFF atoms -- so this is
    ffprobe's job, not Pillow's (§16.4).
    """
    path = Path(path)
    argv = [
        _binary("ffprobe"),
        "-v",
        "error",
        "-protocol_whitelist",
        PROTOCOL_WHITELIST,
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        completed = subprocess.run(  # noqa: S603
            argv, capture_output=True, text=True, timeout=_ffprobe_timeout()
        )
        data = json.loads(completed.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise VideoUnavailable(f"Could not probe {path.name}: {exc}") from exc

    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        raise VideoUnavailable(f"{path.name} contains no video stream.")
    stream = streams[0]
    fmt = data.get("format", {})

    frame_rate = None
    raw_rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or ""
    if "/" in raw_rate:
        num, _, den = raw_rate.partition("/")
        try:
            frame_rate = float(num) / float(den) if float(den) else None
        except (TypeError, ValueError, ZeroDivisionError):
            frame_rate = None

    return VideoInfo(
        duration_s=float(fmt["duration"]) if fmt.get("duration") else None,
        frame_rate=frame_rate,
        codec=stream.get("codec_name", ""),
        created_time=(fmt.get("tags") or {}).get("creation_time"),
        is_hdr=stream.get("color_transfer") in _HDR_TRANSFERS,
        width=stream.get("width"),
        height=stream.get("height"),
    )


def extraction_command(
    source: Path | str,
    out_dir: Path | str,
    *,
    fps: float,
    is_hdr: bool,
    ffmpeg: str = "ffmpeg",
    max_duration_s: float | None = None,
) -> list[str]:
    """
    Build the ffmpeg invocation.

    The tonemap chain is not optional on HDR footage: without it, frames come
    out washed out and mis-toned, which hurts feature matching and wrecks GS
    colour consistency (§16.2).
    """
    filters = [f"fps={fps}"]
    if is_hdr:
        filters = [
            "zscale=transfer=linear",
            "tonemap=hable:desat=0",
            "zscale=transfer=bt709:matrix=bt709:primaries=bt709",
            "format=yuv420p",
            *filters,
        ]
    # `-t` before `-i` limits how much input is read, so an hour-long upload
    # costs no more than `VIDEO_MAX_DURATION_S` of decode.
    limit = ["-t", f"{max_duration_s:g}"] if max_duration_s else []
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-protocol_whitelist",
        PROTOCOL_WHITELIST,
        *limit,
        "-i",
        str(source),
        "-vf",
        ",".join(filters),
        # Highest-quality JPEG ffmpeg emits. These are working copies; the
        # video is the master and is retained (§16.5).
        "-qscale:v",
        "2",
        str(Path(out_dir) / "cand_%06d.jpg"),
    ]


def select_sharpest(candidates: list[Path], budget: int) -> list[Path]:
    """
    Keep the sharpest frame from each evenly sized block.

    Blocks rather than a global top-N: taking the globally sharpest frames
    would cluster them in whichever few seconds happened to be steadiest, and
    leave the rest of the walkaround uncovered. Coverage matters more than peak
    sharpness for reconstruction.
    """
    if not candidates or budget <= 0:
        return []
    if len(candidates) <= budget:
        return list(candidates)

    block = len(candidates) / budget
    kept = []
    for index in range(budget):
        start = int(index * block)
        end = max(int((index + 1) * block), start + 1)
        window = candidates[start:end]
        best, best_score = None, -1.0
        for path in window:
            try:
                score = measure_quality(path).sharpness
            except ValueError:
                continue
            if score > best_score:
                best, best_score = path, score
        if best is not None:
            kept.append(best)
    return kept


@dataclass
class ExtractionResult:
    video_id: str
    candidates: int
    kept: int
    is_hdr: bool


def _store_frames(source_video, kept, candidates) -> int:
    """
    Write the kept frames and insert their rows, assigning `seq`.

    The files are written first, **outside** the site lock; only the `seq`
    reservation and the row inserts happen under `select_for_update`, the same
    lock `views.store_uploaded_images` takes (review 2026-11-22 §2). Without
    the lock, a still upload and a video landing at the same site read the
    same maximum and wrote the same `seq`.
    """
    from django.core.files import File
    from django.db import transaction

    from tirtha.models import Image, Site

    pending = []
    try:
        for frame_path in kept:
            image = Image(
                contribution=source_video.contribution,
                source_video=source_video,
                # The candidate's own index, so a frame can be traced back to
                # its moment in the video even after re-extraction with
                # different settings.
                frame_index=candidates.index(frame_path),
            )
            with open(frame_path, "rb") as handle:
                image.image.save(f"{image.ID}.jpg", File(handle), save=False)
            pending.append(image)

        with transaction.atomic():
            # `seq` continues the site's existing sequence: frames are a
            # capture sequence and must sort after whatever came before (§2.9).
            site = Site.objects.select_for_update().get(
                ID=source_video.contribution.site_id
            )
            highest = (
                Image.objects.filter(contribution__site=site)
                .exclude(seq__isnull=True)
                .order_by("-seq")
                .values_list("seq", flat=True)
                .first()
            )
            next_seq = (highest + 1) if highest is not None else 0
            for offset, image in enumerate(pending):
                image.seq = next_seq + offset
                image.save()
    except Exception:
        # No row points at these files; do not leave them in the served tree.
        for image in pending:
            if image._state.adding and image.image:
                image.image.delete(save=False)
        raise
    return len(pending)


def extract_frames(source_video, *, budget: int | None = None) -> ExtractionResult:
    """
    Extract frames from a `SourceVideo` into `Image` rows.

    Runs on the `ingest` queue, never inline: a signal executes inside the
    request/response cycle and extracting a two-minute 4K video takes minutes
    (§16.4).

    **Idempotent** (review 2026-11-22 §2). The task acks late, so a worker
    restart redelivers it; that used to insert a second full set of frames. A
    video whose extraction completed is now skipped, and the frames of one
    that was interrupted are removed before starting again -- except any a
    run already used, which are never deleted.
    """
    import tempfile

    from tirtha.models import Image

    budget = budget or int(
        getattr(settings, "VIDEO_FRAME_BUDGET", DEFAULT_FRAME_BUDGET)
    )

    existing = Image.objects.filter(source_video=source_video)
    # `frames_extracted` is written only after every frame row exists, so it is
    # the completion marker. Not re-derived from the rows: frames a moderator
    # deleted since must not come back on a redelivery.
    if source_video.frames_extracted:
        return ExtractionResult(
            video_id=str(source_video.ID),
            candidates=0,
            kept=existing.count(),
            is_hdr=source_video.is_hdr,
        )
    # A partial set from an interrupted attempt. Deleted one by one so
    # django-cleanup removes the files too.
    for partial in existing.filter(runs__isnull=True):
        partial.delete()

    media = Path(settings.MEDIA_ROOT)
    path = media / source_video.video.name
    if not path.exists():
        raise VideoUnavailable(f"Video file missing: {path}")

    info = probe(path)
    if info.width and info.height and info.width * info.height > _max_pixels():
        raise VideoUnavailable(
            f"{path.name} is {info.width}x{info.height}, above the "
            f"{_max_pixels()}-pixel limit (VIDEO_MAX_PIXELS)."
        )
    source_video.duration_s = info.duration_s
    source_video.frame_rate = info.frame_rate
    source_video.codec = info.codec
    source_video.is_hdr = info.is_hdr
    source_video.save()

    # Decode enough candidates to choose from, but not the whole video: at
    # CANDIDATES_PER_FRAME per kept frame, a two-minute clip yields a few
    # hundred rather than several thousand. Only the first
    # `VIDEO_MAX_DURATION_S` seconds are decoded, so the rate is spread over
    # that span, not over the whole file.
    max_duration = _max_duration_s()
    span = min(info.duration_s, max_duration) if info.duration_s else None
    wanted = budget * CANDIDATES_PER_FRAME
    fps = (wanted / span) if span else 2.0
    fps = max(min(fps, info.frame_rate or fps), 0.1)

    with tempfile.TemporaryDirectory() as tmp:
        argv = extraction_command(
            path,
            tmp,
            fps=fps,
            is_hdr=info.is_hdr,
            ffmpeg=_binary("ffmpeg"),
            max_duration_s=max_duration,
        )
        try:
            completed = subprocess.run(  # noqa: S603
                argv, capture_output=True, text=True, timeout=_ffmpeg_timeout()
            )
        except subprocess.TimeoutExpired as exc:
            raise VideoUnavailable(
                f"ffmpeg took longer than {_ffmpeg_timeout():g}s on {path.name}."
            ) from exc
        candidates = sorted(Path(tmp).glob("cand_*.jpg"))
        if completed.returncode != 0 and not candidates:
            raise VideoUnavailable(
                f"ffmpeg failed on {path.name}: {completed.stderr[:400]}"
            )

        kept = select_sharpest(candidates, budget)
        stored = _store_frames(source_video, kept, candidates)

    source_video.frames_extracted = stored
    source_video.save()
    return ExtractionResult(
        video_id=str(source_video.ID),
        candidates=len(candidates),
        kept=stored,
        is_hdr=info.is_hdr,
    )
