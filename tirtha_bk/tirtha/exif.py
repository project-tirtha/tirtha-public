"""
Reading EXIF from an image file.

Lifted out of `management/commands/backfill_exif.py`, which is where it was
written and the only place that could use it. The columns it fills -- `date_taken`,
`camera_make`, `camera_model`, `focal_length` -- matter at *ingest*, not only in a
backfill: `seq` orders by capture time (internal design notes), and COLMAP groups
photographs into cameras by what the EXIF says they were taken with. All 19,394
images in the archive have these columns empty, and one consequence was COLMAP
solving 73 separate intrinsics for 73 photographs of one fountain taken on one
phone.

Header reads only, never a decode: this runs inside the upload request.

GPS is deliberately not read by default. Contributors were never asked to share
location (internal design notes), and lifting coordinates out of a file into a queryable
column is new processing of personal data. It takes an explicit argument and a
human decision, here as in the backfill.
"""

import math
from pathlib import Path

from django.utils import timezone
from PIL import Image as PILImage
from PIL import UnidentifiedImageError
from PIL.ExifTags import GPSTAGS, TAGS

# EXIF stores this as "YYYY:MM:DD HH:MM:SS", which is not ISO-8601.
_EXIF_DATE = "%Y:%m:%d %H:%M:%S"


def _clean(value, limit: int) -> str:
    """
    Make an EXIF string safe to store.

    **PostgreSQL rejects NUL bytes in text outright**; SQLite accepts them
    silently. Camera makes and models in this archive genuinely contain them --
    fixed-width EXIF fields padded with \x00 -- so a backfill that does not
    strip them dies partway through, and only on the database that matters.
    Other control characters go too: they cannot render and are never meaningful
    here.
    """
    text = str(value)
    return "".join(ch for ch in text if ch.isprintable() or ch == " ").strip()[:limit]


def _rational(value):
    """
    EXIF rationals, defensively.

    ZeroDivisionError is not hypothetical: the live archive contains GPS
    rationals with a zero denominator, and `float()` raises on them. A malformed
    tag should yield no value, not stop a backfill over 19,000 files.

    Pillow 12 does not raise on a zero denominator any more -- `IFDRational(50,
    0)` returns `nan` from `float()` and `"nan"` from `str()`. So the guard has
    to be on the value, not only on the exception: without it a camera with one
    malformed rational was recorded as having a focal length of "nan", and a GPS
    tag as latitude nan, which `is not None` happily let through.
    """
    try:
        number = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return number if math.isfinite(number) else None


def _gps_decimal(coord, ref):
    """EXIF GPS is degrees/minutes/seconds plus a hemisphere letter."""
    if isinstance(coord, (str, bytes)):
        # A string is iterable and would unpack into three characters, each of
        # which `_rational` refuses -- so this returned None by luck, not by
        # decision. Say so.
        return None
    try:
        degrees, minutes, seconds = (_rational(v) for v in coord)
        if None in (degrees, minutes, seconds):
            return None
        decimal = degrees + minutes / 60 + seconds / 3600
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return -decimal if ref in ("S", "W") else decimal


def read_exif(path: Path, *, include_gps: bool = False) -> dict:
    """
    Everything 2.6's columns want, or as much as survived.

    Never raises: a corrupt or EXIF-less file is a normal outcome across an
    archive of this age, not an error worth stopping a backfill for.
    """
    found = {}
    try:
        with PILImage.open(path) as img:
            raw = img.getexif()
    except (OSError, UnidentifiedImageError, ValueError):
        return found
    if not raw:
        return found

    tags = {TAGS.get(k, k): v for k, v in raw.items()}
    ifd = {}
    try:
        # DateTimeOriginal lives in the Exif sub-IFD, not the top level.
        ifd = {TAGS.get(k, k): v for k, v in raw.get_ifd(0x8769).items()}
    except (AttributeError, KeyError, OSError):
        pass

    make = tags.get("Make")
    model = tags.get("Model")
    if make:
        found["camera_make"] = _clean(make, 200)
    if model:
        found["camera_model"] = _clean(model, 200)

    focal = ifd.get("FocalLength") or tags.get("FocalLength")
    if focal is not None:
        # str() on a rational can itself divide by zero -- and on Pillow 12 it
        # returns "nan" instead, which is worse because nothing raises.
        try:
            text = _clean(focal, 100)
        except ZeroDivisionError:
            text = ""
        if text and text.lower() not in ("nan", "inf", "-inf"):
            found["focal_length"] = text

    stamp = (
        ifd.get("DateTimeOriginal")
        or ifd.get("DateTimeDigitized")
        or tags.get("DateTime")
    )
    if stamp:
        try:
            naive = timezone.datetime.strptime(_clean(stamp, 32), _EXIF_DATE)
            # EXIF carries no timezone. Stamping the project timezone is a
            # guess, but a consistent one, and ordering only compares images
            # within a single contribution anyway (internal design notes).
            found["date_taken"] = timezone.make_aware(
                naive, timezone.get_default_timezone()
            )
        except (ValueError, OverflowError):
            pass

    # OFF BY DEFAULT, deliberately. 49% of the live archive carries GPS in the
    # file (measured on a 400-image sample), but §2.5 decided that contributors
    # are *asked* whether to share location -- and nobody asked the people who
    # uploaded these. Lifting coordinates out of old files into queryable
    # columns is new processing of personal data that was never consented to,
    # so it takes an explicit flag and a human decision.
    gps = {}
    if include_gps:
        try:
            gps = {GPSTAGS.get(k, k): v for k, v in raw.get_ifd(0x8825).items()}
        except (AttributeError, KeyError, OSError):
            gps = {}
    if gps:
        lat = _gps_decimal(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef"))
        lon = _gps_decimal(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))
        if lat is not None and lon is not None:
            found["gps_latitude"] = lat
            found["gps_longitude"] = lon

    return found
