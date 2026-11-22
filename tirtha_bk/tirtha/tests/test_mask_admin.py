"""
Supplying masks through the admin (internal design notes).

The consuming half -- `RunAdmin.masks_available`, `trigger_gaussian_cut`, the
cull in `tirtha_recon.gaussian_cut` -- was built against a table with 19,623
images and 0 masks, because `ImageAdmin.fieldsets` never listed `mask` and the
contributor path had no client. These tests cover the two doors that now exist:
one mask on one image through the change form, and a folder of them on a site.

Both doors go through `uploads.prepare_mask`, so what they store is the
normalised greyscale PNG the pipeline expects, not whatever the editor exported.
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages import constants as message_levels
from django.contrib.messages import get_messages
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from PIL import Image as PILImage

from tirtha.admin import ImageAdmin
from tirtha.models import Contribution, Contributor, Image, Site

MEDIA = tempfile.mkdtemp(prefix="tirtha-mask-admin-")


def _jpeg(size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", size, (120, 90, 40)).save(buf, format="JPEG")
    return buf.getvalue()


def _png(size=(64, 64), mode="L") -> bytes:
    """
    An `RGBA` mask carries its shape in alpha over uniform white -- the editor
    export that reads as "everything is subject" if stored unnormalised.
    """
    if mode == "RGBA":
        img = PILImage.new("RGBA", size, (255, 255, 255, 0))
        img.paste((255, 255, 255, 255), (0, 0, size[0] // 2, size[1]))
    else:
        img = PILImage.new(mode, size, 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _Messages:
    """The admin writes user messages; a bare RequestFactory request has none."""

    def __init__(self):
        self.sent = []

    def add(self, level, message, extra_tags=""):
        self.sent.append((level, str(message)))


@override_settings(MEDIA_ROOT=MEDIA)
class _Base(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.site = Site.objects.create(
            name=f"Konark {self.id().rsplit('.', 1)[-1]}",
            country="India",
            state="Odisha",
            district="Puri",
        )
        self.contributor = Contributor.objects.create(
            name="T", email="t@example.org", active=True
        )
        self.contribution = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )
        self.superuser = get_user_model().objects.create_superuser(
            username=f"op-{self.id().rsplit('.', 1)[-1]}"[:150],
            email="op@example.org",
            password="x",
        )
        self.admin = ImageAdmin(Image, AdminSite())
        self.request = RequestFactory().post("/")
        self.request.user = self.superuser
        self.request._messages = _Messages()
        self.url = reverse("admin:tirtha_site_upload_masks", args=[self.site.ID])
        self.change_url = reverse("admin:tirtha_site_change", args=[self.site.ID])

    def _image(
        self, original="IMG_0117.jpeg", size=(64, 64), contribution=None
    ) -> Image:
        img = Image(
            contribution=contribution or self.contribution, original_filename=original
        )
        img.image.save(original, SimpleUploadedFile(original, _jpeg(size)), save=True)
        return img

    def _with_contributor_mask(self, img: Image) -> Image:
        img.mask.save(f"{img.ID}.png", ContentFile(_png()), save=False)
        img.mask_source = "contributor"
        img.save(update_fields=["mask", "mask_source"])
        return img


class TheChangeForm(_Base):
    """
    Driven through `ImageAdmin.get_form`, not `ImageAdminForm` directly, so the
    wiring is under test too: the form class the admin actually builds, with
    `mask` in a fieldset and `mask_source` read-only.
    """

    def _form(self, img, files=None, data=None):
        FormClass = self.admin.get_form(self.request, img, change=True)
        return FormClass(
            data={"safety_status": img.safety_status, **(data or {})},
            files=files or {},
            instance=img,
        )

    def _save(self, form) -> Image:
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        self.admin.save_model(self.request, obj, form, change=True)
        obj.refresh_from_db()
        return obj

    def test_mask_source_is_not_editable(self):
        # Provenance, not a setting: a typed value could disagree with the file.
        FormClass = self.admin.get_form(self.request, self._image(), change=True)
        self.assertNotIn("mask_source", FormClass.base_fields)
        self.assertIn("mask", FormClass.base_fields)

    def test_a_mask_of_the_wrong_size_is_rejected_with_the_gates_message(self):
        # The common mistake, and the one nothing downstream can detect: the cut
        # skips a mismatched mask silently. It has to fail here, on the field.
        img = self._image(size=(64, 64))
        form = self._form(
            img, files={"mask": SimpleUploadedFile("m.png", _png(size=(32, 32)))}
        )
        self.assertFalse(form.is_valid())
        message = " ".join(form.errors["mask"])
        self.assertIn("32x32", message)
        self.assertIn("64x64", message)
        self.assertIn("original size", message)

    def test_a_jpeg_is_rejected(self):
        img = self._image()
        form = self._form(
            img, files={"mask": SimpleUploadedFile("m.jpg", _jpeg(), "image/jpeg")}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("PNG", " ".join(form.errors["mask"]))

    def test_an_accepted_rgba_mask_is_stored_normalised_at_the_canonical_path(self):
        img = self._image()
        obj = self._save(
            self._form(
                img, files={"mask": SimpleUploadedFile("drawn.png", _png(mode="RGBA"))}
            )
        )

        self.assertEqual(obj.mask.name, f"models/{self.site.ID}/masks/{img.ID}.png")
        self.assertEqual(obj.mask_source, "admin")
        with PILImage.open(obj.mask.path) as stored:
            self.assertEqual(stored.mode, "L")
            self.assertEqual(stored.size, (64, 64))
            # The shape came from alpha, not from the uniformly white RGB.
            self.assertEqual(stored.getpixel((8, 8)), 255)
            self.assertEqual(stored.getpixel((56, 8)), 0)

    def test_saving_without_touching_mask_leaves_a_contributors_attribution_alone(self):
        img = self._with_contributor_mask(self._image())
        obj = self._save(self._form(img, data={"remark": "checked by hand"}))
        self.assertEqual(obj.remark, "checked by hand")
        self.assertEqual(obj.mask_source, "contributor")
        self.assertTrue(obj.mask)

    def test_ticking_clear_empties_both_and_removes_the_file(self):
        img = self._with_contributor_mask(self._image())
        path = img.mask.path
        obj = self._save(self._form(img, data={"mask-clear": "on"}))
        self.assertFalse(obj.mask)
        self.assertEqual(obj.mask_source, "")
        self.assertFalse(os.path.exists(path), "cleared mask left on disk")

    def test_replacing_a_mask_keeps_the_canonical_name(self):
        # Storage never overwrites, so without removing the old file first the
        # replacement lands at `<ID>_AbC123.png`.
        img = self._with_contributor_mask(self._image())
        obj = self._save(
            self._form(img, files={"mask": SimpleUploadedFile("again.png", _png())})
        )
        self.assertEqual(obj.mask.name, f"models/{self.site.ID}/masks/{img.ID}.png")
        self.assertEqual(obj.mask_source, "admin")


class TheChangelist(_Base):
    def test_clear_mask_empties_both_and_removes_the_file(self):
        img = self._with_contributor_mask(self._image())
        path = img.mask.path
        untouched = self._image("IMG_0118.jpeg")

        self.admin.clear_mask(self.request, Image.objects.filter(pk=img.pk))

        img.refresh_from_db()
        self.assertFalse(img.mask)
        self.assertEqual(img.mask_source, "")
        self.assertFalse(os.path.exists(path))
        self.assertTrue(
            any("1 mask was removed" in m for _, m in self.request._messages.sent)
        )
        untouched.refresh_from_db()
        self.assertFalse(untouched.mask)

    def test_the_filter_partitions_masked_from_unmasked(self):
        masked = self._with_contributor_mask(self._image("A.jpeg"))
        bare = self._image("B.jpeg")

        def rows(**params):
            request = RequestFactory().get("/", params)
            request.user = self.superuser
            return set(self.admin.get_changelist_instance(request).queryset)

        self.assertEqual(rows(has_mask="yes"), {masked})
        self.assertEqual(rows(has_mask="no"), {bare})
        self.assertEqual(rows(), {masked, bare})
        self.assertTrue(self.admin.has_mask(masked))
        self.assertFalse(self.admin.has_mask(bare))


class TheBulkView(_Base):
    def test_it_renders_for_a_superuser(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        # The rules the operator needs before choosing files are on the page.
        self.assertContains(response, "IMG_0117.jpeg.png")
        self.assertContains(response, 'name="masks"')

    def test_it_attaches_what_matches_and_warns_about_what_does_not(self):
        img = self._image("IMG_0117.jpeg")
        self.client.force_login(self.superuser)

        response = self.client.post(
            self.url,
            {
                "masks": [
                    SimpleUploadedFile("IMG_0117.png", _png(), "image/png"),
                    SimpleUploadedFile("NOPE.png", _png(), "image/png"),
                ]
            },
        )

        self.assertRedirects(response, self.change_url, fetch_redirect_response=False)
        img.refresh_from_db()
        self.assertTrue(img.mask)
        self.assertEqual(img.mask_source, "admin")

        sent = list(get_messages(response.wsgi_request))
        levels = {m.level for m in sent}
        self.assertIn(message_levels.SUCCESS, levels)
        self.assertIn(message_levels.WARNING, levels)
        warning = next(m for m in sent if m.level == message_levels.WARNING)
        self.assertIn("NOPE.png", str(warning))

    def test_anonymous_users_are_sent_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response["Location"])

    def test_it_reaches_an_image_a_different_contributor_uploaded(self):
        """
        The `scope=None` property. A contributor may only mask their own
        images; an operator curating the site may mask any of them.
        """
        other = Contributor.objects.create(
            name="Someone else", email="else@example.org", active=True
        )
        theirs = Contribution.objects.create(site=self.site, contributor=other)
        img = self._image("DSC_0001.jpeg", contribution=theirs)
        self.client.force_login(self.superuser)

        self.client.post(
            self.url,
            {"masks": [SimpleUploadedFile("DSC_0001.png", _png(), "image/png")]},
        )

        img.refresh_from_db()
        self.assertTrue(img.mask)
        self.assertEqual(img.mask_source, "admin")

    def test_the_link_from_the_site_page_points_here(self):
        from tirtha.admin import SiteAdmin

        html = str(SiteAdmin(Site, AdminSite()).upload_masks_link(self.site))
        self.assertIn(self.url, html)


class TheAdminPagesStillRender(_Base):
    """
    The wiring, end to end: a fieldset naming a field the form does not have,
    or a readonly display method that queries on an unsaved row, is a 500 on
    a page an operator opens every day -- and nothing above would notice.
    """

    def test_the_image_change_page_shows_the_mask_fieldset(self):
        img = self._with_contributor_mask(self._image())
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:tirtha_image_change", args=[img.ID]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Segmentation mask")
        self.assertContains(response, "Contributor-supplied")
        self.assertContains(response, img.mask.url)

    def test_the_site_add_page_renders(self):
        # `Site.ID` is a UUID default, so the unsaved row on this page has a pk;
        # `upload_masks_link` must not take that as a licence to query.
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:tirtha_site_add"))
        self.assertEqual(response.status_code, 200)

    def test_the_site_change_page_carries_the_link(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.change_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.url)
