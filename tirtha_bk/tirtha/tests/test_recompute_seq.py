"""
`seq` orders the symlinks COLMAP's sequential matcher walks, so it should follow
capture order. Migration 0020 established that rule but ran before any EXIF had
been read, so `date_taken` was null everywhere and every image took the upload-
order fallback.
"""

from __future__ import annotations

import io
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from PIL import Image as PILImage

from tirtha.models import Contribution, Contributor, Image, Site


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (8, 8), (1, 2, 3)).save(buffer, format="JPEG")
    return buffer.getvalue()


class SeqFollowsCaptureOrder(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.contributor = Contributor.objects.create(
            name="T", email="t@example.org", active=True
        )

    def _contribution(self, when):
        contribution = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )
        Contribution.objects.filter(pk=contribution.pk).update(contributed_at=when)
        contribution.refresh_from_db()
        return contribution

    def _image(self, contribution, name, taken):
        img = Image(contribution=contribution, original_filename=name, date_taken=taken)
        img.image.save(name, SimpleUploadedFile(name, _jpeg()), save=True)
        return img

    def test_images_are_ordered_by_capture_time_inside_a_contribution(self):
        now = timezone.now()
        contribution = self._contribution(now)
        # Uploaded c, a, b; captured a, b, c.
        c = self._image(contribution, "c.jpg", now + timedelta(minutes=2))
        a = self._image(contribution, "a.jpg", now)
        b = self._image(contribution, "b.jpg", now + timedelta(minutes=1))

        call_command("recompute_image_seq", site=str(self.site.ID))
        for img in (a, b, c):
            img.refresh_from_db()
        self.assertEqual([a.seq, b.seq, c.seq], [0, 1, 2])

    def test_contributions_stay_in_arrival_order(self):
        """
        EXIF clocks are not comparable across contributors -- drift, timezones,
        cameras never set -- so a later contribution never interleaves into an
        earlier one however its images are stamped.
        """
        now = timezone.now()
        first = self._contribution(now)
        second = self._contribution(now + timedelta(days=1))
        # The second contribution's photo claims to predate the first's.
        late = self._image(first, "late.jpg", now + timedelta(days=10))
        early = self._image(second, "early.jpg", now - timedelta(days=10))

        call_command("recompute_image_seq", site=str(self.site.ID))
        late.refresh_from_db()
        early.refresh_from_db()
        self.assertLess(late.seq, early.seq)

    def test_images_with_no_capture_time_fall_back_to_upload_order(self):
        now = timezone.now()
        contribution = self._contribution(now)
        first = self._image(contribution, "1.jpg", None)
        second = self._image(contribution, "2.jpg", None)
        call_command("recompute_image_seq", site=str(self.site.ID))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertLess(first.seq, second.seq)

    def test_dry_run_changes_nothing(self):
        now = timezone.now()
        contribution = self._contribution(now)
        c = self._image(contribution, "c.jpg", now + timedelta(minutes=2))
        a = self._image(contribution, "a.jpg", now)
        Image.objects.update(seq=None)

        call_command("recompute_image_seq", site=str(self.site.ID), dry_run=True)
        a.refresh_from_db()
        c.refresh_from_db()
        self.assertIsNone(a.seq)
        self.assertIsNone(c.seq)

    def test_it_is_idempotent(self):
        now = timezone.now()
        contribution = self._contribution(now)
        self._image(contribution, "b.jpg", now + timedelta(minutes=1))
        self._image(contribution, "a.jpg", now)

        call_command("recompute_image_seq", site=str(self.site.ID))
        before = list(Image.objects.order_by("ID").values_list("ID", "seq"))
        call_command("recompute_image_seq", site=str(self.site.ID))
        self.assertEqual(
            before, list(Image.objects.order_by("ID").values_list("ID", "seq"))
        )
