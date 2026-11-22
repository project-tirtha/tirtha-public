"""
Tests for the upload endpoint (internal design notes).

The behaviour change that matters: **full resolution is the default and the
flag is inverted.** It used to be `allow_full_resolution`, so a client that
omitted it got the lossy path; it is `compress` now, so a client that omits it
gets the better one. Defaults should fail toward keeping data, not discarding
it.
"""

import io
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image as PILImage

from tirtha.models import Contribution, Contributor, Image, Site, SourceVideo


def _jpeg(name="photo.jpg", size=(64, 64)) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    PILImage.new("RGB", size, (140, 90, 40)).save(buffer, format="JPEG", quality=95)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


class _Base(TestCase):
    """
    `views.auth.GOOGLE_LOGIN` is captured at import, so `override_settings` cannot
    reach it -- the same module-level-capture pattern fixed in `imageops` and
    `signals` (internal design notes). Patched here rather than reworking views.py,
    which is out of scope for this unit; noted as an open item.
    """

    def setUp(self):
        patcher = patch("tirtha.views.auth.GOOGLE_LOGIN", True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.contributor = Contributor.objects.create(
            name="T", email="t@example.org", active=True
        )
        session = self.client.session
        session["tirtha_user_info"] = {
            "email": self.contributor.email,
            "name": self.contributor.name,
        }
        session.save()

    def _post(self, **payload):
        payload.setdefault("mesh_vid", self.site.verbose_id)
        return self.client.post(reverse("upload"), payload)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class FidelityTests(_Base):
    def test_images_are_stored_at_full_resolution_by_default(self):
        with patch("tirtha.views.post_save_contrib_imageops.delay"):
            response = self._post(images=[_jpeg(size=(1200, 900))])
        self.assertEqual(response.json()["status"], "Success")
        image = Image.objects.get()
        self.assertFalse(image.is_compressed)

    def test_compression_happens_only_when_asked_for(self):
        with patch("tirtha.views.post_save_contrib_imageops.delay"):
            self._post(images=[_jpeg(size=(1200, 900))], compress="1")
        self.assertTrue(Image.objects.get().is_compressed)

    def test_a_missing_flag_means_full_resolution_not_compression(self):
        # The inversion is the point: losing the field must not silently
        # degrade the upload.
        with patch("tirtha.views.post_save_contrib_imageops.delay"):
            self._post(images=[_jpeg()])
        self.assertFalse(Image.objects.get().is_compressed)

    def test_the_original_filename_is_kept(self):
        with patch("tirtha.views.post_save_contrib_imageops.delay"):
            self._post(images=[_jpeg("IMG_4021.jpg")])
        self.assertEqual(Image.objects.get().original_filename, "IMG_4021.jpg")


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class VideoTests(_Base):
    def _video(self, name="clip.mp4"):
        return SimpleUploadedFile(
            name, b"\x00\x00\x00 ftypmp42", content_type="video/mp4"
        )

    def test_a_video_is_stored_and_extraction_is_enqueued(self):
        # Enqueued on commit (review 2026-11-22 §4), which a TestCase only runs
        # when asked to.
        with (
            patch("tirtha.views.extract_video_frames_task.delay") as task,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self._post(videos=[self._video()])
        self.assertEqual(response.json()["status"], "Success")
        self.assertEqual(SourceVideo.objects.count(), 1)
        task.assert_called_once()

    def test_image_checks_are_not_run_on_a_video_only_contribution(self):
        # There are no frames yet; the extraction task enqueues the checks
        # itself once there are.
        with (
            patch("tirtha.views.extract_video_frames_task.delay"),
            patch("tirtha.views.post_save_contrib_imageops.delay") as checks,
        ):
            self._post(videos=[self._video()])
        checks.assert_not_called()

    def test_photos_and_video_can_arrive_together(self):
        # One visit is one contribution, even when it produced both.
        with (
            patch("tirtha.views.extract_video_frames_task.delay"),
            patch("tirtha.views.post_save_contrib_imageops.delay"),
        ):
            self._post(images=[_jpeg()], videos=[self._video()])
        self.assertEqual(Contribution.objects.count(), 1)
        contribution = Contribution.objects.get()
        self.assertEqual(contribution.images.count(), 1)
        self.assertEqual(contribution.videos.count(), 1)

    def test_an_unsupported_container_is_refused_by_name(self):
        response = self._post(videos=[self._video("clip.avi")])
        self.assertIn("clip.avi", response.json()["output"])
        self.assertEqual(SourceVideo.objects.count(), 0)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class RejectionTests(_Base):
    def test_an_empty_upload_is_refused(self):
        # Masks are a third thing an upload may legitimately carry
        # (internal design notes), so the refusal names all three.
        self.assertIn("No images, videos or masks", self._post().json()["output"])

    def test_a_contribution_is_not_left_behind_when_nothing_usable_arrives(self):
        bad = SimpleUploadedFile("x.jpg", b"not an image", content_type="image/jpeg")
        response = self._post(images=[bad])
        self.assertIn("No usable files", response.json()["output"])
        self.assertEqual(Contribution.objects.count(), 0)

    def test_a_completed_site_accepts_nothing(self):
        self.site.completed = True
        self.site.save()
        self.assertIn("not accepting", self._post(images=[_jpeg()]).json()["output"])

    def test_a_banned_contributor_is_refused(self):
        self.contributor.banned = True
        self.contributor.save()
        self.assertIn("not authorized", self._post(images=[_jpeg()]).json()["output"])
