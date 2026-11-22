"""
Tests for the rewritten ingest checks (internal design notes).

Two properties carry the weight. Quality is recorded as *measurements* with no
verdict attached, so that moving a threshold is a re-query rather than a
relabelling of every row (2.8). And safety is never allowed to default open: an
image whose check could not run stays unpublished.

Nothing here loads a model. The safety checker is injected, which is also how a
future swap to Falconsai (18.2) would land.
"""

import numpy as np
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from tirtha.imageops import (
    ImageOps,
    NudeNetChecker,
    SafetyUnavailable,
    measure_quality,
)
from tirtha.models import Contribution, Contributor, Image, Site

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


class _FakeChecker:
    """Returns a fixed score, or raises, without loading anything."""

    def __init__(self, score=0.0, raises=None):
        self._score = score
        self._raises = raises
        self.calls = []

    def score(self, path):
        self.calls.append(str(path))
        if self._raises:
            raise self._raises
        return self._score


def _write_image(path, *, kind="sharp"):
    """A real JPEG on disk -- the measurements read pixels, so mocks won't do."""
    rng = np.random.default_rng(0)
    if kind == "sharp":
        img = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
    elif kind == "flat":
        img = np.full((64, 64, 3), 128, dtype=np.uint8)
    elif kind == "blown":
        img = np.full((64, 64, 3), 255, dtype=np.uint8)
    elif kind == "black":
        img = np.zeros((64, 64, 3), dtype=np.uint8)
    cv2.imwrite(str(path), img)
    return path


class QualityMeasurementTests(TestCase):
    def test_noise_measures_sharper_than_a_flat_field(self):
        a = _write_image(self.tmp / "sharp.jpg", kind="sharp")
        b = _write_image(self.tmp / "flat.jpg", kind="flat")
        self.assertGreater(measure_quality(a).sharpness, measure_quality(b).sharpness)

    def test_a_blown_out_image_scores_badly_on_exposure(self):
        blown = measure_quality(_write_image(self.tmp / "blown.jpg", kind="blown"))
        self.assertGreater(blown.clipped_highlights, 0.9)
        self.assertLess(blown.exposure, 0.1)

    def test_a_black_image_fails_exposure_the_same_way(self):
        # Correct: neither a blown sky nor a black interior contributes
        # features, so they should not be scored differently.
        black = measure_quality(_write_image(self.tmp / "black.jpg", kind="black"))
        self.assertGreater(black.clipped_shadows, 0.9)
        self.assertLess(black.exposure, 0.1)

    def test_a_well_exposed_image_scores_well(self):
        ok = measure_quality(_write_image(self.tmp / "flat.jpg", kind="flat"))
        self.assertGreater(ok.exposure, 0.9)

    def test_an_unreadable_file_raises_rather_than_returning_zeros(self):
        bad = self.tmp / "notanimage.jpg"
        bad.write_bytes(b"not a jpeg")
        with self.assertRaises(ValueError):
            measure_quality(bad)

    def test_the_remark_records_the_numbers(self):
        remark = measure_quality(_write_image(self.tmp / "s.jpg")).as_remark()
        self.assertIn("sharpness(tenengrad)", remark)
        self.assertIn("mean_luma", remark)

    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)


class _OpsBase(TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmpdir = tempfile.TemporaryDirectory()
        self.media = Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)

        self.site = Site.objects.create(
            name="Test Site", country="India", state="Odisha", district="Khordha"
        )
        self.contributor = Contributor.objects.create(name="T", email="t@example.org")
        self.contribution = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )

    def _image(self, name="a.jpg", kind="sharp"):
        img = Image.objects.create(
            contribution=self.contribution,
            image=SimpleUploadedFile(name, b"x", content_type="image/jpeg"),
        )
        target = self.media / img.image.name
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_image(target, kind=kind)
        return img


class SafetyTests(_OpsBase):
    def test_a_clean_image_is_marked_safe_and_measured(self):
        img = self._image()
        with override_settings(MEDIA_ROOT=str(self.media)):
            ops = ImageOps(str(self.contribution.ID), safety_checker=_FakeChecker(0.01))
            ops.check_image(img)
        img.refresh_from_db()
        self.assertEqual(img.safety_status, "safe")
        self.assertIsNotNone(img.safety_checked_at)
        self.assertIsNotNone(img.sharpness_score)

    def test_an_unsafe_image_is_rejected(self):
        img = self._image()
        with override_settings(MEDIA_ROOT=str(self.media)):
            ops = ImageOps(str(self.contribution.ID), safety_checker=_FakeChecker(0.99))
            ops.check_image(img)
        img.refresh_from_db()
        self.assertEqual(img.safety_status, "rejected")

    def test_quality_is_still_measured_for_a_rejected_image(self):
        # The axes are independent (2.8): withholding an image from the site
        # says nothing about whether it would have reconstructed well.
        img = self._image()
        with override_settings(MEDIA_ROOT=str(self.media)):
            ImageOps(
                str(self.contribution.ID), safety_checker=_FakeChecker(0.99)
            ).check_image(img)
        img.refresh_from_db()
        self.assertEqual(img.safety_status, "rejected")
        self.assertIsNotNone(img.sharpness_score)

    def test_an_unavailable_model_stops_the_run_rather_than_publishing(self):
        # Never fall through to "publish it anyway".
        self._image()
        checker = _FakeChecker(raises=SafetyUnavailable("no onnxruntime"))
        with override_settings(MEDIA_ROOT=str(self.media)):
            ops = ImageOps(str(self.contribution.ID), safety_checker=checker)
            with self.assertRaises(SafetyUnavailable):
                ops.check_images()
        self.assertEqual(Image.objects.filter(safety_status="pending").count(), 1)

    def test_an_aborted_run_does_not_mark_the_contribution_processed(self):
        self._image()
        checker = _FakeChecker(raises=SafetyUnavailable("no onnxruntime"))
        with override_settings(MEDIA_ROOT=str(self.media)):
            with self.assertRaises(SafetyUnavailable):
                ImageOps(
                    str(self.contribution.ID), safety_checker=checker
                ).check_images()
        self.contribution.refresh_from_db()
        self.assertFalse(self.contribution.processed)

    def test_no_verdict_field_is_written(self):
        # Usability is a query over measurements, not a stored conclusion.
        img = self._image()
        with override_settings(MEDIA_ROOT=str(self.media)):
            ImageOps(
                str(self.contribution.ID), safety_checker=_FakeChecker(0.0)
            ).check_image(img)
        img.refresh_from_db()
        self.assertEqual(img.admin_verdict, "")
        self.assertFalse(hasattr(img, "label"))


class UnreadableFileTests(_OpsBase):
    def test_an_unreadable_image_is_recorded_not_fatal(self):
        img = self._image()
        (self.media / img.image.name).write_bytes(b"corrupt")
        with override_settings(MEDIA_ROOT=str(self.media)):
            ImageOps(
                str(self.contribution.ID), safety_checker=_FakeChecker(0.0)
            ).check_image(img)
        img.refresh_from_db()
        self.assertIn("UNREADABLE", img.remark)
        self.assertIsNone(img.sharpness_score)

    def test_the_rest_of_the_contribution_still_gets_checked(self):
        self._image("bad.jpg")
        good = self._image("good.jpg")
        (self.media / Image.objects.get(pk=self._first_pk()).image.name).write_bytes(
            b"corrupt"
        )
        with override_settings(MEDIA_ROOT=str(self.media)):
            ImageOps(
                str(self.contribution.ID), safety_checker=_FakeChecker(0.0)
            ).check_images()
        good.refresh_from_db()
        self.assertIsNotNone(good.sharpness_score)
        self.contribution.refresh_from_db()
        self.assertTrue(self.contribution.processed)

    def _first_pk(self):
        return Image.objects.order_by("created_at").first().pk


class ContributionTests(_OpsBase):
    def test_a_contribution_with_no_images_is_refused(self):
        empty = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )
        with self.assertRaises(ValueError):
            ImageOps(str(empty.ID), safety_checker=_FakeChecker())

    def test_a_missing_contribution_is_refused(self):
        with self.assertRaises(ValueError):
            ImageOps("does-not-exist", safety_checker=_FakeChecker())

    def test_a_completed_run_marks_the_contribution_processed(self):
        self._image()
        with override_settings(MEDIA_ROOT=str(self.media)):
            ImageOps(
                str(self.contribution.ID), safety_checker=_FakeChecker(0.0)
            ).check_images()
        self.contribution.refresh_from_db()
        self.assertTrue(self.contribution.processed)
        self.assertIsNotNone(self.contribution.processed_at)


class NudeNetCheckerTests(TestCase):
    def test_the_score_is_the_maximum_not_the_sum(self):
        # Summing would let several low-confidence detections cross the
        # threshold together, which is not what "confident" means.
        checker = NudeNetChecker()
        checker._detector = type(
            "D",
            (),
            {
                "detect": staticmethod(
                    lambda _p: [
                        {"class": "FEMALE_BREAST_EXPOSED", "score": 0.4},
                        {"class": "BUTTOCKS_EXPOSED", "score": 0.3},
                    ]
                )
            },
        )()
        self.assertAlmostEqual(checker.score("x.jpg"), 0.4)

    def test_safe_classes_are_ignored(self):
        checker = NudeNetChecker()
        checker._detector = type(
            "D",
            (),
            {
                "detect": staticmethod(
                    lambda _p: [{"class": "FACE_FEMALE", "score": 0.99}]
                )
            },
        )()
        self.assertEqual(checker.score("x.jpg"), 0.0)

    def test_a_missing_dependency_is_reported_clearly(self):
        checker = NudeNetChecker()
        with self.assertRaises(SafetyUnavailable):
            import builtins

            real_import = builtins.__import__

            def fake_import(name, *a, **kw):
                if name == "nudenet":
                    raise ImportError("no module named nudenet")
                return real_import(name, *a, **kw)

            builtins.__import__ = fake_import
            try:
                _ = checker.detector
            finally:
                builtins.__import__ = real_import


class QuarantineSignalTests(_OpsBase):
    """
    The `pre_save` file mover (internal design notes).

    It used to sort images into good/bad/nsfw from `Image.label`. That field is
    gone, and re-saving any existing Image was raising AttributeError as a
    result -- a regression from 2.6 that this unit fixes. Most of the sorting
    goes with it: quality is a measurement now, and "is this usable" is a query.
    Safety is the exception, because rejected content has to be physically out
    of the served media path.
    """

    def test_re_saving_an_existing_image_does_not_raise(self):
        # The 2.6 regression, pinned.
        img = self._image()
        img.sharpness_score = 42.0
        img.save()
        img.refresh_from_db()
        self.assertEqual(img.sharpness_score, 42.0)

    def test_rejection_moves_the_file_into_quarantine(self):
        with override_settings(MEDIA_ROOT=str(self.media)):
            img = self._image()
            img.safety_status = "rejected"
            img.save()
        img.refresh_from_db()
        self.assertIn("/nsfw/", img.image.name)

    def test_clearing_a_rejection_releases_the_file(self):
        with override_settings(MEDIA_ROOT=str(self.media)):
            img = self._image()
            img.safety_status = "rejected"
            img.save()
            img.safety_status = "safe"
            img.save()
        img.refresh_from_db()
        self.assertNotIn("/nsfw/", img.image.name)

    def test_a_safe_image_is_not_moved(self):
        with override_settings(MEDIA_ROOT=str(self.media)):
            img = self._image()
            before = img.image.name
            img.safety_status = "safe"
            img.save()
        img.refresh_from_db()
        self.assertEqual(img.image.name, before)

    def test_quality_changes_do_not_move_anything(self):
        # Quality is a query, not a folder.
        with override_settings(MEDIA_ROOT=str(self.media)):
            img = self._image()
            before = img.image.name
            img.sharpness_score = 1.0
            img.exposure_score = 0.2
            img.save()
        img.refresh_from_db()
        self.assertEqual(img.image.name, before)

    def test_a_missing_file_does_not_block_moderation(self):
        # Rows outlive files (archived or hand-cleaned runs). Refusing to
        # record a moderation decision because the file has moved on would be
        # the wrong tradeoff.
        with override_settings(MEDIA_ROOT=str(self.media)):
            img = self._image()
            (self.media / img.image.name).unlink()
            img.safety_status = "rejected"
            img.save()
        img.refresh_from_db()
        self.assertEqual(img.safety_status, "rejected")


class SignalRegistrationTests(TestCase):
    """
    Guards a regression introduced in Phase 1.2.

    `TirthaConfig.ready()` imports `tirtha.signals` purely for its side effects
    -- importing the module is what connects every `@receiver` in it. Nothing
    uses the imported name, so `ruff --fix` removed it as an unused import and
    silently disconnected all of them: directory creation on Site save, the
    image quarantine mover, post_migrate defaults and admin notification mail
    stopped firing, with no error anywhere.

    Asserting the receivers are connected catches that class of breakage
    directly, instead of waiting for someone to notice a missing side effect.
    """

    @staticmethod
    def _receiver_names(signal, sender):
        live = signal._live_receivers(sender)
        # Django 5 returns (sync, async) receiver lists; older versions a single
        # list. Accept both rather than pin the shape.
        if isinstance(live, tuple):
            live = [r for group in live for r in group]
        return {r.__name__ for r in live}

    def test_the_image_quarantine_signal_is_connected(self):
        from django.db.models.signals import pre_save

        self.assertIn("pre_save_image", self._receiver_names(pre_save, Image))

    def test_the_site_directory_signal_is_connected(self):
        from django.db.models.signals import post_save

        self.assertIn("post_save_site", self._receiver_names(post_save, Site))
