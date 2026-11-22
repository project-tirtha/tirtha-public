"""
Tests for upload fidelity and video extraction (internal design notes).

The headline property is that an already-usable file is stored *byte for byte*.
Re-encoding a JPEG at any quality is strictly worse than leaving it alone, and
the previous full-resolution path still re-encoded at quality 95 -- lossy for no
benefit.
"""

import io
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from PIL import Image as PILImage

from tirtha.uploads import prepare_upload
from tirtha.videoops import (
    extraction_command,
    select_sharpest,
)


def _jpeg_bytes(size=(64, 64), quality=95) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", size, (120, 90, 60)).save(
        buffer, format="JPEG", quality=quality
    )
    return buffer.getvalue()


def _png_bytes(size=(64, 64)) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", size, (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


class PassthroughTests(TestCase):
    def test_a_jpeg_is_stored_byte_for_byte(self):
        raw = _jpeg_bytes()
        stored = prepare_upload(raw, "photo.jpg")
        self.assertEqual(stored.data, raw)
        self.assertTrue(stored.passthrough)

    def test_a_png_is_stored_byte_for_byte(self):
        raw = _png_bytes()
        self.assertEqual(prepare_upload(raw, "photo.png").data, raw)

    def test_the_extension_case_does_not_matter(self):
        raw = _jpeg_bytes()
        self.assertEqual(prepare_upload(raw, "PHOTO.JPEG").data, raw)

    def test_full_resolution_is_the_default(self):
        # The substance of section 17 is this flip.
        large = _jpeg_bytes(size=(5000, 4000))
        stored = prepare_upload(large, "big.jpg")
        self.assertFalse(stored.compressed)
        self.assertEqual(stored.data, large)


class CompressionTests(TestCase):
    def test_compression_is_opt_in(self):
        raw = _jpeg_bytes(size=(5000, 4000))
        self.assertTrue(prepare_upload(raw, "big.jpg", compress=True).compressed)

    @override_settings(UPLOAD_COMPRESS_MAX_DIMENSION=256)
    def test_compression_bounds_the_long_edge(self):
        stored = prepare_upload(_jpeg_bytes(size=(2000, 1000)), "b.jpg", compress=True)
        self.assertLessEqual(max(PILImage.open(io.BytesIO(stored.data)).size), 256)

    def test_compression_actually_reduces_size(self):
        raw = _jpeg_bytes(size=(3000, 2000), quality=100)
        stored = prepare_upload(raw, "b.jpg", compress=True)
        self.assertLess(len(stored.data), len(raw))


def _bytes_as(fmt: str, size=(64, 64), **kwargs) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", size, (200, 30, 90)).save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


class ConversionTests(TestCase):
    """
    Decoding is restricted to a safe raster set (review 2026-11-22 §5, as
    corrected by the follow-up review): JPEG/MPO, PNG, HEIF/AVIF, WebP, GIF,
    BMP and TIFF. Within it, the content need not match the name -- a
    mismatched or non-passthrough format is converted to JPEG, as HEAD did.
    Anything outside it (EPS, PSD, ...) is refused and never reaches its
    decoder.
    """

    def _assert_converted(self, raw: bytes, name: str, *, size=(64, 64)):
        stored = prepare_upload(raw, name)
        self.assertIsNotNone(stored.converted_from)
        self.assertFalse(stored.passthrough)
        self.assertEqual(Path(stored.filename).suffix, ".jpg")
        self.assertEqual(Path(stored.filename).stem, Path(name).stem)
        with PILImage.open(io.BytesIO(stored.data)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, size)
        return stored

    def test_webp_is_converted(self):
        self._assert_converted(_bytes_as("WEBP"), "photo.webp")

    def test_gif_is_converted_from_its_first_frame(self):
        frames = [PILImage.new("RGB", (64, 64), c) for c in ((255, 0, 0), (0, 0, 255))]
        buffer = io.BytesIO()
        frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:])
        stored = self._assert_converted(buffer.getvalue(), "anim.gif")
        with PILImage.open(io.BytesIO(stored.data)) as image:
            red, green, blue = image.convert("RGB").getpixel((32, 32))
        self.assertGreater(red, blue)

    def test_bmp_is_converted(self):
        self._assert_converted(_bytes_as("BMP"), "odd.bmp")

    def test_tiff_is_converted(self):
        self._assert_converted(_bytes_as("TIFF"), "scan.tif")
        self._assert_converted(_bytes_as("TIFF"), "scan.TIFF")

    def test_png_named_jpg_is_converted(self):
        self._assert_converted(_png_bytes(), "photo.jpg")

    def test_jpeg_named_png_is_converted(self):
        self._assert_converted(_jpeg_bytes(), "photo.png")

    def test_android_webp_named_jpg_is_converted(self):
        self._assert_converted(_bytes_as("WEBP"), "IMG_20261122.jpg")

    def test_jpeg_named_heic_is_converted(self):
        self._assert_converted(_jpeg_bytes(), "IMG_0001.heic")

    def test_heif_sequence_names_are_accepted(self):
        import pillow_heif

        pillow_heif.register_heif_opener()
        raw = _bytes_as("HEIF")
        # A sequence brand, as an iPhone burst writes; pillow-heif opens the
        # primary still.
        raw = raw[:8] + b"msf1" + raw[12:]
        self._assert_converted(raw, "burst.heics")
        self._assert_converted(raw, "burst.heifs")

    def test_an_unknown_suffix_is_refused_even_if_readable(self):
        from tirtha.uploads import UploadRejected

        with self.assertRaises(UploadRejected):
            prepare_upload(_jpeg_bytes(), "photo.psd")
        with self.assertRaises(UploadRejected):
            prepare_upload(_jpeg_bytes(), "photo")

    def test_a_format_outside_the_safe_set_is_refused(self):
        from PIL import UnidentifiedImageError

        # Pillow reads PPM, but nothing outside the safe set may decode uploads.
        with self.assertRaises((UnidentifiedImageError, ValueError)):
            prepare_upload(_bytes_as("PPM"), "photo.jpg")

    def test_an_eps_named_jpg_is_never_handed_to_ghostscript(self):
        from PIL import UnidentifiedImageError

        eps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 8 8\nshowpage\n"
        with patch("PIL.EpsImagePlugin.EpsImageFile._open") as eps_open:
            with self.assertRaises((UnidentifiedImageError, ValueError)):
                prepare_upload(eps, "photo.jpg")
            with self.assertRaises((UnidentifiedImageError, ValueError)):
                prepare_upload(eps, "photo.jpg", compress=True)
            with self.assertRaises((UnidentifiedImageError, ValueError)):
                prepare_upload(eps, "photo.tiff")
        eps_open.assert_not_called()

    def test_unreadable_bytes_raise_rather_than_storing_garbage(self):
        from PIL import UnidentifiedImageError

        with self.assertRaises(UnidentifiedImageError):
            prepare_upload(b"not an image", "x.jpg")

    def test_a_mask_must_be_a_real_png(self):
        from tirtha.uploads import MaskRejected, prepare_mask

        with self.assertRaises(MaskRejected):
            prepare_mask(_jpeg_bytes(), "m.png", expected_size=(64, 64))


class FrameSelectionTests(TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _frames(self, n, sharp_upto=None):
        import numpy as np

        rng = np.random.default_rng(0)
        paths = []
        for i in range(n):
            path = self.dir / f"cand_{i:04d}.jpg"
            arr = (
                rng.integers(0, 255, (48, 48, 3), dtype=np.uint8)
                if sharp_upto is None or i < sharp_upto
                else np.full((48, 48, 3), 128, dtype=np.uint8)
            )
            PILImage.fromarray(arr).save(path)
            paths.append(path)
        return paths

    def test_the_budget_is_respected(self):
        self.assertEqual(len(select_sharpest(self._frames(60), 10)), 10)

    def test_fewer_candidates_than_budget_keeps_all(self):
        self.assertEqual(len(select_sharpest(self._frames(3), 10)), 3)

    def test_unreadable_candidates_are_skipped_not_fatal(self):
        frames = self._frames(4)
        (self.dir / "cand_9999.jpg").write_bytes(b"corrupt")
        frames.append(self.dir / "cand_9999.jpg")
        self.assertEqual(len(select_sharpest(frames, 5)), 5)

    def test_no_candidates_is_not_an_error(self):
        self.assertEqual(select_sharpest([], 10), [])

    def test_selection_spreads_across_the_clip(self):
        # Blocks rather than a global top-N: the globally sharpest frames would
        # cluster in whichever seconds happened to be steadiest and leave the
        # rest of the walkaround uncovered.
        # The first four frames are by far the sharpest.
        kept = select_sharpest(self._frames(20, sharp_upto=4), 4)
        # One per block, so the last block must be represented even though its
        # frames are flat.
        self.assertEqual(len(kept), 4)
        self.assertTrue(any(int(p.stem.split("_")[1]) >= 15 for p in kept))


class ExtractionCommandTests(TestCase):
    def test_sdr_extraction_is_a_plain_fps_filter(self):
        argv = extraction_command("/v.mov", "/out", fps=2.0, is_hdr=False)
        filters = argv[argv.index("-vf") + 1]
        self.assertEqual(filters, "fps=2.0")

    def test_hdr_footage_is_tonemapped(self):
        # Default on recent iPhones. Without this the frames come out washed
        # out and mis-toned, hurting matching and GS colour consistency.
        argv = extraction_command("/v.mov", "/out", fps=2.0, is_hdr=True)
        filters = argv[argv.index("-vf") + 1]
        self.assertIn("tonemap", filters)
        self.assertIn("zscale", filters)

    def test_the_tonemap_precedes_the_frame_rate_filter(self):
        argv = extraction_command("/v.mov", "/out", fps=2.0, is_hdr=True)
        filters = argv[argv.index("-vf") + 1]
        self.assertLess(filters.index("tonemap"), filters.index("fps="))

    def test_frames_are_written_at_high_quality(self):
        # These are working copies, but the video is the master -- there is no
        # reason to lose more than necessary.
        argv = extraction_command("/v.mov", "/out", fps=1.0, is_hdr=False)
        self.assertEqual(argv[argv.index("-qscale:v") + 1], "2")


class FrontendAllowListTests(TestCase):
    """The browser marks a file Ready only if the server will keep it."""

    def test_frontend_extensions_match_the_backend(self):
        import re

        from django.conf import settings

        from tirtha.uploads import ACCEPTED_SUFFIXES

        source = (
            Path(settings.BASE_DIR).parent
            / "frontend"
            / "src"
            / "utils"
            / "imageValidation.js"
        )
        if not source.exists():
            self.skipTest("frontend sources not present")
        match = re.search(
            r"VALID_IMAGE_EXTENSIONS\s*=\s*Object\.freeze\(\[(.*?)\]\)",
            source.read_text(),
            re.S,
        )
        self.assertIsNotNone(match)
        frontend = set(re.findall(r"'(\.[a-z0-9]+)'", match.group(1)))
        self.assertEqual(frontend, set(ACCEPTED_SUFFIXES))
