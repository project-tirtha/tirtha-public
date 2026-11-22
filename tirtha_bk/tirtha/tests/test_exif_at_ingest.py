"""
EXIF is recorded when the image arrives, not only by a backfill nobody ran.

`date_taken`, `camera_make`, `camera_model` and `focal_length` were added in 2.6
and filled by nothing: all 19,394 images in the archive read empty. Two things
depend on them -- `seq` orders by capture time (2.9), and COLMAP groups
photographs into cameras by what took them. Without that it solved 73 separate
intrinsics for 73 photographs of one fountain, taken on one phone, each
calibrated from a single view.
"""

from __future__ import annotations

import io
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image as PILImage

from tirtha.models import Contribution, Contributor, Image, Site
from tirtha.views import store_uploaded_images

_MAKE = 0x010F
_MODEL = 0x0110


def _jpeg(make: str | None = None, model: str | None = None) -> bytes:
    """A small JPEG, optionally carrying camera EXIF."""
    buf = io.BytesIO()
    img = PILImage.new("RGB", (64, 64), (120, 90, 40))
    if make or model:
        exif = PILImage.Exif()
        if make:
            exif[_MAKE] = make
        if model:
            exif[_MODEL] = model
        img.save(buf, format="JPEG", exif=exif)
    else:
        img.save(buf, format="JPEG")
    return buf.getvalue()


class ExifIsRecordedOnUpload(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.contributor = Contributor.objects.create(
            name="T", email="t@example.org", active=True
        )
        self.contribution = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )

    def _upload(self, data: bytes, name="IMG_0001.jpg"):
        store_uploaded_images(
            self.contribution,
            [SimpleUploadedFile(name, data, content_type="image/jpeg")],
            compress=False,
        )
        return Image.objects.filter(contribution=self.contribution).latest("created_at")

    def test_the_camera_is_recorded(self):
        img = self._upload(_jpeg(make="Apple", model="iPhone 17 Pro Max"))
        self.assertEqual(img.camera_make, "Apple")
        self.assertEqual(img.camera_model, "iPhone 17 Pro Max")

    def test_an_image_without_exif_still_uploads(self):
        # Most of the archive is exactly this, because the browser re-encoded it.
        # A missing camera is not a failed contribution.
        img = self._upload(_jpeg())
        self.assertEqual(img.camera_make, "")
        self.assertTrue(img.image)

    def test_a_failing_exif_read_does_not_fail_the_upload(self):
        """
        The guard around the read, tested directly.

        A file that is not an image at all never reaches this: `prepare_upload`
        rejects it and the row is never created. What this covers is the other
        case -- a real photograph whose metadata makes Pillow throw. It is still
        a photograph, and losing it over a malformed header would be worse than
        not knowing which camera took it.
        """
        with patch("tirtha.views.uploads.read_exif", side_effect=ValueError("bad IFD")):
            img = self._upload(_jpeg("Apple", "iPhone"))
        self.assertTrue(Image.objects.filter(pk=img.pk).exists())
        self.assertEqual(img.camera_make, "")

    def test_two_photographs_from_one_phone_agree(self):
        """
        What COLMAP needs to collapse them into a single camera.

        The failure this guards against is not an exception -- it is 73 images
        that each look like a different camera because none of them says what
        took it.
        """
        a = self._upload(_jpeg("Apple", "iPhone 17 Pro Max"), "IMG_0001.jpg")
        b = self._upload(_jpeg("Apple", "iPhone 17 Pro Max"), "IMG_0002.jpg")
        self.assertEqual(
            (a.camera_make, a.camera_model), (b.camera_make, b.camera_model)
        )
        self.assertTrue(a.camera_model, "no camera recorded at all")


class WhetherTheSiteHasOneCamera(TestCase):
    """
    What decides `--ImageReader.single_camera`.

    Both answers are wrong in the other's situation. With no EXIF, COLMAP gives
    each photograph its own intrinsics -- 73 for 73 views of one fountain from
    one phone. Forced to one camera, a crowdsourced site solves a single focal
    length for a mixture of lenses. So this is answered from what the images
    record, and an archive that records nothing gets COLMAP's own default.
    """

    def setUp(self):
        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.contributor = Contributor.objects.create(
            name="T", email="t@example.org", active=True
        )

    def _image(self, make="", model="", focal="", contribution=None):
        c = contribution or Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )
        img = Image(
            contribution=c,
            camera_make=make,
            camera_model=model,
            focal_length=focal,
            safety_status="safe",
        )
        img.image.save("x.jpg", SimpleUploadedFile("x.jpg", _jpeg()), save=True)
        return img

    def _decide(self):
        from tirtha.workers import GSOps

        ops = GSOps.__new__(GSOps)
        ops.site = self.site
        return GSOps._one_camera_for_the_site(ops)

    def test_one_phone_is_one_camera(self):
        for _ in range(3):
            self._image("Apple", "iPhone 17 Pro Max", "6.765")
        self.assertTrue(self._decide())

    def test_two_phones_are_not(self):
        self._image("Apple", "iPhone 17 Pro Max", "6.765")
        self._image("Google", "Pixel 9", "6.9")
        self.assertFalse(self._decide())

    def test_no_exif_at_all_defers_to_colmap(self):
        """
        The whole archive, until the ingest change starts applying.

        Empty columns mean "we do not know", not "one camera" -- claiming one
        camera for images we know nothing about is the mistake that bends the
        geometry.
        """
        for _ in range(3):
            self._image()
        self.assertFalse(self._decide())

    def test_the_same_phone_at_two_focal_lengths_is_not_one_camera(self):
        # A zoom, or two lenses on one body. COLMAP would be solving one set of
        # intrinsics for two optical systems.
        self._image("Apple", "iPhone 17 Pro Max", "6.765")
        self._image("Apple", "iPhone 17 Pro Max", "2.220")
        self.assertFalse(self._decide())
