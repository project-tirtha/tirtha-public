"""
Uploading a map preview by hand (internal design notes).

`backfill_turntables` renders these from the published model and refuses for
three sites whose reconstruction is too poor to frame. Those are exactly the
ones a curator wants to hand a clip made elsewhere, so the admin has to accept
one -- and because the file is addressed by convention rather than stored on a
model field, getting the *name* right is the whole feature. A preview saved as
`x__turntable_HK3nd.mp4` is invisible to everything.
"""

import shutil
import tempfile
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings

from tirtha.admin import SiteAdmin, SiteAdminForm
from tirtha.models import Site
from tirtha.turntable import find_turntable, turntable_path

MEDIA = tempfile.mkdtemp(prefix="tirtha-turntable-admin-")


def _png():
    """
    A real PNG, because `Site.save()` opens the thumbnail with Pillow to write
    its two derivatives -- a byte-header stub raises `UnidentifiedImageError`
    before any of this code is reached.
    """
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), (90, 80, 70)).save(buffer, format="PNG")
    return SimpleUploadedFile(
        "konark_thumb.png", buffer.getvalue(), content_type="image/png"
    )


class _Messages:
    """The admin writes user messages; a bare RequestFactory request has none."""

    def __init__(self):
        self.sent = []

    def add(self, level, message, extra_tags=""):
        self.sent.append((level, message))


@override_settings(MEDIA_ROOT=MEDIA)
class TurntableUpload(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        # `verbose_id` is derived from the name and place and is unique, so
        # every test needs its own site rather than the same one.
        self.site = Site.objects.create(
            name=f"Konark Sun Temple {self.id().rsplit('.', 1)[-1]}",
            country="India",
            state="Odisha",
            district="Puri",
            thumbnail=_png(),
        )
        self.admin = SiteAdmin(Site, AdminSite())
        self.request = RequestFactory().post("/")
        self.request._messages = _Messages()

    def _save(self, **fields):
        form = SiteAdminForm(instance=self.site)
        form.cleaned_data = {
            "turntable_upload": None,
            "turntable_clear": False,
            **fields,
        }
        self.admin.save_model(self.request, self.site, form, change=True)

    def _clip(self, name="orbit.mp4", size=2048):
        return SimpleUploadedFile(name, b"\x00" * size, content_type="video/mp4")

    def test_stores_it_under_the_name_the_map_looks_for(self):
        """
        The one thing that must be exact.

        Nothing records where the preview is; `find_turntable` derives it from
        the thumbnail's own path. A file stored anywhere else exists on disk and
        is invisible to the API, the map and this admin page alike.
        """
        self._save(turntable_upload=self._clip())
        expected = turntable_path(self.site.thumbnail.name)
        self.assertTrue(default_storage.exists(expected))
        self.assertEqual(
            find_turntable(self.site.thumbnail.name, default_storage), expected
        )

    def test_keeps_the_uploaded_format(self):
        # A GIF has to stay a GIF: the client builds an `<img>` for it and a
        # `<video>` for an MP4, and a GIF in a `<video>` renders nothing.
        self._save(turntable_upload=self._clip("orbit.gif"))
        found = find_turntable(self.site.thumbnail.name, default_storage)
        self.assertTrue(found.endswith(".gif"), found)

    def test_replacing_an_mp4_with_a_gif_leaves_only_the_gif(self):
        """
        Both would otherwise sit on disk, and `find_turntable` checks `.mp4`
        first -- so the curator uploads a new preview, is told it worked, and
        the map goes on showing the old one.
        """
        self._save(turntable_upload=self._clip("orbit.mp4"))
        self._save(turntable_upload=self._clip("orbit.gif"))

        stem = self.site.thumbnail.name.rsplit(".", 1)[0]
        self.assertFalse(default_storage.exists(f"{stem}__turntable.mp4"))
        self.assertTrue(default_storage.exists(f"{stem}__turntable.gif"))

    def test_clearing_removes_the_file(self):
        self._save(turntable_upload=self._clip())
        self._save(turntable_clear=True)
        self.assertIsNone(find_turntable(self.site.thumbnail.name, default_storage))

    def test_clearing_when_there_is_nothing_is_not_an_error(self):
        self._save(turntable_clear=True)
        self.assertIsNone(find_turntable(self.site.thumbnail.name, default_storage))

    def test_a_save_that_touches_neither_leaves_the_preview_alone(self):
        self._save(turntable_upload=self._clip())
        before = find_turntable(self.site.thumbnail.name, default_storage)
        self._save()
        self.assertEqual(
            find_turntable(self.site.thumbnail.name, default_storage), before
        )


class UploadValidation(TestCase):
    """What the form refuses, before anything reaches the disk."""

    def setUp(self):
        self.site = Site.objects.create(
            name=f"Konark Sun Temple {self.id().rsplit('.', 1)[-1]}",
            thumbnail=_png(),
        )

    def _form(self, upload=None, clear=False):
        return SiteAdminForm(
            data={"name": self.site.name, "turntable_clear": clear},
            files={"turntable_upload": upload} if upload else {},
            instance=self.site,
        )

    def test_refuses_a_format_the_map_cannot_show(self):
        form = self._form(SimpleUploadedFile("orbit.mov", b"\x00" * 64))
        self.assertFalse(form.is_valid())
        self.assertIn("turntable_upload", form.errors)

    def test_refuses_something_far_too_large_for_a_hover_tile(self):
        # The rendered previews are ~15 KB. A phone clip is tens of megabytes,
        # and this is fetched on hover.
        oversize = SimpleUploadedFile(
            "orbit.mp4",
            b"\x00" * (SiteAdminForm.MAX_UPLOAD + 1),
            content_type="video/mp4",
        )
        form = self._form(oversize)
        self.assertFalse(form.is_valid())
        self.assertIn("too large", " ".join(form.errors["turntable_upload"]))

    def test_refuses_upload_and_remove_together(self):
        form = self._form(SimpleUploadedFile("orbit.mp4", b"\x00" * 64), clear=True)
        self.assertFalse(form.is_valid())

    def test_refuses_when_the_site_has_no_thumbnail(self):
        """
        The preview's name is derived from the thumbnail's, so without one
        there is no path to store it at -- and silently choosing a different
        convention would put it where nothing looks.
        """
        self.site.thumbnail = ""
        self.site.save()
        form = self._form(SimpleUploadedFile("orbit.mp4", b"\x00" * 64))
        self.assertFalse(form.is_valid())
        self.assertIn("thumbnail", " ".join(form.errors["__all__"]).lower())

    def test_accepts_every_format_the_client_can_render(self):
        for name in ("orbit.mp4", "orbit.webm", "orbit.gif", "orbit.webp"):
            with self.subTest(name=name):
                form = self._form(SimpleUploadedFile(name, b"\x00" * 512))
                form.is_valid()
                self.assertNotIn("turntable_upload", form.errors)


@override_settings(MEDIA_ROOT=MEDIA)
class ReplacingAPreviewIsSafe(TestCase):
    """
    The old preview survives a failed replacement.

    `save_model` deleted the current file before `default_storage.save` had a
    chance to fail, so a storage error left the site with neither -- it lost a
    working preview in order to store a broken one.
    """

    def setUp(self):
        self.site = Site.objects.create(
            name=f"Swap Temple {self.id().rsplit('.', 1)[-1]}", thumbnail=_png()
        )
        self.admin = SiteAdmin(Site, AdminSite())
        self.request = RequestFactory().post("/")
        self.request._messages = _Messages()

    def _save(self, **fields):
        form = SiteAdminForm(instance=self.site)
        form.cleaned_data = {
            "turntable_upload": None,
            "turntable_clear": False,
            **fields,
        }
        self.admin.save_model(self.request, self.site, form, change=True)

    def _clip(self, name="orbit.mp4"):
        return SimpleUploadedFile(name, b"\x00" * 1024, content_type="video/mp4")

    def test_a_failed_store_leaves_the_old_preview_in_place(self):
        self._save(turntable_upload=self._clip())
        before = find_turntable(self.site.thumbnail.name, default_storage)
        self.assertIsNotNone(before)

        with patch("tirtha.admin.os.replace", side_effect=OSError("disk full")):
            self._save(turntable_upload=self._clip("orbit.webm"))

        self.assertEqual(
            find_turntable(self.site.thumbnail.name, default_storage),
            before,
            "a failed replacement destroyed the working preview",
        )

    def test_a_failed_store_leaves_no_scratch_file_behind(self):
        self._save(turntable_upload=self._clip())
        with patch("tirtha.admin.os.replace", side_effect=OSError("disk full")):
            self._save(turntable_upload=self._clip("orbit.webm"))

        stem = self.site.thumbnail.name.rsplit(".", 1)[0]
        leftovers = [
            name
            for name in default_storage.listdir(stem.rsplit("/", 1)[0])[1]
            if ".incoming" in name
        ]
        self.assertEqual(leftovers, [])

    def test_replacing_with_the_same_extension_still_works(self):
        # The same name in and out is the common case, and the one a
        # delete-first implementation handled by accident.
        self._save(turntable_upload=self._clip())
        self._save(turntable_upload=self._clip())
        self.assertIsNotNone(find_turntable(self.site.thumbnail.name, default_storage))
