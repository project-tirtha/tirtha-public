"""
Tests for the export_dataset command -- the bridge between the database and the
Django-free reconstruction layer (internal design notes).

What matters here is the ordering contract: each contribution occupies a
contiguous block in arrival order, ordered internally by upload time, never
sorted globally by timestamp (internal design notes).
"""

import tempfile
from io import StringIO
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from tirtha.models import Contribution, Contributor, Image, Site

_MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=_MEDIA)
class ExportDatasetTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="Mukteswar Temple", country="India", state="Odisha", district="Khordha"
        )
        self.contributor = Contributor.objects.create(
            name="Test", email="t@example.org"
        )

    def _contribution(self):
        return Contribution.objects.create(site=self.site, contributor=self.contributor)

    def _image(self, contribution, name="img.jpg", **kw):
        return Image.objects.create(
            contribution=contribution,
            image=SimpleUploadedFile(name, b"x", content_type="image/jpeg"),
            **kw,
        )

    def _run(self, **kwargs):
        out = StringIO()
        call_command("export_dataset", stdout=out, stderr=StringIO(), **kwargs)
        return out.getvalue()

    def test_links_are_ordered_and_zero_padded(self):
        contribution = self._contribution()
        for i in range(3):
            self._image(contribution, f"a{i}.jpg")
        with tempfile.TemporaryDirectory() as work:
            self._run(site=str(self.site.ID), out=work)
            names = sorted(p.name for p in (Path(work) / "images").iterdir())
        self.assertEqual(len(names), 3)
        self.assertTrue(all(n[:8].isdigit() for n in names))
        self.assertEqual([n[:8] for n in names], ["00000001", "00000002", "00000003"])

    def test_contributions_occupy_contiguous_blocks_in_arrival_order(self):
        first, second = self._contribution(), self._contribution()
        a = [self._image(first, f"a{i}.jpg") for i in range(2)]
        b = [self._image(second, f"b{i}.jpg") for i in range(2)]
        with tempfile.TemporaryDirectory() as work:
            self._run(site=str(self.site.ID), out=work)
            names = sorted(p.name for p in (Path(work) / "images").iterdir())
        ids = [n.split("_", 1)[1].rsplit(".", 1)[0] for n in names]
        self.assertEqual(ids[:2], [str(i.ID) for i in a])
        self.assertEqual(ids[2:], [str(i.ID) for i in b])

    def test_rejected_images_excluded_by_default(self):
        contribution = self._contribution()
        self._image(contribution, "good.jpg")
        self._image(contribution, "bad.jpg", admin_verdict="reject")
        self._image(contribution, "nsfw.jpg", safety_status="rejected")
        with tempfile.TemporaryDirectory() as work:
            self._run(site=str(self.site.ID), out=work)
            self.assertEqual(len(list((Path(work) / "images").iterdir())), 1)

    def test_include_rejected_opt_in(self):
        contribution = self._contribution()
        self._image(contribution, "good.jpg")
        self._image(contribution, "bad.jpg", admin_verdict="reject")
        with tempfile.TemporaryDirectory() as work:
            self._run(site=str(self.site.ID), out=work, include_rejected=True)
            self.assertEqual(len(list((Path(work) / "images").iterdir())), 2)

    def test_colmap_dir_is_created_alongside_images(self):
        self._image(self._contribution())
        with tempfile.TemporaryDirectory() as work:
            self._run(site=str(self.site.ID), out=work)
            self.assertTrue((Path(work) / "colmap").is_dir())

    def test_accepts_verbose_id_as_well_as_pk(self):
        self._image(self._contribution())
        with tempfile.TemporaryDirectory() as work:
            output = self._run(site=self.site.verbose_id, out=work)
        self.assertIn(self.site.verbose_id, output)

    def test_dry_run_touches_nothing(self):
        self._image(self._contribution())
        with tempfile.TemporaryDirectory() as work:
            self._run(site=str(self.site.ID), out=work, dry_run=True)
            self.assertEqual(list(Path(work).iterdir()), [])

    def test_unknown_site_is_an_error(self):
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaises(CommandError):
                self._run(site="nosuchsite", out=work)

    def test_site_with_no_images_is_an_error(self):
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaises(CommandError):
                self._run(site=str(self.site.ID), out=work)
