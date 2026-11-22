"""
Tests for the durable per-site COLMAP workspace (internal design notes).

The properties here are the ones that make continual reconstruction possible
and that protect the media store. COLMAP records *relative* image paths, so a
link name must never change once used; the archive must never be renamed,
because `django_cleanup` deletes the old file when a FileField changes; and
withheld or unchecked images must not reach a reconstruction.
"""

import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from tirtha.models import Contribution, Contributor, Image, Site
from tirtha.workspace import Workspace, image_refs, materialise_site


class _Base(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.media = self.root / "media"
        self.ws = self.root / "ws"
        self.media.mkdir()
        self.addCleanup(self._tmp.cleanup)

        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.contributor = Contributor.objects.create(name="T", email="t@example.org")
        self.contribution = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )

    def _image(self, name, *, seq=None, status="safe", write=True):
        img = Image.objects.create(
            contribution=self.contribution,
            image=SimpleUploadedFile(name, b"x", content_type="image/jpeg"),
            seq=seq,
            safety_status=status,
        )
        if write:
            target = self.media / img.image.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"jpeg")
        return img

    def _materialise(self):
        with override_settings(
            MEDIA_ROOT=str(self.media), COLMAP_WORKSPACE_DIR=str(self.ws)
        ):
            return materialise_site(self.site)


class SelectionTests(_Base):
    def test_safe_images_are_materialised(self):
        self._image("a.jpg", seq=0)
        self._image("b.jpg", seq=1)
        workspace, result = self._materialise()
        self.assertEqual(result.linked, 2)
        self.assertEqual(len(list(workspace.images.iterdir())), 2)

    def test_withheld_images_never_reach_a_reconstruction(self):
        self._image("ok.jpg", seq=0)
        self._image("bad.jpg", seq=1, status="rejected")
        workspace, _ = self._materialise()
        self.assertEqual(len(list(workspace.images.iterdir())), 1)

    def test_unchecked_images_are_excluded(self):
        # An unchecked image is not publishable, so it must not end up in a
        # published model (internal design notes).
        self._image("pending.jpg", seq=0, status="pending")
        workspace, _ = self._materialise()
        self.assertEqual(len(list(workspace.images.iterdir())), 0)


class LinkNameTests(_Base):
    def test_link_names_are_seq_ordered(self):
        self._image("b.jpg", seq=1)
        self._image("a.jpg", seq=0)
        workspace, _ = self._materialise()
        names = sorted(p.name for p in workspace.images.iterdir())
        self.assertTrue(names[0].startswith("00000000_"))
        self.assertTrue(names[1].startswith("00000001_"))

    def test_sorted_link_order_equals_seq_order(self):
        # This is the property sequential_matcher depends on: COLMAP assigns
        # image_ids in sorted filename order.
        for i in range(12):
            self._image(f"{i}.jpg", seq=i)
        workspace, _ = self._materialise()
        seqs = [int(p.name.split("_")[0]) for p in sorted(workspace.images.iterdir())]
        self.assertEqual(seqs, list(range(12)))

    def test_a_link_name_is_stable_across_runs(self):
        # COLMAP stores relative paths; a name that changed between runs would
        # invalidate the database (internal design notes).
        img = self._image("a.jpg", seq=0)
        workspace, _ = self._materialise()
        first = {p.name for p in workspace.images.iterdir()}
        img.sharpness_score = 5.0
        img.save()
        workspace, _ = self._materialise()
        self.assertEqual({p.name for p in workspace.images.iterdir()}, first)

    def test_an_image_without_seq_still_gets_an_ordered_name(self):
        # Backfilled by 0020, but older ingest code could produce one; dropping
        # the whole run over it would be worse than appending it.
        self._image("a.jpg", seq=None)
        workspace, _ = self._materialise()
        self.assertEqual(len(list(workspace.images.iterdir())), 1)


class DurabilityTests(_Base):
    def test_a_second_run_adds_links_without_relinking(self):
        self._image("a.jpg", seq=0)
        self._materialise()
        self._image("b.jpg", seq=1)
        _, result = self._materialise()
        self.assertEqual(result.linked, 1)
        self.assertEqual(result.already_present, 1)

    def test_removing_an_image_does_not_remove_its_link(self):
        # COLMAP's database still references it; pruning here would break the
        # reconstruction rather than tidy it.
        img = self._image("a.jpg", seq=0)
        self._image("b.jpg", seq=1)
        workspace, _ = self._materialise()
        before = len(list(workspace.images.iterdir()))
        img.delete()
        workspace, _ = self._materialise()
        self.assertEqual(len(list(workspace.images.iterdir())), before)

    def test_the_workspace_is_outside_the_media_store(self):
        # The media store is canonical and never touched -- that is what makes
        # this design safe against django_cleanup.
        with override_settings(
            MEDIA_ROOT=str(self.media), COLMAP_WORKSPACE_DIR=str(self.ws)
        ):
            workspace = Workspace.for_site(self.site.ID)
        self.assertNotIn(str(self.media), str(workspace.root))

    def test_the_media_store_is_never_modified(self):
        self._image("a.jpg", seq=0)
        before = {p.name for p in (self.media).rglob("*") if p.is_file()}
        self._materialise()
        after = {p.name for p in (self.media).rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_links_point_back_into_the_media_store(self):
        self._image("a.jpg", seq=0)
        workspace, _ = self._materialise()
        link = next(iter(workspace.images.iterdir()))
        self.assertTrue(link.is_symlink())
        self.assertIn(str(self.media), str(link.resolve()))


class MissingSourceTests(_Base):
    def test_a_missing_source_is_reported_not_fatal(self):
        # One missing file out of hundreds should not lose the reconstruction.
        self._image("ok.jpg", seq=0)
        self._image("gone.jpg", seq=1, write=False)
        workspace, result = self._materialise()
        self.assertEqual(len(result.missing_sources), 1)
        self.assertEqual(len(list(workspace.images.iterdir())), 1)


class RefOrderingTests(_Base):
    def test_refs_come_back_in_seq_order(self):
        self._image("c.jpg", seq=2)
        self._image("a.jpg", seq=0)
        self._image("b.jpg", seq=1)
        with override_settings(MEDIA_ROOT=str(self.media)):
            refs = image_refs(self.site)
        self.assertEqual([r.seq for r in refs], [0, 1, 2])


class SeqBackfillTests(TestCase):
    """
    Migration 0020 (internal design notes).

    Ordered *within* a contribution, never globally by timestamp: EXIF clocks
    are not comparable across contributors -- drift, timezones, and cameras
    whose clock was never set.
    """

    def setUp(self):
        import importlib

        self.mig = importlib.import_module("tirtha.migrations.0020_backfill_image_seq")
        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.contributor = Contributor.objects.create(name="T", email="t@example.org")

    class _Apps:
        @staticmethod
        def get_model(app_label, model_name):
            return {
                "Site": Site,
                "Contribution": Contribution,
                "Image": Image,
            }[model_name]

    def _contribution(self):
        return Contribution.objects.create(site=self.site, contributor=self.contributor)

    def _image(self, contribution, name, date_taken=None):
        return Image.objects.create(
            contribution=contribution,
            image=SimpleUploadedFile(name, b"x", content_type="image/jpeg"),
            date_taken=date_taken,
        )

    def test_seq_is_assigned_from_zero_per_site(self):
        c = self._contribution()
        self._image(c, "a.jpg")
        self._image(c, "b.jpg")
        self.mig.backfill_seq(self._Apps, None)
        self.assertEqual(sorted(Image.objects.values_list("seq", flat=True)), [0, 1])

    def test_capture_time_orders_within_a_contribution(self):
        from django.utils import timezone

        c = self._contribution()
        now = timezone.now()
        late = self._image(c, "late.jpg", date_taken=now)
        early = self._image(
            c, "early.jpg", date_taken=now - timezone.timedelta(hours=2)
        )
        self.mig.backfill_seq(self._Apps, None)
        early.refresh_from_db()
        late.refresh_from_db()
        self.assertLess(early.seq, late.seq)

    def test_contributions_are_blocked_in_arrival_order(self):
        # Cross-contribution ordering is by arrival, never by EXIF: two
        # contributors' clocks cannot be compared.
        from django.utils import timezone

        first = self._contribution()
        second = self._contribution()
        # The later contribution carries an *earlier* camera timestamp.
        a = self._image(first, "a.jpg", date_taken=timezone.now())
        b = self._image(
            second, "b.jpg", date_taken=timezone.now() - timezone.timedelta(days=365)
        )
        self.mig.backfill_seq(self._Apps, None)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertLess(a.seq, b.seq)

    def test_seq_is_contiguous_across_contributions(self):
        for _ in range(3):
            c = self._contribution()
            for i in range(2):
                self._image(c, f"{id(c)}-{i}.jpg")
        self.mig.backfill_seq(self._Apps, None)
        self.assertEqual(
            sorted(Image.objects.values_list("seq", flat=True)), list(range(6))
        )

    def test_running_twice_is_stable(self):
        c = self._contribution()
        self._image(c, "a.jpg")
        self._image(c, "b.jpg")
        self.mig.backfill_seq(self._Apps, None)
        first = list(Image.objects.order_by("ID").values_list("seq", flat=True))
        self.mig.backfill_seq(self._Apps, None)
        self.assertEqual(
            list(Image.objects.order_by("ID").values_list("seq", flat=True)), first
        )

    def test_no_files_are_touched(self):
        # A column update only -- the entire reason for the symlink design.
        c = self._contribution()
        img = self._image(c, "a.jpg")
        before = img.image.name
        self.mig.backfill_seq(self._Apps, None)
        img.refresh_from_db()
        self.assertEqual(img.image.name, before)


class MaskMaterialisationTests(_Base):
    """
    Masks in the workspace (internal design notes).

    COLMAP looks for `<image filename>.png` inside the mask directory. The link
    name therefore has to mirror the *image link* name, extension included --
    get it wrong and COLMAP silently ignores every mask, which is
    indistinguishable from masking not helping.
    """

    def _with_mask(self, name="a.jpg", seq=0):
        img = self._image(name, seq=seq)
        img.mask.save(
            f"{img.ID}.png",
            SimpleUploadedFile(f"{img.ID}.png", b"png", content_type="image/png"),
            save=True,
        )
        target = self.media / img.mask.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"pngdata")
        return img

    def test_a_mask_is_linked_under_the_name_colmap_expects(self):
        img = self._with_mask("a.jpg", seq=7)
        workspace, _ = self._materialise()
        names = [p.name for p in workspace.masks.iterdir()]
        self.assertEqual(names, [f"00000007_{img.ID}.jpg.png"])

    def test_the_mask_name_mirrors_the_image_link_name(self):
        self._with_mask("a.jpg", seq=3)
        workspace, _ = self._materialise()
        image_link = next(iter(workspace.images.iterdir()))
        mask_link = next(iter(workspace.masks.iterdir()))
        self.assertEqual(mask_link.name, f"{image_link.name}.png")

    def test_images_without_masks_produce_no_link(self):
        # Partial coverage is normal; COLMAP ignores images with no mask.
        self._image("a.jpg", seq=0)
        self._with_mask("b.jpg", seq=1)
        workspace, _ = self._materialise()
        self.assertEqual(len(list(workspace.masks.iterdir())), 1)

    def test_no_masks_at_all_leaves_an_empty_directory(self):
        self._image("a.jpg", seq=0)
        workspace, _ = self._materialise()
        self.assertEqual(list(workspace.masks.iterdir()), [])

    def test_masks_are_not_copied_into_the_workspace(self):
        # Same reasoning as images: the media store is canonical.
        self._with_mask("a.jpg", seq=0)
        workspace, _ = self._materialise()
        self.assertTrue(next(iter(workspace.masks.iterdir())).is_symlink())


class ColmapMaskingTests(_Base):
    def test_colmap_masking_is_off_by_default(self):
        # FRGS consumes masks in its training loop regardless; whether COLMAP
        # masking additionally helps SfM is unmeasured (internal design notes).
        from django.conf import settings

        self.assertFalse(getattr(settings, "COLMAP_USE_MASKS", False))
