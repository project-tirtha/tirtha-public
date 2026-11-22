"""
Storing an upload at full fidelity (internal design notes).

Full resolution is the default now; compression is an explicit opt-in.

**Passthrough beats re-encoding.** Re-encoding an already-JPEG file at *any*
quality is strictly worse than leaving it alone -- the previous full-resolution
path re-encoded at quality 95, which is still lossy for no benefit. A JPEG or
PNG is stored byte-for-byte.

HEIC/HEIF, WebP, GIF, BMP and TIFF are the exception, because COLMAP cannot
read them (or reads them badly); so is any file whose content is not the format
its name promises. The original is kept as the master and a JPEG working copy
is derived (§17.1, §17.2).

The live Django site never compressed at all; the aggressive
`maxSizeMB: 1.5` cap is a *new-frontend* behaviour, and 1.5 MB on a 12 MP
photograph discards exactly the detail feature matching depends on.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

# Stored untouched when the content really is what the name says. Anything
# COLMAP and Pillow both read directly needs no processing, and processing it
# can only lose information.
PASSTHROUGH_FORMATS_BY_SUFFIX = {
    ".jpg": ("JPEG", "MPO"),
    ".jpeg": ("JPEG", "MPO"),
    ".png": ("PNG",),
}
PASSTHROUGH_SUFFIXES = frozenset(PASSTHROUGH_FORMATS_BY_SUFFIX)

# Converted to JPEG, because COLMAP cannot read them (or reads them badly).
# The original is retained. `.heics`/`.heifs` are HEIF sequences: pillow-heif
# opens their primary still (it accepts the `hevs`/`msf1` brands), and that
# still is what is converted. One with no still image fails to decode and is
# refused like any other unreadable file.
CONVERT_SUFFIXES = frozenset(
    {".heic", ".heif", ".heics", ".heifs", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
)

# Every name an upload may carry. Mirrored exactly by `VALID_IMAGE_EXTENSIONS`
# in `frontend/src/utils/imageValidation.js`; a test checks the two agree.
ACCEPTED_SUFFIXES = PASSTHROUGH_SUFFIXES | CONVERT_SUFFIXES

# The only decoders that ever see uploaded bytes (review 2026-11-22 §5).
#
# `Image.open` with no `formats=` tries every plugin Pillow has, so an EPS --
# which Pillow hands to Ghostscript -- passed as `photo.jpg`. Ghostscript is
# not installed on the production host today, which is the only reason that
# was not code execution. Every open now names this set and nothing else.
#
# The content need *not* match the name: Android saves WebP as `.jpg`, and
# PNG-named-JPEG is common. Anything that decodes within this set is accepted;
# when it is not the format its name promises it is converted to JPEG rather
# than stored under a misleading name. Anything outside the set is refused.
#
# `MPO` is a JPEG (multi-picture, from many phones). `HEIF` is the format name
# pillow-heif registers for .heic and .heif alike; `AVIF` is Pillow's own
# plugin. GIF, MPO and TIFF are converted from their first frame.
SAFE_FORMATS = ("JPEG", "MPO", "PNG", "HEIF", "AVIF", "WEBP", "GIF", "BMP", "TIFF")


class UploadRejected(ValueError):
    """A file whose name or content is not an accepted image type."""


@dataclass(frozen=True)
class StoredUpload:
    """What was stored, and whether anything was done to it."""

    data: bytes
    filename: str
    converted_from: str | None
    compressed: bool

    @property
    def passthrough(self) -> bool:
        return self.converted_from is None and not self.compressed


def _compression_quality() -> int:
    return int(getattr(settings, "UPLOAD_COMPRESS_QUALITY", 85))


def _max_dimension() -> int:
    return int(getattr(settings, "UPLOAD_COMPRESS_MAX_DIMENSION", 4096))


def prepare_upload(
    raw: bytes, filename: str, *, compress: bool = False
) -> StoredUpload:
    """
    Decide what actually gets written for one uploaded file.

    `compress` is opt-in and off by default -- that flip is the substance of
    §17. When it is off and the format is already usable, the bytes are stored
    exactly as uploaded.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        raise UploadRejected(
            f"{filename}: only {', '.join(sorted(ACCEPTED_SUFFIXES))} images "
            "are accepted."
        )
    if suffix in {".heic", ".heif", ".heics", ".heifs"}:
        # Named HEIF: a missing pillow-heif is a deployment fault worth saying.
        _register_heif()
    else:
        # Anything may *contain* HEIF; offer the decoder if it is installed.
        _register_heif(required=False)

    # Decode within the safe set only. This raises for anything outside it.
    image_format = _detect_format(raw)

    if compress:
        return _compress(raw, filename)

    if image_format in PASSTHROUGH_FORMATS_BY_SUFFIX.get(suffix, ()):
        # Verified (`_detect_format` ran `verify()`), and stored exactly as
        # uploaded: nothing is re-encoded.
        return StoredUpload(
            data=raw, filename=filename, converted_from=None, compressed=False
        )

    # A convertible format, or content that is not what its name says.
    return _convert_to_jpeg(raw, filename, converted_from=suffix)


def _registered(formats) -> list[str]:
    """
    The subset Pillow can open right now. Naming an unregistered format (HEIF
    before pillow-heif is loaded) makes `Image.open` raise KeyError.
    """
    from PIL import Image as PILImage

    PILImage.init()
    available = [f for f in formats if f in PILImage.OPEN]
    if not available:
        raise UploadRejected(f"No decoder available for {', '.join(formats)}.")
    return available


def _detect_format(raw: bytes) -> str:
    """
    The Pillow format of these bytes, decoded with the safe set only.

    `verify()` reads headers (and, for PNG, checksums) without re-encoding, so
    a passthrough file is still checked before it is stored. It consumes the
    file object, which is why `_open` reopens.
    """
    from PIL import Image as PILImage

    with PILImage.open(io.BytesIO(raw), formats=_registered(SAFE_FORMATS)) as image:
        image_format = image.format
        if image_format not in SAFE_FORMATS:  # defence in depth
            raise UploadRejected(f"{image_format} images are not accepted.")
        image.verify()
    return image_format


def _open(raw: bytes):
    from PIL import Image as PILImage
    from PIL import ImageOps as PILImageOps

    image = PILImage.open(io.BytesIO(raw), formats=_registered(SAFE_FORMATS))
    # Phones record orientation in EXIF rather than rotating pixels; COLMAP
    # reads pixels.
    image = PILImageOps.exif_transpose(image)
    return image


def _register_heif(*, required: bool = True) -> None:
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError as exc:
        if not required:
            return
        raise ValueError(
            "pillow-heif is not installed; HEIC uploads cannot be converted."
        ) from exc


def _convert_to_jpeg(raw: bytes, filename: str, *, converted_from: str) -> StoredUpload:
    image = _open(raw)
    # The EXIF, carried across the conversion.
    #
    # `save()` writes none unless it is handed some, so every HEIC arrived with
    # a camera and was stored without one -- and `read_exif` then read the
    # converted file and found nothing. iPhones shoot HEIC by default, so this
    # was the whole camera identity of an entire class of upload, and with it
    # the focal-length prior COLMAP calibrates from.
    #
    # Safe to pass through: `_open` has already run `exif_transpose`, which
    # rotates the pixels *and* drops the Orientation tag from the EXIF it
    # returns. Verified against Pillow 12.3.0 -- orientation comes back absent,
    # so nothing downstream rotates a second time.
    exif = image.info.get("exif")
    image = image.convert("RGB")
    buffer = io.BytesIO()
    # quality=100 with subsampling off: this is a one-time conversion of a
    # master we are keeping, so the working copy should lose as little as the
    # format allows.
    image.save(
        buffer,
        format="JPEG",
        quality=100,
        subsampling=0,
        **({"exif": exif} if exif else {}),
    )
    return StoredUpload(
        data=buffer.getvalue(),
        filename=str(Path(filename).with_suffix(".jpg")),
        converted_from=converted_from,
        compressed=False,
    )


def _compress(raw: bytes, filename: str) -> StoredUpload:
    """Opt-in only. Server-side, so the limits are ours rather than a browser's."""
    _register_heif(required=False)
    image = _open(raw)
    # Kept for the same reason as in `_convert_to_jpeg`: a smaller working copy
    # is still a photograph of something, taken by a camera we want to know.
    exif = image.info.get("exif")
    image = image.convert("RGB")
    limit = _max_dimension()
    if max(image.size) > limit:
        image.thumbnail((limit, limit))
    buffer = io.BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=_compression_quality(),
        optimize=True,
        **({"exif": exif} if exif else {}),
    )
    return StoredUpload(
        data=buffer.getvalue(),
        filename=str(Path(filename).with_suffix(".jpg")),
        converted_from=None,
        compressed=True,
    )


# --- Masks (internal design notes) ------------------------------------------------

# A mask is a binary stencil. COLMAP requires PNG, and every other format here
# is either lossy (which would turn a hard edge into a gradient) or unreadable
# by the tools downstream.
MASK_SUFFIXES = frozenset({".png"})


class MaskRejected(ValueError):
    """
    A mask that cannot be trusted to mean what it says.

    Raised at intake rather than tolerated downstream, on purpose. The cull in
    `tirtha_recon.gaussian_cut` skips a mask whose dimensions disagree with its
    camera, and a skipped mask is invisible: the contributor is told their
    upload succeeded, the operator sees a cut that removed nothing, and nobody
    learns why. Failing here puts the message in front of the one person who
    can fix it.
    """


def prepare_mask(raw: bytes, filename: str, *, expected_size: tuple[int, int]) -> bytes:
    """
    Validate a mask against the image it claims to describe, and normalise it.

    `expected_size` is the `(width, height)` of that image. A mask drawn on a
    resized copy is the common mistake and the one that must not pass: the
    projection has no way to know, so every pixel would be sampled in the wrong
    place and the vote would read the subject as background.

    Normalised to single-channel greyscale. That is lossless for a stencil and
    removes an ambiguity the passthrough rule above would otherwise leave --
    an RGBA mask whose alpha carries the shape and whose RGB is uniformly white
    reads as "everything is subject" if only the first channel is sampled.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in MASK_SUFFIXES:
        raise MaskRejected(
            f"{filename}: masks must be PNG, not {suffix or 'a file with no extension'}."
        )

    from PIL import Image as PILImage

    try:
        with PILImage.open(io.BytesIO(raw), formats=["PNG"]) as handle:
            handle.verify()
        with PILImage.open(io.BytesIO(raw), formats=["PNG"]) as handle:
            if handle.size != expected_size:
                raise MaskRejected(
                    f"{filename}: mask is {handle.size[0]}x{handle.size[1]}, "
                    f"but the image it matches is {expected_size[0]}x{expected_size[1]}. "
                    "A mask has to be drawn on the image at its original size."
                )
            # `A` when there is one, because a mask exported from an editor
            # often carries its shape in alpha over a uniform colour.
            band = "A" if "A" in handle.getbands() else None
            grey = handle.getchannel(band) if band else handle.convert("L")
            out = io.BytesIO()
            grey.save(out, format="PNG", optimize=True)
            return out.getvalue()
    except MaskRejected:
        raise
    except Exception as exc:  # noqa: BLE001
        raise MaskRejected(f"{filename}: not a readable PNG ({exc}).") from exc
