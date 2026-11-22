"""
`upload` had no rate limit.

It is the most expensive thing an authenticated caller can do -- it writes
files, fully decodes every image, reads EXIF, and holds one of four gunicorn
workers for the length of a body nginx allows to reach 1 GB.
"""

from __future__ import annotations

import io
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image as PILImage

from tirtha.models import Contributor, Site


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (32, 32), (90, 90, 90)).save(buffer, format="JPEG")
    return buffer.getvalue()


@override_settings(UPLOAD_RATE_LIMIT=3, UPLOAD_RATE_WINDOW=600)
class UploadIsThrottled(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        patcher = patch("tirtha.views.auth.GOOGLE_LOGIN", True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.alice = Contributor.objects.create(
            name="Alice", email="alice@example.org", active=True
        )
        self.bob = Contributor.objects.create(
            name="Bob", email="bob@example.org", active=True
        )
        self._sign_in(self.alice)

    def _sign_in(self, contributor):
        session = self.client.session
        session["tirtha_user_info"] = {
            "email": contributor.email,
            "name": contributor.name,
        }
        session.save()

    def _post(self):
        return self.client.post(
            reverse("upload"),
            {
                "mesh_vid": self.site.verbose_id,
                "images": SimpleUploadedFile(
                    "a.jpg", _jpeg(), content_type="image/jpeg"
                ),
            },
        )

    def test_the_fourth_request_in_a_window_is_refused(self):
        for _ in range(3):
            self.assertNotEqual(self._post().status_code, 429)
        response = self._post()
        self.assertEqual(response.status_code, 429)
        self.assertIn("retry_after", response.json())

    def test_the_limit_is_per_contributor_not_per_address(self):
        """
        A university or a phone network puts thousands of people behind one
        address; keying on it would throttle a crowd for one person's batch.
        """
        for _ in range(3):
            self._post()
        self.assertEqual(self._post().status_code, 429)

        self._sign_in(self.bob)
        self.assertNotEqual(self._post().status_code, 429)

    def test_an_unauthenticated_caller_is_refused_before_the_throttle(self):
        """The cheap check runs first, so a flood costs no cache writes."""
        session = self.client.session
        session["tirtha_user_info"] = None
        session.save()
        for _ in range(10):
            response = self.client.post(
                reverse("upload"), {"mesh_vid": self.site.verbose_id}
            )
            self.assertNotEqual(response.status_code, 429)

    @override_settings(UPLOAD_RATE_LIMIT=0)
    def test_a_zero_limit_disables_it(self):
        for _ in range(6):
            self.assertNotEqual(self._post().status_code, 429)
