"""
Tests for the EXIF backfill (internal design notes).

2.6 added the EXIF columns; nothing ever filled them, so §2.9's ordering rule
was silently taking its `(created_at, ID)` fallback for all 19,321 archived
images. Measured on a 400-file sample of the live store: 95% carry a usable
capture time, 49% also carry GPS.

That second number is why the privacy default is pinned here as hard as the
parsing is.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase
from PIL import Image as PILImage

# These moved to `tirtha.exif` so the upload path could use them too: EXIF now
# gets read when an image arrives, not only when somebody remembers to run the
# backfill. The command still owns the walk over existing rows.
from tirtha.exif import _gps_decimal, _rational, read_exif


def _write_jpeg_with_exif(path: Path, tags: dict[int, object]):
    """
    A JPEG carrying real EXIF, built with Pillow only.

    Pillow can write an Exif object back, which is enough to exercise the
    reader without adding piexif as a test dependency.
    """
    img = PILImage.new("RGB", (8, 8), (120, 120, 120))
    exif = img.getexif()
    for tag_id, value in tags.items():
        exif[tag_id] = value
    img.save(path, "JPEG", exif=exif)
    return path


# EXIF tag numbers, so the test does not depend on Pillow's name table.
_MAKE, _MODEL, _DATETIME = 0x010F, 0x0110, 0x0132


class RationalTests(TestCase):
    def test_a_zero_denominator_yields_no_value(self):
        # Not hypothetical: the live archive contains GPS rationals with a zero
        # denominator, and float() raises ZeroDivisionError on them. A single
        # malformed tag must not stop a backfill over 19,000 files.
        from fractions import Fraction

        class _Bad:
            def __float__(self):
                raise ZeroDivisionError

        self.assertIsNone(_rational(_Bad()))
        self.assertEqual(_rational(Fraction(3, 2)), 1.5)

    def test_non_numeric_values_yield_no_value(self):
        self.assertIsNone(_rational("not a number"))
        self.assertIsNone(_rational(None))


class GpsDecimalTests(TestCase):
    def test_degrees_minutes_seconds_convert(self):
        self.assertAlmostEqual(_gps_decimal((20, 14, 42.36), "N"), 20.2451, places=4)

    def test_southern_and_western_hemispheres_are_negative(self):
        self.assertLess(_gps_decimal((20, 0, 0), "S"), 0)
        self.assertLess(_gps_decimal((85, 0, 0), "W"), 0)

    def test_a_malformed_coordinate_yields_no_value(self):
        self.assertIsNone(_gps_decimal(("x", "y", "z"), "N"))
        self.assertIsNone(_gps_decimal(None, "N"))


class ReadExifTests(TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_camera_and_capture_time_are_recovered(self):
        path = _write_jpeg_with_exif(
            self.dir / "a.jpg",
            {
                _MAKE: "LENOVO",
                _MODEL: "TB373FU",
                _DATETIME: "2026:02:24 07:35:23",
            },
        )
        found = read_exif(path)
        self.assertEqual(found["camera_make"], "LENOVO")
        self.assertEqual(found["camera_model"], "TB373FU")
        self.assertIsNotNone(found["date_taken"])

    def test_the_capture_time_is_timezone_aware(self):
        # EXIF carries no timezone; stamping the project one is a guess, but a
        # consistent one -- and ordering only compares within a contribution.
        path = self.dir / "a.jpg"
        img = PILImage.new("RGB", (8, 8))
        exif = img.getexif()
        exif[_DATETIME] = "2026:02:24 07:35:23"
        img.save(path, "JPEG", exif=exif)
        self.assertIsNotNone(read_exif(path)["date_taken"].tzinfo)

    def test_a_malformed_timestamp_is_skipped_not_fatal(self):
        path = self.dir / "a.jpg"
        img = PILImage.new("RGB", (8, 8))
        exif = img.getexif()
        exif[_DATETIME] = "not a date"
        img.save(path, "JPEG", exif=exif)
        self.assertNotIn("date_taken", read_exif(path))

    def test_an_image_without_exif_yields_nothing(self):
        path = self.dir / "bare.jpg"
        PILImage.new("RGB", (8, 8)).save(path, "JPEG")
        self.assertEqual(read_exif(path), {})

    def test_a_corrupt_file_yields_nothing_rather_than_raising(self):
        # Normal across an archive of this age, not an error worth stopping for.
        path = self.dir / "corrupt.jpg"
        path.write_bytes(b"not a jpeg at all")
        self.assertEqual(read_exif(path), {})

    def test_a_missing_file_yields_nothing(self):
        self.assertEqual(read_exif(self.dir / "absent.jpg"), {})


class GpsConsentTests(TestCase):
    """
    GPS is off by default, and that is a consent decision rather than an
    oversight.

    49% of the live archive carries coordinates in the file. Contributors were
    never asked to share location (§2.5), so lifting it into queryable columns
    is new processing of personal data and takes an explicit flag.

    The gate is tested directly rather than through a written GPS IFD: Pillow's
    round-trip of that structure is version-dependent, and a fixture that
    silently fails to embed anything would make this test pass for the wrong
    reason.
    """

    class _FakeExif(dict):
        def get_ifd(self, tag):
            if tag == 0x8825:
                return {1: "N", 2: (20, 14, 42.36), 3: "E", 4: (85, 50, 17.88)}
            return {}

    def _patched_open(self):
        from contextlib import contextmanager
        from unittest.mock import patch

        exif = self._FakeExif({0x010F: "CAM"})

        class _Img:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def getexif(self_inner):
                return exif

        @contextmanager
        def _ctx():
            with patch("PIL.Image.open", return_value=_Img()):
                yield

        return _ctx()

    def test_gps_is_not_recorded_by_default(self):
        with self._patched_open():
            found = read_exif(Path("/any.jpg"))
        self.assertNotIn("gps_latitude", found)
        self.assertNotIn("gps_longitude", found)
        # The rest of the EXIF is still read -- the gate is GPS-specific.
        self.assertEqual(found["camera_make"], "CAM")

    def test_gps_is_recorded_when_explicitly_requested(self):
        with self._patched_open():
            found = read_exif(Path("/any.jpg"), include_gps=True)
        self.assertAlmostEqual(found["gps_latitude"], 20.2451, places=4)
        self.assertAlmostEqual(found["gps_longitude"], 85.8383, places=4)


class PostgresSafetyTests(TestCase):
    """
    EXIF strings must be safe for the database that matters.

    **PostgreSQL rejects NUL bytes in text outright; SQLite accepts them
    silently.** Camera makes and models in this archive genuinely contain them
    -- fixed-width EXIF fields padded with \\x00 -- so the backfill died
    partway through, and only on PostgreSQL. Found running it for real.
    """

    def test_nul_bytes_are_stripped(self):
        from tirtha.exif import _clean

        self.assertEqual(_clean("Canon\x00\x00", 200), "Canon")

    def test_other_control_characters_go_too(self):
        # They cannot render and are never meaningful in a camera make.
        from tirtha.exif import _clean

        self.assertEqual(_clean("A\x01B\x1f", 200), "AB")

    def test_ordinary_text_is_only_trimmed(self):
        from tirtha.exif import _clean

        self.assertEqual(_clean("  NIKON D3500  ", 200), "NIKON D3500")

    def test_the_length_limit_is_applied(self):
        from tirtha.exif import _clean

        self.assertEqual(len(_clean("x" * 500, 200)), 200)

    def test_non_ascii_names_survive(self):
        # Vernacular camera names are printable and must not be stripped.
        from tirtha.exif import _clean

        self.assertEqual(_clean("Niño\x00", 200), "Niño")
