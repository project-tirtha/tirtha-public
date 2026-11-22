"""
EXIF has to survive the server's own conversions.

`prepare_upload` converts HEIC to JPEG, and optionally compresses. Both went
through `Image.save(..., format="JPEG")`, which writes no EXIF unless it is
handed some -- so every HEIC arrived carrying a camera and was stored without
one, and `read_exif` then read the converted file and found nothing.

iPhones shoot HEIC by default. This was the entire camera identity of a whole
class of upload, and with it the focal-length prior COLMAP calibrates from.
"""

from __future__ import annotations

import io

from django.test import TestCase
from PIL import Image as PILImage
from PIL import ImageOps

from tirtha.exif import read_exif


def _jpeg_with_exif(size=(40, 30), orientation=None) -> bytes:
    image = PILImage.new("RGB", size, (200, 120, 60))
    exif = PILImage.Exif()
    exif[271] = "Apple"
    exif[272] = "iPhone 15 Pro"
    exif[37386] = 6.765
    if orientation is not None:
        exif[274] = orientation
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def _tags(data: bytes) -> dict:
    return dict(PILImage.open(io.BytesIO(data)).getexif())


class ConversionKeepsTheCamera(TestCase):
    def test_convert_to_jpeg_carries_the_exif(self):
        from tirtha.uploads import _convert_to_jpeg

        stored = _convert_to_jpeg(
            _jpeg_with_exif(), "IMG_0001.heic", converted_from=".heic"
        )
        tags = _tags(stored.data)
        self.assertEqual(tags.get(271), "Apple")
        self.assertEqual(tags.get(272), "iPhone 15 Pro")
        self.assertAlmostEqual(float(tags.get(37386)), 6.765, places=3)

    def test_server_compression_carries_the_exif(self):
        from tirtha.uploads import _compress

        stored = _compress(_jpeg_with_exif(), "IMG_0001.jpg")
        tags = _tags(stored.data)
        self.assertEqual(tags.get(271), "Apple")
        self.assertAlmostEqual(float(tags.get(37386)), 6.765, places=3)

    def test_the_pixels_are_not_rotated_twice(self):
        """
        `_open` runs `exif_transpose`, which rotates the pixels and drops the
        Orientation tag. Carrying the rest across is therefore safe -- but if
        Orientation ever survived, everything downstream would rotate again.
        """
        from tirtha.uploads import _convert_to_jpeg

        # Orientation 6 means "rotate 90": a 40x30 source becomes 30x40.
        stored = _convert_to_jpeg(
            _jpeg_with_exif(size=(40, 30), orientation=6),
            "IMG_0002.heic",
            converted_from=".heic",
        )
        out = PILImage.open(io.BytesIO(stored.data))
        self.assertEqual(out.size, (30, 40))
        self.assertIsNone(_tags(stored.data).get(274))
        # And a second pass must not move it again.
        self.assertEqual(ImageOps.exif_transpose(out).size, (30, 40))

    def test_read_exif_now_finds_a_camera_on_a_converted_file(self):
        """The end of the chain: what the ingest path actually records."""
        from tirtha.uploads import _convert_to_jpeg

        stored = _convert_to_jpeg(
            _jpeg_with_exif(), "IMG_0003.heic", converted_from=".heic"
        )
        path = self._write(stored.data, "IMG_0003.jpg")
        found = read_exif(path)
        self.assertEqual(found.get("camera_make"), "Apple")
        self.assertEqual(found.get("camera_model"), "iPhone 15 Pro")
        self.assertTrue(found.get("focal_length"))

    def test_an_image_with_no_exif_still_converts(self):
        from tirtha.uploads import _convert_to_jpeg

        plain = io.BytesIO()
        PILImage.new("RGB", (20, 20), (10, 10, 10)).save(plain, format="JPEG")
        stored = _convert_to_jpeg(
            plain.getvalue(), "IMG_0004.heic", converted_from=".heic"
        )
        self.assertTrue(stored.data)
        self.assertEqual(_tags(stored.data), {})

    def _write(self, data: bytes, name: str):
        import tempfile
        from pathlib import Path

        directory = Path(tempfile.mkdtemp())
        path = directory / name
        path.write_bytes(data)
        return path
