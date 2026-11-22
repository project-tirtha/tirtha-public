"""
Backend fixes from review 2026-11-22 (§1 #6, #8, #9; §2; §4; §5).

One class per finding, each named for the property it protects.
"""

from __future__ import annotations

import io
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core import mail
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image as PILImage

from tirtha.models import (
    ARK,
    Contribution,
    Contributor,
    Image,
    Run,
    RunStage,
    Site,
    SourceVideo,
)


def _jpeg_bytes(size=(64, 64), colour=(140, 90, 40)) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", size, colour).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


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

    def _image(self, *, status="safe", real=False, age_days=0, **fields):
        data = _jpeg_bytes() if real else b"x"
        img = Image.objects.create(
            contribution=self.contribution,
            image=SimpleUploadedFile("a.jpg", data, content_type="image/jpeg"),
            safety_status=status,
            **fields,
        )
        if age_days:
            Image.objects.filter(pk=img.pk).update(
                created_at=timezone.now() - timedelta(days=age_days)
            )
        return img


class _FakeChecker:
    def __init__(self, score):
        self._score = score
        self.calls = 0

    def score(self, path):
        self.calls += 1
        return self._score


# --- §1 #6: moderation is never overwritten ----------------------------------


class ImageOpsNeverOverwritesAModerator(_Base):
    def _moderate(self, img, status):
        # What `admin._moderate` does: set the field, full save.
        img.safety_status = status
        img.save()
        img.refresh_from_db()
        return img

    def test_a_status_change_by_a_person_is_recorded(self):
        img = self._moderate(self._image(status="safe", real=True), "rejected")
        self.assertIsNotNone(img.safety_moderated_at)

    def test_the_classifier_is_not_mistaken_for_a_person(self):
        from tirtha.imageops import ImageOps

        img = self._image(status="pending", real=True)
        ImageOps(
            str(self.contribution.ID), safety_checker=_FakeChecker(0.99)
        ).check_images()
        img.refresh_from_db()
        self.assertEqual(img.safety_status, "rejected")
        self.assertIsNone(img.safety_moderated_at)

    def test_a_recheck_keeps_a_moderators_nsfw_decision(self):
        from tirtha.imageops import ImageOps

        img = self._moderate(self._image(status="safe", real=True), "rejected")
        checker = _FakeChecker(0.0)
        ImageOps(str(self.contribution.ID), safety_checker=checker).check_images(
            recheck=True
        )
        img.refresh_from_db()
        self.assertEqual(img.safety_status, "rejected")
        self.assertEqual(checker.calls, 0)
        self.assertIn("/nsfw/", img.image.name)

    def test_a_recheck_keeps_a_moderators_release(self):
        from tirtha.imageops import ImageOps

        img = self._moderate(self._image(status="rejected", real=True), "safe")
        ImageOps(
            str(self.contribution.ID), safety_checker=_FakeChecker(0.99)
        ).check_images(recheck=True)
        img.refresh_from_db()
        self.assertEqual(img.safety_status, "safe")

    def test_a_decision_made_while_the_classifier_ran_wins(self):
        from tirtha.imageops import ImageOps

        img = self._image(status="pending", real=True)

        class Racing(_FakeChecker):
            def score(inner, path):
                # A moderator acts between the score and the write.
                fresh = Image.objects.get(pk=img.pk)
                fresh.safety_status = "rejected"
                fresh.save()
                return 0.0

        ImageOps(str(self.contribution.ID), safety_checker=Racing(0.0)).check_images()
        img.refresh_from_db()
        self.assertEqual(img.safety_status, "rejected")

    def test_a_redelivery_does_not_rescore_checked_images(self):
        from tirtha.imageops import ImageOps

        self._image(status="pending", real=True)
        ops = ImageOps(str(self.contribution.ID), safety_checker=_FakeChecker(0.0))
        ops.check_images()
        checker = _FakeChecker(0.99)
        ImageOps(str(self.contribution.ID), safety_checker=checker).check_images()
        self.assertEqual(checker.calls, 0)
        self.assertEqual(Image.objects.get().safety_status, "safe")


class TheMigrationBackfillsHumanDecisions(_Base):
    def test_rows_the_classifier_cannot_have_written_are_marked(self):
        import importlib

        from django.apps import apps

        module = importlib.import_module(
            "tirtha.migrations.0030_review_fixes_scheduler_moderation"
        )
        human_flag = self._image(status="flagged")
        unchecked = self._image(status="rejected")
        disagrees = self._image(status="safe")
        Image.objects.filter(pk=disagrees.pk).update(
            safety_score=0.99, safety_checked_at=timezone.now()
        )
        machine = self._image(status="safe")
        Image.objects.filter(pk=machine.pk).update(
            safety_score=0.01, safety_checked_at=timezone.now()
        )
        Image.objects.update(safety_moderated_at=None)

        module.backfill_moderated(apps, None)

        marked = set(
            Image.objects.filter(safety_moderated_at__isnull=False).values_list(
                "pk", flat=True
            )
        )
        self.assertEqual(marked, {human_flag.pk, unchecked.pk, disagrees.pk})


# --- §1 #9: the thumbnail is processed once ---------------------------------


class TheThumbnailIsProcessedOnlyWhenItChanges(TestCase):
    def _site(self):
        site = Site.objects.create(
            name="Th", country="India", state="Odisha", district="Puri"
        )
        site.thumbnail.save(
            "t.jpg", ContentFile(_jpeg_bytes(size=(1200, 900))), save=True
        )
        return Site.objects.get(pk=site.pk)

    def test_a_plain_save_does_not_reencode(self):
        site = self._site()
        before = Path(site.thumbnail.path).read_bytes()
        with patch.object(Site, "_process_thumbnail") as process:
            site.description = "changed"
            site.save()
        process.assert_not_called()
        self.assertEqual(Path(site.thumbnail.path).read_bytes(), before)

    def test_a_new_thumbnail_is_processed(self):
        site = self._site()
        with patch.object(Site, "_process_thumbnail") as process:
            site.thumbnail.save("n.jpg", ContentFile(_jpeg_bytes()), save=True)
        process.assert_called_once()

    def test_the_first_save_resizes(self):
        site = self._site()
        with PILImage.open(site.thumbnail.path) as handle:
            self.assertEqual(min(handle.size), 400)

    def test_a_missing_file_does_not_fail_the_save(self):
        site = Site.objects.create(
            name="Gone", country="India", state="Odisha", district="Puri"
        )
        site.thumbnail = "models/nowhere/missing.png"
        site.save()  # must not raise
        site.status = "Live"
        site.save()
        self.assertEqual(Site.objects.get(pk=site.pk).status, "Live")


# --- §1 #8: the upload view does not save a stale Site ----------------------


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class UploadViewTests(TestCase):
    def setUp(self):
        patcher = patch("tirtha.views.auth.GOOGLE_LOGIN", True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.site = Site.objects.create(
            name="U", country="India", state="Odisha", district="Khordha"
        )
        self.contributor = Contributor.objects.create(
            name="T", email="t@example.org", active=True
        )
        session = self.client.session
        session["tirtha_user_info"] = {"email": "t@example.org", "name": "T"}
        session.save()

    def _post(self, **payload):
        payload.setdefault("mesh_vid", self.site.verbose_id)
        return self.client.post(reverse("upload"), payload)

    def _jpeg(self, name="p.jpg"):
        return SimpleUploadedFile(name, _jpeg_bytes(), content_type="image/jpeg")

    def _video(self):
        return SimpleUploadedFile(
            "clip.mp4", b"\x00\x00\x00 ftypmp42", content_type="video/mp4"
        )

    def test_the_site_row_is_not_rewritten(self):
        with (
            patch.object(Site, "save", side_effect=AssertionError("full save")),
            patch("tirtha.views.post_save_contrib_imageops.delay"),
            patch("tirtha.views.notify_admin_new_contribution_task.delay"),
        ):
            response = self._post(images=[self._jpeg()])
        self.assertEqual(response.json()["status"], "Success")

    def test_an_edit_made_during_the_upload_survives(self):
        def hide_meanwhile(*args, **kwargs):
            Site.objects.filter(pk=self.site.pk).update(hidden=True, name="Edited")

        with (
            patch(
                "tirtha.views.post_save_contrib_imageops.delay",
                side_effect=hide_meanwhile,
            ),
            patch("tirtha.views.notify_admin_new_contribution_task.delay"),
            patch("tirtha.views.transaction.on_commit", side_effect=lambda f: f()),
        ):
            self._post(images=[self._jpeg()])
        site = Site.objects.get(pk=self.site.pk)
        self.assertTrue(site.hidden)
        self.assertEqual(site.name, "Edited")

    def test_a_failed_enqueue_with_nothing_queued_removes_the_contribution(self):
        with (
            patch(
                "tirtha.views.post_save_contrib_imageops.delay",
                side_effect=OSError("broker down"),
            ),
            patch("tirtha.views.notify_admin_new_contribution_task.delay"),
            patch("tirtha.views.transaction.on_commit", side_effect=lambda f: f()),
        ):
            response = self._post(images=[self._jpeg()])
        self.assertEqual(response.json()["status"], "Error")
        self.assertEqual(Contribution.objects.count(), 0)

    def test_a_contribution_with_queued_work_is_never_deleted(self):
        with (
            patch("tirtha.views.extract_video_frames_task.delay") as extract,
            patch(
                "tirtha.views.post_save_contrib_imageops.delay",
                side_effect=OSError("broker down"),
            ),
            patch("tirtha.views.notify_admin_new_contribution_task.delay"),
            patch("tirtha.views.transaction.on_commit", side_effect=lambda f: f()),
        ):
            response = self._post(images=[self._jpeg()], videos=[self._video()])
        extract.assert_called_once()
        self.assertEqual(response.json()["status"], "Success")
        self.assertEqual(Contribution.objects.count(), 1)

    def test_video_enqueue_failures_are_handled_too(self):
        with (
            patch(
                "tirtha.views.extract_video_frames_task.delay",
                side_effect=OSError("broker down"),
            ),
            patch("tirtha.views.notify_admin_new_contribution_task.delay"),
            patch("tirtha.views.transaction.on_commit", side_effect=lambda f: f()),
        ):
            response = self._post(videos=[self._video()])
        self.assertEqual(response.json()["status"], "Error")
        self.assertEqual(SourceVideo.objects.count(), 0)

    def test_work_is_enqueued_only_on_commit(self):
        with (
            patch("tirtha.views.post_save_contrib_imageops.delay") as checks,
            patch("tirtha.views.notify_admin_new_contribution_task.delay"),
            self.captureOnCommitCallbacks(execute=False) as callbacks,
        ):
            self._post(images=[self._jpeg()])
            checks.assert_not_called()
        self.assertTrue(callbacks)


# --- §5: decode outside the lock ---------------------------------------------


class DecodingHappensBeforeTheLock(_Base):
    def test_files_are_prepared_before_the_site_row_is_locked(self):
        from tirtha import views

        events = []
        real_prepare = views.prepare_upload
        real_lock = Site.objects.select_for_update

        def prepare(*args, **kwargs):
            events.append("decode")
            return real_prepare(*args, **kwargs)

        def lock(*args, **kwargs):
            events.append("lock")
            return real_lock(*args, **kwargs)

        files = [
            SimpleUploadedFile(f"{i}.jpg", _jpeg_bytes(), content_type="image/jpeg")
            for i in range(3)
        ]
        with (
            patch("tirtha.views.uploads.prepare_upload", side_effect=prepare),
            patch.object(Site.objects, "select_for_update", side_effect=lock),
        ):
            views.store_uploaded_images(self.contribution, files, compress=False)
        self.assertEqual(events, ["decode", "decode", "decode", "lock"])
        self.assertEqual(
            list(Image.objects.order_by("seq").values_list("seq", flat=True)),
            [0, 1, 2],
        )

    def test_seq_continues_after_existing_images(self):
        from tirtha import views

        self._image(seq=7)
        views.store_uploaded_images(
            self.contribution,
            [SimpleUploadedFile("n.jpg", _jpeg_bytes(), content_type="image/jpeg")],
            compress=False,
        )
        self.assertEqual(Image.objects.order_by("-seq").first().seq, 8)

    def test_a_failure_under_the_lock_leaves_no_orphan_files(self):
        from tirtha import views

        media = Path(settings.MEDIA_ROOT)
        with (
            patch.object(Image, "save", side_effect=RuntimeError("db gone")),
            self.assertRaises(RuntimeError),
        ):
            views.store_uploaded_images(
                self.contribution,
                [SimpleUploadedFile("o.jpg", _jpeg_bytes(), content_type="image/jpeg")],
                compress=False,
            )
        images_dir = media / f"models/{self.site.ID}/images"
        leftovers = [p for p in images_dir.glob("*") if p.is_file()]
        self.assertEqual(leftovers, [])


# --- §2: scheduler state ------------------------------------------------------


@override_settings(
    RECON_MIN_NEW_IMAGES=2, RECON_MAX_WAIT_DAYS=14, RECON_MAX_CONSECUTIVE_FAILURES=3
)
class SchedulerState(_Base):
    def _candidates(self):
        from tirtha.scheduler import sites_awaiting_reconstruction

        return [c.site.ID for c in sites_awaiting_reconstruction()]

    def test_a_queued_site_is_not_enqueued_again(self):
        self._image()
        self._image()
        Site.objects.filter(pk=self.site.pk).update(recon_queued_at=timezone.now())
        self.assertEqual(self._candidates(), [])

    @override_settings(RECON_QUEUED_TTL_HOURS=72)
    def test_a_lost_queue_marker_expires(self):
        self._image()
        self._image()
        Site.objects.filter(pk=self.site.pk).update(
            recon_queued_at=timezone.now() - timedelta(hours=100)
        )
        self.assertEqual(self._candidates(), [self.site.ID])

    def test_the_task_marks_the_site_before_enqueueing(self):
        from tirtha.tasks import schedule_reconstructions_task

        self._image()
        self._image()
        seen = []

        def enqueue(*args, **kwargs):
            seen.append(Site.objects.get(pk=self.site.pk).recon_queued_at)

        with patch("tirtha.tasks.recon_runner_task.apply_async", side_effect=enqueue):
            schedule_reconstructions_task()
        self.assertIsNotNone(seen[0])
        self.assertIsNotNone(Site.objects.get(pk=self.site.pk).recon_queued_at)

    def test_a_failed_enqueue_clears_the_marker(self):
        from tirtha.tasks import schedule_reconstructions_task

        self._image()
        self._image()
        with (
            patch(
                "tirtha.tasks.recon_runner_task.apply_async",
                side_effect=OSError("broker"),
            ),
            self.assertRaises(OSError),
        ):
            schedule_reconstructions_task()
        self.assertIsNone(Site.objects.get(pk=self.site.pk).recon_queued_at)

    def test_nothing_is_enqueued_while_the_queue_has_a_message_waiting(self):
        from tirtha.tasks import schedule_reconstructions_task

        self._image()
        self._image()
        with (
            patch("tirtha.tasks._recon_queue_waiting", return_value=1),
            patch("tirtha.tasks.recon_runner_task.apply_async") as enqueue,
        ):
            schedule_reconstructions_task()
        enqueue.assert_not_called()

    def test_a_run_starting_clears_the_marker(self):
        Site.objects.filter(pk=self.site.pk).update(recon_queued_at=timezone.now())
        Run.objects.create(site=self.site, kind="GS")
        self.assertIsNone(Site.objects.get(pk=self.site.pk).recon_queued_at)

    def test_the_recon_task_clears_the_marker_even_when_it_does_nothing(self):
        from tirtha.tasks import recon_runner_task

        Site.objects.filter(pk=self.site.pk).update(recon_queued_at=timezone.now())
        with patch("tirtha.workers.prerun_check", return_value=(False, "no")):
            recon_runner_task(str(self.contribution.ID), "GS")
        self.assertIsNone(Site.objects.get(pk=self.site.pk).recon_queued_at)

    def _fail(self, n):
        for _ in range(n):
            Run.objects.create(site=self.site, kind="GS", status="Error")

    def test_a_site_that_keeps_failing_is_backed_off(self):
        self._image()
        self._image()
        self._fail(3)
        self.assertEqual(self._candidates(), [])
        from tirtha.scheduler import backed_off_sites

        self.assertEqual(
            [(s.ID, st.failures) for s, st in backed_off_sites()],
            [(self.site.ID, 3)],
        )

    def test_fewer_failures_than_the_limit_still_run(self):
        self._image()
        self._image()
        self._fail(2)
        self.assertEqual(self._candidates(), [self.site.ID])

    def test_the_manual_reset_lets_it_run_again(self):
        self._image()
        self._image()
        self._fail(3)
        Site.objects.filter(pk=self.site.pk).update(
            recon_failures_reset_at=timezone.now()
        )
        self.assertEqual(self._candidates(), [self.site.ID])

    def test_a_success_resets_the_count(self):
        from tirtha.scheduler import consecutive_failures

        self._fail(3)
        Run.objects.create(site=self.site, kind="GS", status="Archived")
        self.assertEqual(consecutive_failures(self.site), 0)

    @override_settings(RECON_MAX_CONSECUTIVE_FAILURES=0)
    def test_zero_disables_the_back_off(self):
        self._image()
        self._image()
        self._fail(5)
        self.assertEqual(self._candidates(), [self.site.ID])

    def test_an_image_checked_after_a_run_started_is_still_pending(self):
        from tirtha.scheduler import pending_images

        used = self._image(age_days=2)
        late = self._image(status="pending", age_days=2)
        run = Run.objects.create(site=self.site, kind="GS", status="Archived")
        run.images.set([used])
        # Checked after the run started, so the run never had it.
        Image.objects.filter(pk=late.pk).update(safety_status="safe")
        self.assertEqual(list(pending_images(self.site)), [late])

    def test_a_processing_site_is_not_a_candidate(self):
        self._image()
        self._image()
        Site.objects.filter(pk=self.site.pk).update(status="Processing")
        self.assertEqual(self._candidates(), [])

    def test_the_cadence_is_configurable(self):
        from tirtha import tasks

        self.assertIn("RECON_SCHEDULE_MINUTE", Path(tasks.__file__).read_text())
        self.assertEqual(tasks.RECON_SCHEDULE_INTERVAL.hour, set(range(24)))


# --- §2: idempotent tasks ----------------------------------------------------


class ImageOpsRetries(_Base):
    def test_an_unavailable_model_is_retried_not_mailed(self):
        from celery.exceptions import Retry

        from tirtha.imageops import SafetyUnavailable
        from tirtha.tasks import post_save_contrib_imageops

        self._image(status="pending")
        with (
            patch(
                "tirtha.imageops.ImageOps.check_images",
                side_effect=SafetyUnavailable("no model"),
            ),
            patch.object(
                post_save_contrib_imageops, "retry", side_effect=Retry()
            ) as retry,
            patch("tirtha.email_utils.send_image_processing_failure_email") as mailed,
            self.assertRaises(Retry),
        ):
            post_save_contrib_imageops(str(self.contribution.ID))
        self.assertEqual(retry.call_args.kwargs["countdown"], 60)
        mailed.assert_not_called()

    def test_other_failures_still_mail(self):
        from tirtha.tasks import post_save_contrib_imageops

        self._image(status="pending")
        with (
            patch(
                "tirtha.imageops.ImageOps.check_images",
                side_effect=RuntimeError("boom"),
            ),
            patch("tirtha.email_utils.send_image_processing_failure_email") as mailed,
            self.assertRaises(RuntimeError),
        ):
            post_save_contrib_imageops(str(self.contribution.ID))
        mailed.assert_called_once()

    def test_limits_are_declared(self):
        from tirtha.tasks import (
            extract_video_frames_task,
            post_save_contrib_imageops,
            recon_runner_task,
        )

        self.assertEqual(recon_runner_task.soft_time_limit, 30 * 3600)
        self.assertGreater(recon_runner_task.time_limit, 30 * 3600)
        self.assertTrue(extract_video_frames_task.soft_time_limit)
        self.assertTrue(post_save_contrib_imageops.soft_time_limit)


class StuckContributionsAreRequeued(_Base):
    def _age(self, contribution, minutes):
        Contribution.objects.filter(pk=contribution.pk).update(
            contributed_at=timezone.now() - timedelta(minutes=minutes)
        )

    def test_an_old_unprocessed_contribution_is_requeued(self):
        from tirtha.tasks import requeue_stuck_contributions_task

        self._image(status="pending")
        self._age(self.contribution, 90)
        with patch("tirtha.tasks.post_save_contrib_imageops.apply_async") as enqueue:
            self.assertEqual(requeue_stuck_contributions_task(), 1)
        self.assertEqual(enqueue.call_args.kwargs["args"], (str(self.contribution.ID),))

    def test_a_recent_or_processed_one_is_left_alone(self):
        from tirtha.tasks import requeue_stuck_contributions_task

        self._image(status="pending")
        self._age(self.contribution, 10)
        other = Contribution.objects.create(
            site=self.site, contributor=self.contributor, processed=True
        )
        self._age(other, 600)
        with patch("tirtha.tasks.post_save_contrib_imageops.apply_async") as enqueue:
            self.assertEqual(requeue_stuck_contributions_task(), 0)
        enqueue.assert_not_called()

    def test_an_unextracted_video_is_re_extracted(self):
        from tirtha.tasks import requeue_stuck_contributions_task

        video = SourceVideo.objects.create(
            contribution=self.contribution, video="v.mp4"
        )
        self._age(self.contribution, 90)
        with patch("tirtha.tasks.extract_video_frames_task.apply_async") as enqueue:
            requeue_stuck_contributions_task()
        self.assertEqual(enqueue.call_args.kwargs["args"], (str(video.ID),))


class FrameExtractionIsIdempotent(_Base):
    def setUp(self):
        super().setUp()
        media = Path(settings.MEDIA_ROOT)
        path = media / f"models/{self.site.ID}/videos/v.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        self.video = SourceVideo.objects.create(
            contribution=self.contribution, video=f"models/{self.site.ID}/videos/v.mp4"
        )
        self.argv = []

    def _fake_ffmpeg(self, argv, **kwargs):
        self.argv.append(argv)
        out_dir = Path(argv[-1]).parent
        for i in range(4):
            (out_dir / f"cand_{i:06d}.jpg").write_bytes(
                _jpeg_bytes(colour=(i * 40, 10, 10))
            )
        return SimpleNamespace(returncode=0, stderr="")

    def _extract(self, **probe):
        from tirtha import videoops

        info = videoops.VideoInfo(
            duration_s=probe.get("duration", 10.0),
            frame_rate=30.0,
            codec="h264",
            created_time=None,
            is_hdr=False,
            width=probe.get("width", 1920),
            height=probe.get("height", 1080),
        )
        with (
            patch("tirtha.videoops.probe", return_value=info),
            patch("tirtha.videoops._binary", return_value="ffmpeg"),
            patch("tirtha.videoops.subprocess.run", side_effect=self._fake_ffmpeg),
        ):
            video = SourceVideo.objects.get(pk=self.video.pk)
            return videoops.extract_frames(video, budget=2)

    def test_a_redelivery_does_not_insert_a_second_set(self):
        self._extract()
        self._extract()
        self.assertEqual(Image.objects.filter(source_video=self.video).count(), 2)
        self.assertEqual(len(self.argv), 1)

    def test_a_partial_set_is_replaced(self):
        partial = self._image(source_video=self.video)
        self._extract()
        frames = Image.objects.filter(source_video=self.video)
        self.assertEqual(frames.count(), 2)
        self.assertNotIn(partial.pk, set(frames.values_list("pk", flat=True)))

    def test_frames_continue_the_sites_sequence(self):
        self._image(seq=41)
        self._extract()
        seqs = sorted(
            Image.objects.filter(source_video=self.video).values_list("seq", flat=True)
        )
        self.assertEqual(seqs, [42, 43])

    def test_ffmpeg_is_restricted_and_bounded(self):
        self._extract()
        argv = self.argv[0]
        self.assertEqual(argv[argv.index("-protocol_whitelist") + 1], "file")
        self.assertLess(argv.index("-t"), argv.index("-i"))

    @override_settings(VIDEO_MAX_PIXELS=1000)
    def test_an_oversized_video_is_refused(self):
        from tirtha.videoops import VideoUnavailable

        with self.assertRaises(VideoUnavailable):
            self._extract()
        self.assertEqual(self.argv, [])

    def test_a_hung_ffmpeg_times_out(self):
        import subprocess

        from tirtha import videoops

        def hang(argv, **kwargs):
            self.assertIn("timeout", kwargs)
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

        self._fake_ffmpeg = hang
        with self.assertRaises(videoops.VideoUnavailable):
            self._extract()

    def test_ffprobe_is_restricted_and_has_a_timeout(self):
        from tirtha import videoops

        captured = {}

        def run(argv, **kwargs):
            captured["argv"], captured["kwargs"] = argv, kwargs
            return SimpleNamespace(stdout="{}", returncode=0)

        with (
            patch("tirtha.videoops._binary", return_value="ffprobe"),
            patch("tirtha.videoops.subprocess.run", side_effect=run),
            self.assertRaises(videoops.VideoUnavailable),
        ):
            videoops.probe("/v.mp4")
        argv = captured["argv"]
        self.assertEqual(argv[argv.index("-protocol_whitelist") + 1], "file")
        self.assertIn("timeout", captured["kwargs"])


class OrphanedRunsRestoreTheSite(_Base):
    def setUp(self):
        super().setUp()
        Site.objects.filter(pk=self.site.pk).update(status="Processing")
        self.run = Run.objects.create(site=self.site, kind="GS", status="Processing")
        Run.objects.filter(pk=self.run.pk).update(
            started_at=timezone.now() - timedelta(hours=2)
        )

    def _publish(self):
        Run.objects.create(site=self.site, kind="GS", status="Archived")

    def test_a_published_site_goes_back_to_live(self):
        from tirtha.celery import reconcile_orphaned_runs

        self._publish()
        reconcile_orphaned_runs(sender=SimpleNamespace(hostname="recon@h"))
        self.assertEqual(Site.objects.get(pk=self.site.pk).status, "Live")
        self.assertEqual(Run.objects.get(pk=self.run.pk).status, "Error")

    def test_an_unpublished_site_goes_back_to_pending(self):
        from tirtha.celery import reconcile_orphaned_runs

        reconcile_orphaned_runs(sender=SimpleNamespace(hostname="recon@h"))
        self.assertEqual(Site.objects.get(pk=self.site.pk).status, "Pending")

    def test_the_periodic_sweep_releases_a_run_no_worker_owns(self):
        from tirtha.celery import sweep_orphaned_runs

        self._publish()
        with patch("tirtha.celery._active_recon_sites", return_value=set()):
            self.assertEqual(sweep_orphaned_runs(), 1)
        self.assertEqual(Site.objects.get(pk=self.site.pk).status, "Live")

    def test_the_periodic_sweep_leaves_a_live_run_alone(self):
        from tirtha.celery import sweep_orphaned_runs

        with patch("tirtha.celery._active_recon_sites", return_value={self.site.ID}):
            self.assertEqual(sweep_orphaned_runs(), 0)
        self.assertEqual(Run.objects.get(pk=self.run.pk).status, "Processing")

    def test_when_nobody_can_say_only_an_impossibly_old_run_is_released(self):
        from tirtha.celery import sweep_orphaned_runs

        with patch("tirtha.celery._active_recon_sites", return_value=None):
            self.assertEqual(sweep_orphaned_runs(), 0)
        Run.objects.filter(pk=self.run.pk).update(
            started_at=timezone.now() - timedelta(hours=40)
        )
        with patch("tirtha.celery._active_recon_sites", return_value=None):
            self.assertEqual(sweep_orphaned_runs(), 1)

    def test_a_failed_recon_task_releases_its_runs(self):
        from billiard.exceptions import WorkerLostError

        from tirtha.celery import task_failure_handler

        task_failure_handler(
            sender=SimpleNamespace(name="tirtha.tasks.recon_runner_task"),
            task_id="t",
            exception=WorkerLostError("gone"),
            args=(str(self.contribution.ID), "all"),
            kwargs={},
        )
        self.assertEqual(Run.objects.get(pk=self.run.pk).status, "Error")
        self.assertEqual(Site.objects.get(pk=self.site.pk).status, "Pending")


# --- §4 -----------------------------------------------------------------------


class ContributorsAreProtected(_Base):
    def test_a_contributor_with_contributions_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError):
            self.contributor.delete()

    def test_one_without_contributions_still_can(self):
        lonely = Contributor.objects.create(name="L", email="l@example.org")
        lonely.delete()
        self.assertFalse(Contributor.objects.filter(pk=lonely.pk).exists())


class ReCutStagesAreRecorded(_Base):
    def test_a_recut_writes_new_attempts(self):
        from tirtha.tasks import gaussian_cut_task

        run = Run.objects.create(site=self.site, kind="GS", status="Archived")
        for name in ("run_gaussian_cut", "run_postprocess"):
            RunStage.objects.create(run=run, name=name, attempt=1, status="Completed")

        ops = MagicMock()
        ops.logger._log_file = "/tmp/x.log"
        ops._end_stage.side_effect = lambda stage, status: stage.finish(status)
        with patch("tirtha.workers.GSOps.for_existing_run", return_value=ops):
            gaussian_cut_task(run.ID)

        for name in ("run_gaussian_cut", "run_postprocess"):
            attempts = sorted(
                RunStage.objects.filter(run=run, name=name).values_list(
                    "attempt", flat=True
                )
            )
            self.assertEqual(attempts, [1, 2])
        self.assertEqual(
            RunStage.objects.get(run=run, name="run_postprocess", attempt=2).status,
            "Completed",
        )


class DeletingAnArchivedRunRemovesItsPublishedFiles(_Base):
    def test_every_sibling_goes_and_nothing_else(self):
        run = Run.objects.create(site=self.site, kind="GS", status="Archived")
        published = Path(settings.STATIC_ROOT) / f"models/{self.site.ID}/published"
        published.mkdir(parents=True, exist_ok=True)
        mine = [
            f"{self.site.ID}_{run.ID}.sog",
            f"{self.site.ID}_{run.ID}.splat",
            f"{self.site.ID}_{run.ID}.splat.gz",
            f"{self.site.ID}_{run.ID}.compressed.ply",
        ]
        other = f"{self.site.ID}_{'Z' * 16}.sog"
        for name in [*mine, other]:
            (published / name).write_bytes(b"x")

        run.delete()

        for name in mine:
            self.assertFalse((published / name).exists(), name)
        self.assertTrue((published / other).exists())


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class SignupMailIsSentFromATask(TestCase):
    def test_nothing_is_sent_inside_the_request(self):
        with self.captureOnCommitCallbacks(execute=False):
            Contributor.objects.create(name="N", email="n@example.org", active=False)
            self.assertEqual(len(mail.outbox), 0)

    def test_the_task_is_enqueued_on_commit_and_sends(self):
        with (
            patch(
                "tirtha.tasks.notify_admin_new_contributor_task.delay",
                wraps=lambda cid: __import__(
                    "tirtha.tasks", fromlist=["x"]
                ).notify_admin_new_contributor_task(cid),
            ) as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            Contributor.objects.create(name="N", email="n@example.org", active=False)
        delay.assert_called_once()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Sign-up", mail.outbox[0].subject)

    def test_an_active_contributor_triggers_nothing(self):
        with (
            patch("tirtha.tasks.notify_admin_new_contributor_task.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            Contributor.objects.create(name="A", email="a@example.org", active=True)
        delay.assert_not_called()


class SmallDefects(_Base):
    def test_run_repr_with_no_ark(self):
        run = Run.objects.create(site=self.site, kind="GS")
        self.assertIn("None", repr(run))

    def test_run_repr_with_an_ark(self):
        ark = ARK.objects.create(
            ark="99999/fk4abc",
            naan="99999",
            shoulder="/fk4",
            assigned_name="abc",
            url="https://example.org/m.sog",
            metadata={},
        )
        run = Run.objects.create(site=self.site, kind="GS", ark=ark)
        self.assertIn("m.sog", repr(run))

    def test_a_run_save_is_one_query_once_it_has_a_directory(self):
        run = Run.objects.create(site=self.site, kind="GS")
        self.assertTrue(run.directory)
        with self.assertNumQueries(1):
            run.status = "Error"
            run.save()

    def test_the_previous_period_wraps_the_year(self):
        from datetime import datetime

        from tirtha.leaderboard import current_period, previous_period

        jan = timezone.make_aware(datetime(2027, 1, 1, 6, 0))
        self.assertEqual(current_period(jan), "2027-01")
        self.assertEqual(previous_period(jan), "2026-12")

    def test_the_period_is_local_not_utc(self):
        from datetime import datetime
        from datetime import timezone as dt_tz

        from tirtha.leaderboard import current_period

        # 02:00 IST on the 1st is still the previous month in UTC.
        moment = datetime(2026, 11, 30, 20, 30, tzinfo=dt_tz.utc)
        self.assertEqual(current_period(moment), "2026-12")

    def test_the_refresh_rebuilds_the_previous_month(self):
        from tirtha.tasks import refresh_leaderboard_task

        with (
            patch("tirtha.leaderboard.compute", return_value=[]) as compute,
            patch("tirtha.leaderboard.award_badges", return_value=0),
            patch("tirtha.leaderboard.previous_period", return_value="2026-10"),
            patch("tirtha.leaderboard.current_period", return_value="2026-11"),
        ):
            refresh_leaderboard_task()
        periods = [call.args[0] for call in compute.call_args_list]
        self.assertEqual(periods, ["all", "2026-10", "2026-11"])

    def test_meshops_contrib_delay_is_no_longer_read(self):
        from tirtha import tasks

        self.assertNotIn("MESHOPS_CONTRIB_DELAY", Path(tasks.__file__).read_text())


# --- §5: OIDC nonce ------------------------------------------------------------


class TheOidcNonceIsChecked(TestCase):
    def test_authlib_validates_the_nonce_it_stored_at_signin(self):
        from tirtha import views

        with patch.object(views.google, "load_server_metadata", return_value={}):
            response = self.client.get(reverse("signin"))
        self.assertEqual(response.status_code, 302)
        state = self.client.session["auth_random_state"]
        stored = self.client.session[f"_state_google_{state}"]["data"]
        self.assertTrue(stored.get("nonce"))

        idinfo = {"email": "o@example.org", "name": "O", "email_verified": True}
        with (
            patch.object(views.google, "load_server_metadata", return_value={}),
            patch.object(
                views.google,
                "fetch_access_token",
                return_value={"id_token": "tok", "access_token": "a"},
            ) as fetch,
            patch.object(views.google, "parse_id_token", return_value={}) as parse,
            patch("tirtha.views.id_token.verify_oauth2_token", return_value=idinfo),
        ):
            response = self.client.get(
                reverse("verifyToken"), {"state": state, "code": "c"}
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse.call_args.kwargs["nonce"], stored["nonce"])
        # The redirect URI sent with the code is the one used at sign-in.
        self.assertEqual(fetch.call_args.kwargs["redirect_uri"], stored["redirect_uri"])
        self.assertEqual(
            self.client.session["tirtha_user_info"]["email"], "o@example.org"
        )

    def test_a_callback_without_authlibs_record_is_refused(self):
        session = self.client.session
        session["auth_random_state"] = "st4te"
        session.save()
        response = self.client.get(
            reverse("verifyToken"), {"state": "st4te", "code": "c"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.client.session.get("tirtha_user_info"))


# --- Permissions declared for admin._may -------------------------------------


class AdminPermissionsExist(TestCase):
    def test_every_codename_admin_checks_is_declared(self):
        from django.contrib.auth.models import Permission

        for codename in (
            "publish_site",
            "publish_run",
            "trigger_run",
            "moderate_contributor",
            "moderate_image",
        ):
            self.assertTrue(
                Permission.objects.filter(
                    content_type__app_label="tirtha", codename=codename
                ).exists(),
                codename,
            )

    def test_staff_with_change_permissions_keep_their_actions(self):
        import importlib

        from django.apps import apps
        from django.contrib.auth.models import Group, Permission, User

        module = importlib.import_module(
            "tirtha.migrations.0031_admin_action_permissions"
        )
        user = User.objects.create_user("staff", is_staff=True)
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tirtha", codename="change_run"
            )
        )
        group = Group.objects.create(name="mods")
        group.permissions.add(
            Permission.objects.get(
                content_type__app_label="tirtha", codename="change_image"
            )
        )
        bystander = User.objects.create_user("viewer", is_staff=True)

        module.grant_to_existing_holders(apps, None)

        user = User.objects.get(pk=user.pk)
        self.assertTrue(user.has_perm("tirtha.publish_run"))
        self.assertTrue(user.has_perm("tirtha.trigger_run"))
        self.assertFalse(user.has_perm("tirtha.moderate_image"))
        member = User.objects.create_user("m", is_staff=True)
        member.groups.add(group)
        self.assertTrue(
            User.objects.get(pk=member.pk).has_perm("tirtha.moderate_image")
        )
        self.assertFalse(
            User.objects.get(pk=bystander.pk).has_perm("tirtha.trigger_run")
        )
