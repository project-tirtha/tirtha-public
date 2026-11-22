"""
A short orbit of a finished model, rendered from the model itself.

The map's hover tile was a photograph a contributor uploaded. That is a picture
of the temple, not a picture of *what this project made of it* -- and the one
thing worth showing on a map of reconstructions is the reconstruction. So once
a run is published, its artifact is rendered from a ring of camera positions,
each frame downscaled, and the frames assembled into a few seconds of video.

**It never touches the GPU, and that is the design rather than a fallback.**
`splat-transform` enumerates adapters, and on this host that is
`[0] NVIDIA RTX A6000` and `[1] llvmpipe`. The A6000 is shared with another lab
and sat at 98% utilisation with 8.9 GB resident throughout every measurement
here; asking it for a frame did not return within 120 s, while llvmpipe
rendered the same frame in 2.4 s. On a saturated card the GPU path is more than
thirty times slower *and* takes ~0.9 GB of someone else's VRAM to be slower.
Measured on the published artifact of `nHjUmrWgjsdQnjEH`: 6 frames in 22.3 s,
peak RSS 1.3 GB, `nvidia-smi` unchanged to the megabyte.

Two behaviours of the tool that are not in its README, both found the hard way:

- `-g cpu` is not a thing. It fails with "writeImage requires a createDevice
  function for GPU rasterization". The software rasteriser has to be chosen by
  *adapter index*, and the index is host-specific -- hence `software_adapter()`,
  which reads the list and matches by name. Never hardcode it: on a host where
  llvmpipe happens to be index 0, a hardcoded 1 would queue on the shared GPU.
- Rendering straight to 160x160 returns an almost empty frame -- a 90-byte file
  against 59 KB at 320x320, from the same camera and the same model. There is a
  screen-space size cutoff that discards nearly every gaussian at small output
  sizes. So frames are rendered at `RENDER_SIZE` and downscaled afterwards,
  which is what the maintainer asked for in as many words: render from the
  model, reduce, then assemble.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

# Big enough that the gaussians survive rasterisation, small enough to be cheap.
# 320 -> 160 and 640 -> 160 differ only along edges, so 320 is both the floor
# and enough.
RENDER_SIZE = 320
# What the tile actually displays.
OUTPUT_SIZE = 160
# A full turn. Twelve reads as a slideshow and forty-eight is a minute of CPU
# for no more information.
FRAME_COUNT = 24

# Names Mesa and friends give their software rasterisers.
_SOFTWARE_ADAPTER = re.compile(r"llvmpipe|lavapipe|swiftshader|softpipe", re.I)
# "[1] llvmpipe (LLVM 20.1.2, 256 bits)"
_ADAPTER_LINE = re.compile(r"^\s*\[(\d+)\]\s+(.*?)\s*$", re.M)

# What splat-transform can read. Notably not `.glb`: the published mesh is
# gltfpack output declaring KHR_mesh_quantization, EXT_meshopt_compression and
# KHR_texture_basisu, which only a PlayCanvas- or three-class engine loads --
# i.e. a headless browser, which is the cost this whole approach avoids.
RENDERABLE_SUFFIXES = (".sog", ".splat", ".ply")

# The default camera migration 0021 wrote where it could not derive one. It is
# not a framing; it is the absence of one, and pointing a renderer at it puts
# the camera inside the temple.
DEFAULT_CAMERA_POSITION = [0.0, 0.0, 5.0]


class TurntableUnavailable(RuntimeError):
    """No software adapter, no renderer, or nothing renderable to render."""


@dataclass(frozen=True)
class Camera:
    """Where to look from, at what, and which way is up."""

    position: tuple[float, float, float]
    target: tuple[float, float, float]
    up: tuple[float, float, float]
    fov: float


def _executable() -> str:
    return getattr(settings, "SPLAT_TRANSFORM_PATH", "splat-transform")


def software_adapter(executable: str | None = None) -> int:
    """
    The index of the software rasteriser, read from the tool rather than assumed.

    Raises rather than falling back to adapter 0. Silently rendering on the
    shared GPU is the one outcome this module exists to prevent, and a host
    without a software adapter should skip the preview, not take the card.
    """
    result = subprocess.run(
        [executable or _executable(), "--list-gpus"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    for index, name in _ADAPTER_LINE.findall(result.stdout):
        if _SOFTWARE_ADAPTER.search(name):
            return int(index)
    raise TurntableUnavailable(
        "no software rasteriser among the adapters splat-transform lists; "
        "refusing to render on the shared GPU"
    )


def orbit_positions(camera: Camera, frames: int = FRAME_COUNT) -> list[tuple]:
    """
    `frames` camera positions, one turn about the up axis through the target.

    Rotating the *stored* camera rather than synthesising one keeps whatever
    distance and elevation a curator chose in the viewer; only the bearing
    changes. Rodrigues, because the axis is an arbitrary unit vector -- the
    stored ups are things like (0.043, -0.999, 0.002), not a basis vector.
    """
    ax, ay, az = camera.up
    length = math.sqrt(ax * ax + ay * ay + az * az)
    if not length:
        raise TurntableUnavailable("camera up vector is zero")
    ax, ay, az = ax / length, ay / length, az / length

    vx = camera.position[0] - camera.target[0]
    vy = camera.position[1] - camera.target[1]
    vz = camera.position[2] - camera.target[2]

    positions = []
    for index in range(frames):
        angle = 2 * math.pi * index / frames
        cos, sin = math.cos(angle), math.sin(angle)
        dot = ax * vx + ay * vy + az * vz
        # v cos + (a x v) sin + a (a . v)(1 - cos)
        rx = vx * cos + (ay * vz - az * vy) * sin + ax * dot * (1 - cos)
        ry = vy * cos + (az * vx - ax * vz) * sin + ay * dot * (1 - cos)
        rz = vz * cos + (ax * vy - ay * vx) * sin + az * dot * (1 - cos)
        positions.append(
            (
                camera.target[0] + rx,
                camera.target[1] + ry,
                camera.target[2] + rz,
            )
        )
    return positions


def camera_for_run(run) -> Camera | None:
    """
    The framing the viewer already uses, if the run has a real one.

    32 of the 81 production runs carry a hand-tuned camera -- `Manual` runs,
    with ups clustered around (0, -1, 0). The other 49 hold migration 0021's
    `[0, 0, 5]` placeholder, which is not a framing at all: rendering from it
    put the camera inside the temple and produced a haze of background
    gaussians. Those fall through to `camera_from_stats`.
    """
    position = getattr(run, "cam_position", None)
    if not position or list(position) == DEFAULT_CAMERA_POSITION:
        return None
    target = getattr(run, "cam_target", None) or [0.0, 0.0, 0.0]
    up = getattr(run, "cam_up", None) or [0.0, 1.0, 0.0]
    return Camera(
        position=tuple(float(v) for v in position),
        target=tuple(float(v) for v in target),
        up=tuple(float(v) for v in up),
        fov=float(getattr(run, "cam_fov", None) or 60.0),
    )


def scene_statistics(source: Path, executable: str | None = None) -> dict:
    """Per-column min/max/median/stdDev, straight from the tool (~0.7 s)."""
    result = subprocess.run(
        [executable or _executable(), str(source), "--stats", "json", "null"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    text = result.stdout
    try:
        payload = json.loads(text[text.index("{") : text.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError) as exc:
        raise TurntableUnavailable(f"could not read scene statistics: {exc}") from exc
    return payload["stats"][0]["data"]


def camera_from_stats(
    stats: dict, fov: float = 60.0, up: tuple = (0.0, -1.0, 0.0)
) -> Camera:
    """
    A framing derived from where the gaussians actually are.

    The centre is the midpoint of the bounding box, not the median. The median
    is where *most gaussians* are, which on a scene with a broad ground plane
    is the floor -- it framed one model with the monument sitting on the bottom
    edge, because half the splats were paving. The box says where the thing is.

    The extent is the box too, but capped at four sigma per axis: a splat scene
    carries a sparse halo reaching far past the monument, and framing to
    include that frames mostly empty space.

    `up` is a parameter rather than a constant because it genuinely varies --
    the hand-tuned cameras cluster around (0, -1, 0), and a scene that came out
    of COLMAP the other way up needs (0, 1, 0). `probe_camera` decides by
    rendering; this only does the arithmetic.
    """
    low = stats["min"][:3]
    high = stats["max"][:3]
    deviation = stats["stdDev"][:3]
    median = stats["median"][:3]

    centre = []
    extent = []
    for axis in range(3):
        span = min(high[axis] - low[axis], 4 * deviation[axis]) or (
            high[axis] - low[axis]
        )
        half = span / 2
        # Clamp the box to the dense region around the median, so a halo on one
        # side does not drag the centre off the monument.
        middle = (low[axis] + high[axis]) / 2
        if abs(middle - median[axis]) > half:
            middle = median[axis]
        centre.append(middle)
        extent.append(half)

    # The two axes perpendicular to `up` are the horizontal ones.
    vertical_axis = max(range(3), key=lambda i: abs(up[i]))
    horizontal = max(extent[i] for i in range(3) if i != vertical_axis)
    vertical = extent[vertical_axis]
    distance = max(horizontal, vertical) / math.tan(math.radians(fov / 2)) + horizontal

    # Stand off along the widest horizontal axis, raised by a third of the
    # distance along `up` so the orbit looks slightly down at the monument.
    offset = [0.0, 0.0, 0.0]
    widest = max((i for i in range(3) if i != vertical_axis), key=lambda i: extent[i])
    offset[widest] = distance * 0.92
    for axis in range(3):
        offset[axis] += up[axis] * distance * 0.38

    return Camera(
        position=tuple(centre[i] + offset[i] for i in range(3)),
        target=tuple(centre),
        up=up,
        fov=fov,
    )


# The up axes worth trying when nothing tells us which one it is.
CANDIDATE_UPS = ((0.0, -1.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, -1.0))


def _recentre(camera: Camera, drift_px: tuple, size: tuple) -> Camera:
    """
    Slide the target so the monument sits in the middle of the frame.

    The derived centre is a bounding box, and a box is not where the *visible*
    monument is: one model's box spanned two units of mostly-empty height with
    the temple along the bottom edge, so the turntable showed a strip of
    masonry under a lot of sky.

    The probe frame already says where the content actually is, in pixels.
    Converting that to world units needs only the frame's angular size, and
    moving the target moves the whole orbit with it.
    """
    width, height = size
    distance = math.dist(camera.position, camera.target)
    # World units spanned by the frame at the target's depth.
    span = 2 * distance * math.tan(math.radians(camera.fov / 2))
    per_pixel = span / height

    forward = [camera.target[i] - camera.position[i] for i in range(3)]
    length = math.sqrt(sum(v * v for v in forward)) or 1.0
    forward = [v / length for v in forward]

    # Screen right is forward x up; screen up is the camera's own up.
    right = [
        forward[1] * camera.up[2] - forward[2] * camera.up[1],
        forward[2] * camera.up[0] - forward[0] * camera.up[2],
        forward[0] * camera.up[1] - forward[1] * camera.up[0],
    ]
    right_length = math.sqrt(sum(v * v for v in right))
    if not right_length:
        return camera
    right = [v / right_length for v in right]

    dx, dy = drift_px
    shift = [
        right[i] * dx * per_pixel - camera.up[i] * dy * per_pixel for i in range(3)
    ]
    return Camera(
        position=tuple(camera.position[i] + shift[i] for i in range(3)),
        target=tuple(camera.target[i] + shift[i] for i in range(3)),
        up=camera.up,
        fov=camera.fov,
    )


def probe_camera(
    source: Path,
    workdir: Path,
    adapter: int,
    stats: dict | None = None,
    executable: str | None = None,
) -> Camera:
    """
    Find a framing by rendering one frame per candidate and looking.

    For the runs with no stored camera there is nothing in the data that says
    which way is up: one model rendered correctly with (0, -1, 0) and another
    showed only a strip of paving along the bottom edge until it was flipped.
    Guessing wrong produces a confidently-published video of nothing, so this
    guesses four times at about 2.7 s each and keeps the best.

    "Best" is coverage inside a sane band, penalised by how far the visible
    content sits from the middle of the frame -- coverage alone rewards a
    camera buried inside the model, which fills the frame with haze.
    """
    from PIL import Image

    if stats is None:
        stats = scene_statistics(source, executable)
    workdir.mkdir(parents=True, exist_ok=True)

    best, best_score = None, -1.0
    for index, up in enumerate(CANDIDATE_UPS):
        camera = camera_from_stats(stats, up=up)
        probe = workdir / f"probe{index}.webp"
        try:
            render_frame(source, probe, camera, camera.position, adapter, executable)
        except (subprocess.SubprocessError, OSError):
            continue

        try:
            with Image.open(probe) as image:
                alpha = image.convert("RGBA").split()[-1]
                width, height = alpha.size
                pixels = alpha.load()
        except (OSError, ValueError):
            continue

        margin = max(2, width // 12)
        opaque = weighted_y = weighted_x = 0
        border = border_opaque = 0
        for y in range(0, height, 4):
            for x in range(0, width, 4):
                lit = pixels[x, y] > 10
                if lit:
                    opaque += 1
                    weighted_x += x
                    weighted_y += y
                if (
                    x < margin
                    or y < margin
                    or x >= width - margin
                    or y >= height - margin
                ):
                    border += 1
                    border_opaque += lit
        probe.unlink(missing_ok=True)
        if not opaque:
            continue

        sampled = (width // 4) * (height // 4)
        coverage = 100.0 * opaque / sampled
        if not COVERAGE_MIN <= coverage <= COVERAGE_MAX:
            continue

        """
        The frame's border must be mostly empty.

        This is what separates "looking at the monument" from "standing inside
        it". Coverage alone does not: a camera buried in the splats fills the
        frame with haze, which scores high and is centred, and the first
        scoring here happily picked exactly that. An object the camera is
        outside of does not reach the edge of the picture.
        """
        if border and border_opaque / border > BORDER_MAX:
            continue

        # 0 when the content is centred, 1 when it is against an edge.
        drift = math.dist(
            (weighted_x / opaque, weighted_y / opaque), (width / 2, height / 2)
        ) / (width / 2)
        score = coverage * max(0.0, 1.0 - drift)
        if score > best_score:
            best = _recentre(
                camera,
                (weighted_x / opaque - width / 2, weighted_y / opaque - height / 2),
                (width, height),
            )
            best_score = score

    if best is None:
        raise TurntableUnavailable(
            "no candidate framing showed the model; it may be empty or "
            "entirely background"
        )
    return best


def render_frame(
    source: Path,
    destination: Path,
    camera: Camera,
    position: tuple,
    adapter: int,
    executable: str | None = None,
    size: int = RENDER_SIZE,
    timeout: int = 180,
) -> None:
    """One frame, on the software adapter, with a transparent background."""
    subprocess.run(
        [
            executable or _executable(),
            "-g",
            str(adapter),
            "--no-tty",
            "-w",
            str(source),
            "--resolution",
            f"{size}x{size}",
            "--camera-pos",
            ",".join(f"{v:.5f}" for v in position),
            "--camera-target",
            ",".join(f"{v:.5f}" for v in camera.target),
            "--camera-up",
            ",".join(f"{v:.5f}" for v in camera.up),
            "--camera-fov",
            str(camera.fov),
            "--background",
            "0,0,0,0",
            str(destination),
        ],
        capture_output=True,
        timeout=timeout,
        check=True,
    )


def frame_coverage(path: Path) -> float:
    """
    The fraction of the frame that is not transparent, as a percentage.

    The one check that catches both failure classes at once: a camera inside
    the model fills the frame, and a camera pointed at nothing leaves it empty.
    """
    from PIL import Image

    with Image.open(path) as image:
        alpha = image.convert("RGBA").split()[-1]
        histogram = alpha.histogram()
    opaque = sum(histogram[11:])
    return 100.0 * opaque / max(sum(histogram), 1)


# Outside this band the frame is either the inside of the temple or empty sky.
COVERAGE_MIN = 1.5
COVERAGE_MAX = 92.0

# How much of the frame's outer border may be opaque before the camera is
# judged to be inside the model rather than looking at it. A monument framed
# from outside leaves its corners empty; a camera buried in the splats does not.
BORDER_MAX = 0.12


def assemble(frames: list[Path], destination: Path, fps: int = 12) -> int:
    """
    The frames, as an MP4 the tooltip can autoplay.

    H.264 rather than animated WebP or a GIF. Measured on identical frames:
    24 KB for H.264 at CRF 32 against 88 KB for `libwebp_anim` and far worse for
    GIF, which has no useful colour depth for a photographic render. The tile is
    56px and appears on hover, so a few tens of kilobytes is the whole budget.

    `yuv420p` and an even frame size because some decoders quietly refuse odd
    dimensions, and `+faststart` so the moov atom precedes the data -- without
    it a progressive download plays only after the whole file lands.

    Alpha is flattened onto the page's own tile background rather than carried:
    H.264 has no alpha channel, and the formats that do cost three to five
    times the bytes for a rounded corner CSS already provides.
    """
    if not frames:
        raise TurntableUnavailable("no frames to assemble")

    listing = destination.parent / f"{destination.stem}.frames.txt"
    listing.write_text("".join(f"file '{frame}'\n" for frame in frames))
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-r",
                str(fps),
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-vf",
                f"scale={OUTPUT_SIZE}:{OUTPUT_SIZE}:flags=lanczos,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "slow",
                "-crf",
                "32",
                "-movflags",
                "+faststart",
                "-an",
                # ffmpeg picks the muxer from the extension, and the temporary
                # file ends in `.tmp`. Without this it exits 234 having written
                # nothing, which reads as a rendering failure and is not one.
                "-f",
                "mp4",
                str(temporary),
            ],
            capture_output=True,
            timeout=300,
            check=True,
        )
        # Replaced in one step, so a reader never sees a half-written file and
        # a failure leaves the previous preview standing.
        temporary.replace(destination)
    finally:
        listing.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
    return destination.stat().st_size


# What a hand-uploaded preview may be. MP4 and WebM play in a `<video>`; GIF
# and animated WebP are images and need an `<img>`, which is why the API
# publishes the extension rather than assuming one.
TURNTABLE_SUFFIXES = (".mp4", ".webm", ".gif", ".webp")


def find_turntable(thumbnail_name: str, storage) -> str | None:
    """
    The site's preview, whatever form it takes, or None.

    The renderer only ever writes `.mp4`, but a curator may upload a GIF or a
    WebM by hand for a model this cannot frame -- and for the 3 sites that
    refuse to render, a hand-made preview is the only way they get one.
    """
    stem = thumbnail_name.rsplit(".", 1)[0]
    for suffix in TURNTABLE_SUFFIXES:
        name = f"{stem}__turntable{suffix}"
        try:
            if storage.exists(name):
                return name
        except (ValueError, NotImplementedError, OSError):
            continue
    return None


def turntable_path(thumbnail_name: str) -> str:
    """
    Where the video lives, given the thumbnail's storage name.

    The same convention as `preview` and `card` -- the thumbnail's own path with
    a suffix -- so there is no migration and nothing to keep in step. Kept out
    of `THUMBNAIL_DERIVATIVES` deliberately: everything in that dict is fed to
    Pillow, and Pillow will not make an MP4 out of a JPEG.
    """
    stem = thumbnail_name.rsplit(".", 1)[0]
    return f"{stem}__turntable.mp4"


def render_turntable(
    source: Path,
    destination: Path,
    camera: Camera,
    workdir: Path,
    frames: int = FRAME_COUNT,
    adapter: int | None = None,
    executable: str | None = None,
) -> int:
    """
    Render one orbit and assemble it. Returns the size of the file written.

    Raises `TurntableUnavailable` for every reason not to have one -- no
    software adapter, an unreadable artifact, a camera that frames nothing.
    Callers treat that as "keep the still image", never as a failed run: this
    is decoration on a finished reconstruction, and it must not be able to fail
    one.
    """
    if source.suffix.lower() not in RENDERABLE_SUFFIXES:
        raise TurntableUnavailable(f"{source.suffix} is not renderable")
    if adapter is None:
        adapter = software_adapter(executable)

    workdir.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    for index, position in enumerate(orbit_positions(camera, frames)):
        frame = workdir / f"frame{index:03d}.webp"
        render_frame(source, frame, camera, position, adapter, executable)
        if index == 0:
            coverage = frame_coverage(frame)
            if not COVERAGE_MIN <= coverage <= COVERAGE_MAX:
                raise TurntableUnavailable(
                    f"first frame covers {coverage:.1f}% of the image, "
                    f"outside {COVERAGE_MIN}-{COVERAGE_MAX}%; "
                    "the camera is inside the model or pointed at nothing"
                )
        rendered.append(frame)

    try:
        return assemble(rendered, destination)
    finally:
        for frame in rendered:
            frame.unlink(missing_ok=True)
