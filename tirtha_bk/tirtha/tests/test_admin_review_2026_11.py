"""
Admin fixes from review 2026-11-22 §5/§7 and review 2026-09-04 §2.7/§4.

* Changelists cost a constant number of queries, whatever the page holds.
* The image list does not pull 200 full-resolution originals eagerly.
* ZIP downloads are one streamed, uncompressed implementation.
* Every action declares the permission it needs, and the destructive or
  publishing ones need more than "staff who may edit the model".
"""

from __future__ import annotations

import io
import os
import tempfile
import zipfile
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tirtha import admin as tadmin
from tirtha.models import ARK, Contribution, Contributor, Image, Run, Site

_counter = iter(range(10**6))


def _populate(n: int):
    """`n` of everything a changelist shows, each with related rows to walk."""
    for _ in range(n):
        i = next(_counter)
        site = Site.objects.create(
            name=f"Temple {i}", country="India", state="Odisha", district="Puri"
        )
        contributor = Contributor.objects.create(
            name=f"P{i}", email=f"p{i}@example.org"
        )
        contribution = Contribution.objects.create(site=site, contributor=contributor)
        images = [
            Image.objects.create(contribution=contribution, image=f"img/{i}-{k}.jpg")
            for k in range(2)
        ]
        ark = ARK.objects.create(
            ark=f"99999/t{i}",
            naan="99999",
            shoulder="/",
            assigned_name=f"t{i}",
            url=f"https://example.org/static/models/{site.ID}/published/x.splat",
            metadata={},
        )
        run = Run.objects.create(site=site, kind="GS", status="Archived", ark=ark)
        run.images.add(*images)
        run.contributors.add(contributor)


def _superuser():
    return get_user_model().objects.create_superuser("root", "root@example.org", "pw")


def _staff(*perms):
    user = get_user_model().objects.create_user(
        "staff", "staff@example.org", "pw", is_staff=True
    )
    for codename in perms:
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label="tirtha", codename=codename)
        )
    return get_user_model().objects.get(pk=user.pk)  # drop the perm cache


class ChangelistsAreConstant(TestCase):
    """Doubling the rows on a page must not change how many queries it costs."""

    MODELS = ("site", "contributor", "contribution", "image", "run", "ark")

    def setUp(self):
        self.client.force_login(_superuser())

    def _count(self, model):
        url = reverse(f"admin:tirtha_{model}_changelist")
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200, model)
        return len(ctx)

    def test_every_changelist_is_constant_in_its_rows(self):
        _populate(2)
        few = {m: self._count(m) for m in self.MODELS}
        _populate(5)
        many = {m: self._count(m) for m in self.MODELS}
        for model in self.MODELS:
            with self.subTest(model=model):
                self.assertEqual(
                    few[model], many[model], f"{model} changelist is N+1 again"
                )

    def test_the_counts_are_right(self):
        _populate(1)
        response = self.client.get(reverse("admin:tirtha_site_changelist"))
        cl = response.context["cl"]
        site = cl.result_list[0]
        self.assertEqual((site._contrib_count, site._image_count), (1, 2))

        response = self.client.get(reverse("admin:tirtha_contribution_changelist"))
        contribution = response.context["cl"].result_list[0]
        self.assertEqual((contribution._image_count, contribution._good_count), (2, 2))

        response = self.client.get(reverse("admin:tirtha_run_changelist"))
        self.assertEqual(response.context["cl"].result_list[0]._image_count, 2)

        response = self.client.get(reverse("admin:tirtha_ark_changelist"))
        self.assertEqual(response.context["cl"].result_list[0]._image_count, 2)

    def test_a_rejected_image_is_not_counted_good(self):
        _populate(1)
        Image.objects.update(admin_verdict="reject")
        response = self.client.get(reverse("admin:tirtha_contribution_changelist"))
        self.assertEqual(response.context["cl"].result_list[0]._good_count, 0)


class TheImageListIsLight(TestCase):
    def test_fifty_per_page(self):
        self.assertEqual(tadmin.ImageAdmin.list_per_page, 50)

    def test_thumbnails_are_lazy_and_sized(self):
        _populate(1)
        client = self.client
        client.force_login(_superuser())
        body = client.get(reverse("admin:tirtha_image_changelist")).content.decode()
        self.assertIn('loading="lazy"', body)
        self.assertIn('decoding="async"', body)
        self.assertIn('width="96" height="96"', body)
        self.assertNotIn("width: 400px", body)

    def test_the_contribution_inline_is_lazy_too(self):
        _populate(1)
        self.client.force_login(_superuser())
        contribution = Contribution.objects.first()
        body = self.client.get(
            reverse("admin:tirtha_contribution_change", args=[contribution.ID])
        ).content.decode()
        self.assertIn('loading="lazy"', body)
        self.assertNotIn("width: 400px", body)

    def test_a_site_list_prefers_the_small_derivative(self):
        site = Site(ID="abc", name="x", verbose_id="x")
        site.thumbnail.name = "models/abc/abc_thumb.png"
        derivative = tadmin.derivative_path(site.thumbnail.name, "preview")
        with patch.object(default_storage, "exists", lambda name: name == derivative):
            self.assertTrue(tadmin._site_thumb_url(site).endswith("__preview.webp"))
        with patch.object(default_storage, "exists", lambda name: False):
            self.assertTrue(tadmin._site_thumb_url(site).endswith("abc_thumb.png"))

    def test_a_site_without_a_thumbnail_does_not_crash_the_list(self):
        Site.objects.create(name="Bare", country="India", state="Odisha")
        self.client.force_login(_superuser())
        response = self.client.get(reverse("admin:tirtha_site_changelist"))
        self.assertEqual(response.status_code, 200)


class OneStreamedZip(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _file(self, name, data):
        path = os.path.join(self._tmp.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def _unzip(self, response):
        self.assertTrue(response.streaming)
        return zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))

    def test_members_are_stored_not_deflated(self):
        a = self._file("a.jpg", b"\xff\xd8" + os.urandom(3000))
        b = self._file("b.jpg", b"a" * 5000)
        zf = self._unzip(
            tadmin.zip_response([(a, "x/a.jpg"), (b, "y/b.jpg")], "t.zip", "t")
        )
        self.assertIsNone(zf.testzip())
        self.assertEqual(sorted(zf.namelist()), ["x/a.jpg", "y/b.jpg"])
        for info in zf.infolist():
            self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
        self.assertEqual(zf.read("y/b.jpg"), b"a" * 5000)

    def test_a_vanished_file_is_skipped_not_fatal(self):
        a = self._file("a.jpg", b"data")
        zf = self._unzip(
            tadmin.zip_response(
                [(os.path.join(self._tmp.name, "gone.jpg"), "gone.jpg"), (a, "a.jpg")],
                "t.zip",
                "t",
            )
        )
        self.assertEqual(zf.namelist(), ["a.jpg"])

    def test_a_large_member_is_sent_in_chunks(self):
        big = self._file("big.bin", os.urandom(tadmin.ZIP_CHUNK * 2 + 10))
        chunks = list(tadmin._iter_zip([(big, "big.bin")], "t"))
        self.assertGreater(len(chunks), 2)
        self.assertLessEqual(max(len(c) for c in chunks), tadmin.ZIP_CHUNK + 4096)

    def test_the_contribution_download_contains_its_images(self):
        site = Site.objects.create(name="Konark", country="India", state="Odisha")
        contributor = Contributor.objects.create(name="P", email="p@example.org")
        contribution = Contribution.objects.create(site=site, contributor=contributor)
        name = default_storage.save(
            "images/zip-test/one.jpg", ContentFile(b"jpegbytes")
        )
        self.addCleanup(default_storage.delete, name)
        Image.objects.create(contribution=contribution, image=name)

        self.client.force_login(_superuser())
        response = self.client.get(
            reverse("admin:tirtha_contribution_download_images", args=[contribution.ID])
        )
        self.assertEqual(response.status_code, 200)
        zf = self._unzip(response)
        (member,) = zf.namelist()
        self.assertTrue(member.startswith(f"{contribution.ID}/unchecked/"))
        self.assertEqual(zf.read(member), b"jpegbytes")


class ActionsDeclarePermissions(TestCase):
    """Every action says what it needs; Django then hides it from those without."""

    def test_every_registered_action_declares_permissions(self):
        from django.contrib import admin

        for model, model_admin in admin.site._registry.items():
            if model._meta.app_label != "tirtha":
                continue
            for action in model_admin.actions or ():
                func = (
                    getattr(model_admin, action) if isinstance(action, str) else action
                )
                with self.subTest(
                    admin=type(model_admin).__name__, action=func.__name__
                ):
                    self.assertTrue(
                        getattr(func, "allowed_permissions", None),
                        "an admin action with no permissions= runs for any staff",
                    )

    def _actions(self, admin_cls, model, user):
        request = RequestFactory().get("/")
        request.user = user
        return set(admin_cls(model, AdminSite()).get_actions(request))

    def test_view_only_staff_gets_only_downloads(self):
        staff = _staff("view_contribution", "view_site", "view_run", "view_image")
        self.assertEqual(
            self._actions(tadmin.ContributionAdmin, Contribution, staff),
            {"download_images_zip"},
        )
        self.assertEqual(
            self._actions(tadmin.SiteAdmin, Site, staff), {"download_images_zip_meshes"}
        )
        self.assertEqual(self._actions(tadmin.ImageAdmin, Image, staff), set())

    def test_change_alone_does_not_reach_the_sensitive_verbs(self):
        cases = [
            (tadmin.SiteAdmin, Site, "change_site", {"mark_hidden", "mark_not_hidden"}),
            (
                tadmin.ContributorAdmin,
                Contributor,
                "change_contributor",
                {"ban_contributors", "unban_contributors"},
            ),
            (
                tadmin.ContributionAdmin,
                Contribution,
                "change_contribution",
                {"trigger_imageops", "trigger_aVOps", "trigger_GSOps"},
            ),
            (tadmin.ImageAdmin, Image, "change_image", {"mark_nsfw", "mark_safe"}),
            (tadmin.RunAdmin, Run, "change_run", {"trigger_gaussian_cut"}),
        ]
        for admin_cls, model, perm, sensitive in cases:
            get_user_model().objects.filter(username="staff").delete()
            staff = _staff(perm)
            with self.subTest(admin=admin_cls.__name__):
                offered = self._actions(admin_cls, model, staff)
                self.assertFalse(offered & sensitive, offered & sensitive)
                self.assertTrue(
                    offered, "change permission should still allow the edits"
                )

    def test_a_superuser_gets_all_of_them(self):
        root = _superuser()
        self.assertIn(
            "ban_contributors",
            self._actions(tadmin.ContributorAdmin, Contributor, root),
        )
        self.assertIn(
            "trigger_GSOps", self._actions(tadmin.ContributionAdmin, Contribution, root)
        )
        self.assertIn("mark_safe", self._actions(tadmin.ImageAdmin, Image, root))
        self.assertIn("mark_hidden", self._actions(tadmin.SiteAdmin, Site, root))
        self.assertIn("trigger_gaussian_cut", self._actions(tadmin.RunAdmin, Run, root))

    def test_a_granted_custom_permission_is_honoured(self):
        """Once the codename exists, granting it is all it takes."""
        staff = _staff("change_contributor")
        request = RequestFactory().get("/")
        request.user = staff
        model_admin = tadmin.ContributorAdmin(Contributor, AdminSite())
        self.assertFalse(model_admin.has_moderate_permission(request))
        with patch.object(type(staff), "has_perm", lambda self, perm, obj=None: True):
            self.assertTrue(model_admin.has_moderate_permission(request))

    def test_posting_a_refused_action_does_nothing(self):
        _populate(1)
        self.client.force_login(_staff("view_contribution", "change_contribution"))
        contribution = Contribution.objects.first()
        with patch("tirtha.admin.recon_runner_task.delay") as delay:
            self.client.post(
                reverse("admin:tirtha_contribution_changelist"),
                {"action": "trigger_GSOps", "_selected_action": [str(contribution.ID)]},
            )
        delay.assert_not_called()

    def test_the_imageops_label_no_longer_promises_reconstructions(self):
        description = tadmin.ContributionAdmin.trigger_imageops.short_description
        self.assertNotIn("reconstruction", description.lower())


class CustomViewsCheckPermissions(TestCase):
    def setUp(self):
        _populate(1)
        self.run = Run.objects.first()
        self.site = self.run.site

    def test_replace_output_refuses_staff_without_publish(self):
        self.client.force_login(_staff("view_run", "change_run"))
        response = self.client.get(
            reverse("admin:tirtha_run_replace_output", args=[self.run.ID])
        )
        self.assertEqual(response.status_code, 403)

    def test_replace_output_lets_a_superuser_past_the_check(self):
        self.client.force_login(_superuser())
        response = self.client.get(
            reverse("admin:tirtha_run_replace_output", args=[self.run.ID])
        )
        # Past the permission check: there is no published file in this
        # fixture, so it answers 404 rather than 403.
        self.assertEqual(response.status_code, 404)

    def test_downloads_refuse_staff_without_view(self):
        self.client.force_login(_staff("view_contributor"))
        contribution = Contribution.objects.first()
        for url in (
            reverse("admin:tirtha_mesh_download_images", args=[self.site.ID]),
            reverse(
                "admin:tirtha_contribution_download_images", args=[contribution.ID]
            ),
            reverse("admin:tirtha_run_download_output", args=[self.run.ID]),
            reverse("admin:tirtha_run_download_full", args=[self.run.ID]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_hiding_a_site_moves_updated_at(self):
        """`update()` skips auto_now; the API cache is keyed on it."""
        before = Site.objects.get(pk=self.site.pk).updated_at
        request = RequestFactory().post("/")
        request.user = _superuser()
        request._messages = type("M", (), {"add": lambda *a, **k: None})()
        tadmin.SiteAdmin(Site, AdminSite()).mark_hidden(
            request, Site.objects.filter(pk=self.site.pk)
        )
        self.assertGreater(Site.objects.get(pk=self.site.pk).updated_at, before)

    def test_the_change_form_does_not_swallow_the_refusal(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(_staff("view_run", "change_run"))
        response = self.client.post(
            reverse("admin:tirtha_run_change", args=[self.run.ID]),
            {"replacement": SimpleUploadedFile("x.splat", b"x")},
        )
        self.assertEqual(response.status_code, 403)
