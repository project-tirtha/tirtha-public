"""
Follow-up review of the review 2026-11-22 change set.

* the reconstruction back-off is a cool-down, never a permanent stop, counts
  scheduled attempts rather than runs, and ignores runs a sweep released;
* the migration starts every existing site's back-off afresh;
* a site left at Processing with nothing behind it is restored by the sweep;
* `trigger_run` reaches holders of `change_contribution`;
* the stuck-ingest sweep does not stack duplicate ImageOps messages.
"""

from __future__ import annotations

import importlib
from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from tirtha.models import Contribution, Contributor, Image, Run, Site


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

    def _image(self):
        return Image.objects.create(
            contribution=self.contribution,
            image=SimpleUploadedFile("a.jpg", b"x", content_type="image/jpeg"),
            safety_status="safe",
        )

    def _run(self, kind="GS", status="Error", *, hours_ago=0.0, minutes=30, notes=""):
        """A run that started `hours_ago` and lasted `minutes`."""
        run = Run.objects.create(site=self.site, kind=kind, status=status, notes=notes)
        started = timezone.now() - timedelta(hours=hours_ago)
        Run.objects.filter(pk=run.pk).update(
            started_at=started, ended_at=started + timedelta(minutes=minutes)
        )
        return run


@override_settings(
    RECON_MIN_NEW_IMAGES=2,
    RECON_MAX_WAIT_DAYS=14,
    RECON_MAX_CONSECUTIVE_FAILURES=3,
    RECON_FAILURE_COOLDOWN_HOURS=24,
    RECON_FAILURE_COOLDOWN_MAX_HOURS=168,
)
class TheBackOffIsACoolDown(_Base):
    def _candidates(self):
        from tirtha.scheduler import sites_awaiting_reconstruction

        return [c.site.ID for c in sites_awaiting_reconstruction()]

    def _fail(self, n, *, newest_hours_ago=1.0):
        # Oldest first, a day apart, the newest `newest_hours_ago`.
        for i in reversed(range(n)):
            self._run(hours_ago=newest_hours_ago + 24 * i)

    def test_within_the_cool_down_the_site_is_skipped(self):
        self._image()
        self._image()
        self._fail(3, newest_hours_ago=1)
        self.assertEqual(self._candidates(), [])

    def test_when_the_cool_down_expires_it_runs_again(self):
        self._image()
        self._image()
        # The last failure ended 25h ago (started 25.5h ago, lasted 30 min).
        self._fail(3, newest_hours_ago=25.5)
        self.assertEqual(self._candidates(), [self.site.ID])

    def test_it_is_measured_from_the_last_failures_end(self):
        from tirtha.scheduler import recon_failure_state

        run = self._run(hours_ago=1)
        self._run(hours_ago=50)
        self._run(hours_ago=80)
        run.refresh_from_db()
        state = recon_failure_state(self.site)
        self.assertEqual(state.failures, 3)
        self.assertEqual(state.last_failure_at, run.ended_at)
        self.assertEqual(state.cooldown_until, run.ended_at + timedelta(hours=24))

    def test_each_further_failure_doubles_it_up_to_the_cap(self):
        from tirtha.scheduler import cooldown_for

        self.assertIsNone(cooldown_for(2))
        self.assertEqual(cooldown_for(3), timedelta(hours=24))
        self.assertEqual(cooldown_for(4), timedelta(hours=48))
        self.assertEqual(cooldown_for(5), timedelta(hours=96))
        self.assertEqual(cooldown_for(6), timedelta(hours=168))
        self.assertEqual(cooldown_for(60), timedelta(hours=168))

    def test_a_fourth_failure_waits_twice_as_long(self):
        self._image()
        self._image()
        # Four failures, the newest ended 30h ago: past 24h, inside 48h.
        self._fail(4, newest_hours_ago=30.5)
        self.assertEqual(self._candidates(), [])

    def test_new_contributions_do_not_bypass_it(self):
        self._fail(3, newest_hours_ago=1)
        for _ in range(10):
            self._image()
        other = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )
        Image.objects.create(
            contribution=other,
            image=SimpleUploadedFile("b.jpg", b"x", content_type="image/jpeg"),
            safety_status="safe",
        )
        self.assertEqual(self._candidates(), [])

    def test_runs_released_by_a_sweep_do_not_count(self):
        from tirtha.celery import RELEASED_RUN_TAG
        from tirtha.scheduler import consecutive_failures

        self._image()
        self._image()
        for i in range(5):
            self._run(hours_ago=1 + i, notes=f"\n\n{RELEASED_RUN_TAG} worker gone")
        self.assertEqual(consecutive_failures(self.site), 0)
        self.assertEqual(self._candidates(), [self.site.ID])

    def test_released_runs_do_not_break_a_streak_either(self):
        from tirtha.celery import RELEASED_RUN_TAG
        from tirtha.scheduler import consecutive_failures

        self._run(hours_ago=5)
        self._run(hours_ago=4, notes=RELEASED_RUN_TAG)
        self._run(hours_ago=3)
        self._run(hours_ago=2, notes=RELEASED_RUN_TAG)
        self._run(hours_ago=1)
        self.assertEqual(consecutive_failures(self.site), 3)

    def test_the_release_paths_write_the_tag(self):
        from tirtha.celery import RELEASED_RUN_TAG, release_orphaned_runs
        from tirtha.scheduler import consecutive_failures

        for _ in range(3):
            Run.objects.create(site=self.site, kind="GS", status="Processing")
        release_orphaned_runs(Run.objects.filter(site=self.site), "Gone.")
        self.assertTrue(
            all(
                RELEASED_RUN_TAG in n
                for n in Run.objects.values_list("notes", flat=True)
            )
        )
        self.assertEqual(consecutive_failures(self.site), 0)

    def test_notes_from_before_the_tag_are_recognised(self):
        from tirtha.scheduler import consecutive_failures

        for note in (
            "Marked Error by the periodic sweep: no recon worker reports ...",
            "Marked Error by the startup sweep: the worker that owned ...",
            "Marked Error when its task failed (WorkerLostError: gone).",
        ):
            self._run(hours_ago=1, notes=f"earlier notes\n\n{note}")
        self.assertEqual(consecutive_failures(self.site), 0)

    def test_an_all_task_failing_in_both_halves_counts_once(self):
        from tirtha.scheduler import consecutive_failures

        # Each attempt: aV runs 2h and fails, GS starts a minute later and fails.
        for hours_ago in (30, 20, 10):
            av = self._run("aV", hours_ago=hours_ago, minutes=120)
            av.refresh_from_db()
            gs = Run.objects.create(site=self.site, kind="GS", status="Error")
            Run.objects.filter(pk=gs.pk).update(
                started_at=av.ended_at + timedelta(minutes=1),
                ended_at=av.ended_at + timedelta(minutes=40),
            )
        self.assertEqual(consecutive_failures(self.site), 3)

    def test_two_runs_of_one_kind_are_two_attempts(self):
        from tirtha.scheduler import consecutive_failures

        # Back to back, as a queued second task would start.
        first = self._run("GS", hours_ago=2, minutes=60)
        first.refresh_from_db()
        second = Run.objects.create(site=self.site, kind="GS", status="Error")
        Run.objects.filter(pk=second.pk).update(
            started_at=first.ended_at + timedelta(seconds=5),
            ended_at=first.ended_at + timedelta(minutes=30),
        )
        self.assertEqual(consecutive_failures(self.site), 2)

    def test_the_admin_shows_when_the_cool_down_ends(self):
        from django.contrib.admin.sites import AdminSite

        from tirtha import admin as tadmin

        self._fail(3, newest_hours_ago=1)
        text = tadmin.SiteAdmin(Site, AdminSite()).recon_failure_state(self.site)
        self.assertIn("backed off", text)
        self.assertIn("until", text)

        Run.objects.filter(site=self.site).update(
            ended_at=timezone.now() - timedelta(hours=30)
        )
        text = tadmin.SiteAdmin(Site, AdminSite()).recon_failure_state(self.site)
        self.assertIn("cool-down ended", text)

    def test_the_scheduler_task_logs_the_cool_down(self):
        from tirtha.tasks import schedule_reconstructions_task

        self._image()
        self._image()
        self._fail(3, newest_hours_ago=1)
        with (
            patch("tirtha.tasks._recon_queue_waiting", return_value=0),
            patch("tirtha.tasks.recon_runner_task.apply_async") as enqueue,
            self.assertLogs("tirtha.tasks", level="WARNING") as logs,
        ):
            schedule_reconstructions_task()
        enqueue.assert_not_called()
        self.assertIn("cooling down until", "\n".join(logs.output))


class TheMigrationStartsTheBackOffAfresh(_Base):
    def test_existing_sites_get_a_reset_instant(self):
        from tirtha.scheduler import consecutive_failures

        for i in range(5):
            self._run(hours_ago=1 + i)
        self.assertEqual(consecutive_failures(self.site), 5)

        module = importlib.import_module(
            "tirtha.migrations.0030_review_fixes_scheduler_moderation"
        )
        module.start_backoff_afresh(apps, None)
        self.site.refresh_from_db()
        self.assertIsNotNone(self.site.recon_failures_reset_at)
        self.assertEqual(consecutive_failures(self.site), 0)


class StrandedProcessingSitesAreRestored(_Base):
    def setUp(self):
        super().setUp()
        Site.objects.filter(pk=self.site.pk).update(status="Processing")

    def _sweep(self):
        from tirtha.celery import sweep_orphaned_runs

        with patch("tirtha.celery._active_recon_sites", return_value=set()):
            sweep_orphaned_runs()
        return Site.objects.get(pk=self.site.pk).status

    def test_a_processing_site_with_no_processing_run_is_restored(self):
        self.assertEqual(self._sweep(), "Pending")

    def test_it_goes_back_to_live_when_it_has_a_published_run(self):
        Run.objects.create(site=self.site, kind="GS", status="Archived")
        self.assertEqual(self._sweep(), "Live")

    def test_a_site_with_a_processing_run_is_left_alone(self):
        # Recent, so the sweep does not release the run itself.
        Run.objects.create(site=self.site, kind="GS", status="Processing")
        self.assertEqual(self._sweep(), "Processing")

    @override_settings(RECON_QUEUED_TTL_HOURS=72)
    def test_a_recently_queued_site_is_left_alone(self):
        Site.objects.filter(pk=self.site.pk).update(recon_queued_at=timezone.now())
        self.assertEqual(self._sweep(), "Processing")

    @override_settings(RECON_QUEUED_TTL_HOURS=72)
    def test_a_stale_queue_marker_does_not_protect_it(self):
        Site.objects.filter(pk=self.site.pk).update(
            recon_queued_at=timezone.now() - timedelta(hours=100)
        )
        self.assertEqual(self._sweep(), "Pending")


class TriggerRunReachesContributionEditors(TestCase):
    def test_change_contribution_grants_trigger_run(self):
        from django.contrib.auth.models import Permission, User

        module = importlib.import_module(
            "tirtha.migrations.0031_admin_action_permissions"
        )
        user = User.objects.create_user("contrib-staff", is_staff=True)
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tirtha", codename="change_contribution"
            )
        )
        module.grant_to_existing_holders(apps, None)
        user = User.objects.get(pk=user.pk)
        self.assertTrue(user.has_perm("tirtha.trigger_run"))
        self.assertFalse(user.has_perm("tirtha.publish_run"))


@override_settings(IMAGEOPS_STUCK_AFTER_MINUTES=60)
class TheIngestSweepDoesNotDuplicate(_Base):
    def setUp(self):
        super().setUp()
        Image.objects.create(
            contribution=self.contribution,
            image=SimpleUploadedFile("a.jpg", b"x", content_type="image/jpeg"),
            safety_status="pending",
        )
        Contribution.objects.filter(pk=self.contribution.pk).update(
            contributed_at=timezone.now() - timedelta(minutes=90)
        )

    def _sweep(self):
        from tirtha.tasks import requeue_stuck_contributions_task

        with patch("tirtha.tasks.post_save_contrib_imageops.apply_async") as enqueue:
            count = requeue_stuck_contributions_task()
        return count, enqueue

    def test_the_sweep_records_the_enqueue_and_does_not_repeat_it(self):
        count, enqueue = self._sweep()
        self.assertEqual(count, 1)
        self.contribution.refresh_from_db()
        self.assertIsNotNone(self.contribution.imageops_enqueued_at)

        count, enqueue = self._sweep()
        self.assertEqual(count, 0)
        enqueue.assert_not_called()

    def test_a_recent_enqueue_elsewhere_is_respected(self):
        Contribution.objects.filter(pk=self.contribution.pk).update(
            imageops_enqueued_at=timezone.now() - timedelta(minutes=10)
        )
        count, enqueue = self._sweep()
        self.assertEqual(count, 0)
        enqueue.assert_not_called()

    def test_an_old_enqueue_is_retried(self):
        Contribution.objects.filter(pk=self.contribution.pk).update(
            imageops_enqueued_at=timezone.now() - timedelta(minutes=61)
        )
        count, enqueue = self._sweep()
        self.assertEqual(count, 1)

    def test_the_admin_trigger_records_the_enqueue(self):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.auth.models import User
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from tirtha import admin as tadmin

        request = RequestFactory().post("/")
        request.user = User.objects.create_superuser("su", "su@example.org", "x")
        request.session = {}
        request._messages = FallbackStorage(request)
        model_admin = tadmin.ContributionAdmin(Contribution, AdminSite())
        with patch("tirtha.admin.post_save_contrib_imageops.delay"):
            model_admin.trigger_imageops(
                request, Contribution.objects.filter(pk=self.contribution.pk)
            )
        self.contribution.refresh_from_db()
        self.assertIsNotNone(self.contribution.imageops_enqueued_at)

    def test_the_upload_view_records_the_enqueue(self):
        import io

        from django.urls import reverse
        from PIL import Image as PILImage

        buffer = io.BytesIO()
        PILImage.new("RGB", (64, 64), (10, 20, 30)).save(buffer, format="JPEG")
        with patch("tirtha.views.auth.GOOGLE_LOGIN", True):
            session = self.client.session
            session["tirtha_user_info"] = {"email": "t@example.org", "name": "T"}
            session.save()
            with (
                patch("tirtha.views.post_save_contrib_imageops.delay") as enqueue,
                patch("tirtha.views.notify_admin_new_contribution_task.delay"),
                patch("tirtha.views.transaction.on_commit", side_effect=lambda f: f()),
            ):
                response = self.client.post(
                    reverse("upload"),
                    {
                        "mesh_vid": self.site.verbose_id,
                        "images": [
                            SimpleUploadedFile(
                                "p.jpg", buffer.getvalue(), content_type="image/jpeg"
                            )
                        ],
                    },
                )
        self.assertEqual(response.json()["status"], "Success")
        enqueue.assert_called_once()
        contribution = Contribution.objects.get(ID=enqueue.call_args.args[0])
        self.assertIsNotNone(contribution.imageops_enqueued_at)
