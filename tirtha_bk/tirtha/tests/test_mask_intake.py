"""
Matching a mask to the one image it describes.

A mask is a stencil in a single image's pixel coordinates. Pairing it with the
wrong photograph is not a degraded result, it is a wrong one: the projection
samples the subject where the subject is not, and the vote in
`tirtha_recon.gaussian_cut` reads the building as background. So every test here
is about refusing to guess.
"""

from __future__ import annotations

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image as PILImage

from tirtha.models import Contribution, Contributor, Image, Site
from tirtha.views import attach_masks


def _png(size=(64, 64), colour=255) -> bytes:
    buf = io.BytesIO()
    PILImage.new("L", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", size, (120, 90, 40)).save(buf, format="JPEG")
    return buf.getvalue()


def _upload(name: str, data: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type="image/png")


class _Base(TestCase):
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

    def _image(self, original: str, size=(64, 64), contribution=None) -> Image:
        img = Image(
            contribution=contribution or self.contribution, original_filename=original
        )
        img.image.save(original, SimpleUploadedFile(original, _jpeg(size)), save=True)
        return img


class MatchingByName(_Base):
    def test_a_mask_named_after_the_image_stem_matches(self):
        img = self._image("IMG_0117.jpeg")
        report = attach_masks(self.site, [_upload("IMG_0117.png", _png())])
        assert report["attached"] == 1, report
        img.refresh_from_db()
        self.assertTrue(img.mask)
        self.assertEqual(img.mask_source, "contributor")

    def test_colmaps_own_double_suffix_matches_too(self):
        # `materialise_masks` writes `<image filename>.png`; a contributor who
        # copies that convention should not be told they are wrong.
        img = self._image("IMG_0117.jpeg")
        report = attach_masks(self.site, [_upload("IMG_0117.jpeg.png", _png())])
        assert report["attached"] == 1, report
        img.refresh_from_db()
        self.assertTrue(img.mask)

    def test_the_image_uuid_matches_exactly(self):
        # How a mask supplied long after the contribution names its target
        # without depending on a filename that may repeat.
        img = self._image("IMG_0117.jpeg")
        report = attach_masks(self.site, [_upload(f"{img.ID}.png", _png())])
        assert report["attached"] == 1, report

    def test_an_unmatched_mask_is_reported_not_dropped(self):
        self._image("IMG_0117.jpeg")
        report = attach_masks(self.site, [_upload("SOMETHING_ELSE.png", _png())])
        self.assertEqual(report["attached"], 0)
        self.assertIn("SOMETHING_ELSE.png", report["rejected"])


class RefusingToGuess(_Base):
    def test_an_ambiguous_name_is_refused(self):
        # Two contributors, both with an IMG_0001.jpg. Attaching to either
        # would silently annotate a stranger's photograph.
        other = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )
        self._image("IMG_0001.jpg")
        self._image("IMG_0001.jpg", contribution=other)

        report = attach_masks(self.site, [_upload("IMG_0001.png", _png())])
        self.assertEqual(report["attached"], 0)
        self.assertIn("ambiguous", report["rejected"]["IMG_0001.png"])

    def test_scoping_to_a_contribution_resolves_that_ambiguity(self):
        # The same two images, but the mask arrived *with* one contribution, so
        # there is no ambiguity to refuse.
        other = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )
        mine = self._image("IMG_0001.jpg")
        self._image("IMG_0001.jpg", contribution=other)

        report = attach_masks(
            self.site,
            [_upload("IMG_0001.png", _png())],
            scope=self.contribution.images.all(),
        )
        assert report["attached"] == 1, report
        mine.refresh_from_db()
        self.assertTrue(mine.mask)


class Validation(_Base):
    def test_a_mask_of_the_wrong_size_is_refused(self):
        # The common mistake: drawn on a resized copy. Nothing downstream can
        # detect it, so it has to be caught in front of the person who can fix it.
        self._image("IMG_0117.jpeg", size=(64, 64))
        report = attach_masks(self.site, [_upload("IMG_0117.png", _png(size=(32, 32)))])
        self.assertEqual(report["attached"], 0)
        self.assertIn("32x32", report["rejected"]["IMG_0117.png"])

    def test_a_jpeg_mask_is_refused(self):
        self._image("IMG_0117.jpeg")
        report = attach_masks(
            self.site, [SimpleUploadedFile("IMG_0117.jpg", _jpeg(), "image/jpeg")]
        )
        self.assertEqual(report["attached"], 0)
        self.assertIn("PNG", report["rejected"]["IMG_0117.jpg"])

    def test_one_bad_mask_does_not_discard_the_good_ones(self):
        self._image("A.jpeg")
        self._image("B.jpeg")
        report = attach_masks(
            self.site,
            [
                _upload("A.png", _png()),
                _upload("NOPE.png", _png()),
                _upload("B.png", _png()),
            ],
        )
        self.assertEqual(report["attached"], 2)
        self.assertEqual(list(report["rejected"]), ["NOPE.png"])
