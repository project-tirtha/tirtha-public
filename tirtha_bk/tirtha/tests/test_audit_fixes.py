"""
Fixes from the 2026-09-06 full audit.

Each of these was verified against the running system before being changed, and
each is asserted here on the behaviour rather than on the source where possible.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tirtha.models import ARK, Contributor, Run, Site


class EmbedsOnlyEverShowAPublishedRun(TestCase):
    """
    `_resolve` ordered by `-ended_at`, and PostgreSQL sorts NULLs *first* under
    DESC. A run that has started and not finished has `ended_at = NULL`, so the
    instant a reconstruction began, every embed of that site switched to a run
    with no artifact and rendered nothing -- correcting itself hours later when
    the run finished, which is the kind of fault nobody catches.
    """

    def setUp(self):
        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.finished = Run.objects.create(site=self.site, kind="GS", status="Archived")
        Run.objects.filter(pk=self.finished.pk).update(
            ended_at=timezone.now() - timezone.timedelta(days=1)
        )
        # The relation lives on Run: `Run.ark` is a nullable OneToOne to ARK,
        # and `ARK.run` is its reverse accessor. The ARK validates that its
        # string equals naan + shoulder + assigned_name.
        self.finished.ark = self._ark("abcdef")
        self.finished.save(update_fields=["ark"])
        self.finished.refresh_from_db()

    @staticmethod
    def _ark(name):
        return ARK.objects.create(
            ark=f"74218/t1{name}",
            naan="74218",
            shoulder="/t1",
            assigned_name=name,
            url="https://example.org/models/scene.sog",
            metadata={},
            commitment="Permanent, unambiguous identifier.",
        )

    def _resolve(self):
        from tirtha.embed import _resolve

        return _resolve(str(self.site.ID), None)[1]

    def test_a_finished_run_is_chosen(self):
        self.assertEqual(self._resolve().ID, self.finished.ID)

    def test_a_run_in_flight_does_not_displace_it(self):
        in_flight = Run.objects.create(site=self.site, kind="GS", status="Processing")
        self.assertIsNone(in_flight.ended_at)
        self.assertEqual(self._resolve().ID, self.finished.ID)

    def test_a_run_without_an_ark_is_not_embeddable(self):
        """A run with no ARK has nothing to show whatever its status says."""
        # Newer, finished -- but with no ARK, so nothing to show.
        newer = Run.objects.create(site=self.site, kind="GS", status="Archived")
        Run.objects.filter(pk=newer.pk).update(ended_at=timezone.now())
        self.assertIsNone(newer.ark)
        self.assertEqual(self._resolve().ID, self.finished.ID)


class ImageReprDoesNotRaise(TestCase):
    """`__repr__` interpolated `label`, dropped in migration 0016."""

    def test_repr_of_an_image_works(self):
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image as PILImage

        from tirtha.models import Contribution, Image

        site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        contribution = Contribution.objects.create(
            site=site,
            contributor=Contributor.objects.create(
                name="T", email="t@example.org", active=True
            ),
        )
        buffer = io.BytesIO()
        PILImage.new("RGB", (8, 8)).save(buffer, format="JPEG")
        img = Image(contribution=contribution)
        img.image.save("a.jpg", SimpleUploadedFile("a.jpg", buffer.getvalue()))
        # The bug: AttributeError, from anything that reprs an Image --
        # a shell session, a traceback, a log line.
        text = repr(img)
        self.assertIn(str(img.ID), text)
        self.assertIn(img.safety_status, text)


class SignInFailureIsNotAServerError(TestCase):
    """
    authlib raises `OAuthError`, whose MRO carries no `ValueError`, as the first
    statement of `authorize_access_token` whenever Google reports a problem --
    most commonly a visitor pressing Cancel on the consent screen.
    """

    def _callback(self, exc):
        session = self.client.session
        session["auth_random_state"] = "st4te"
        session["auth_next"] = "/project/tirtha/site/x/"
        session.save()
        with patch("tirtha.views.google.authorize_access_token", side_effect=exc):
            return self.client.get(reverse("verifyToken"), {"state": "st4te"})

    def test_a_cancelled_consent_screen_is_not_a_500(self):
        from authlib.integrations.base_client.errors import OAuthError

        response = self._callback(OAuthError(error="access_denied"))
        self.assertEqual(response.status_code, 403)

    def test_a_network_failure_talking_to_google_is_not_a_500(self):
        response = self._callback(OSError("connection reset"))
        self.assertEqual(response.status_code, 403)

    def test_the_stale_state_and_destination_are_cleared(self):
        from authlib.integrations.base_client.errors import OAuthError

        self._callback(OAuthError(error="access_denied"))
        self.assertIsNone(self.client.session.get("auth_random_state"))
        self.assertIsNone(self.client.session.get("auth_next"))


class TheSecretKeyGuardIsArmed(TestCase):
    """
    `local_settings.py` passed `debug=True` with a comment lifted from beta, so
    a missing SECRET_KEY would boot on a literal committed to a public repo.
    """

    def test_production_settings_do_not_hardcode_debug_true(self):
        from pathlib import Path

        import tirtha_bk

        path = Path(tirtha_bk.__file__).parent / "local_settings.py"
        if not path.exists():
            self.skipTest("local_settings.py is gitignored; absent in a fresh clone")
        source = path.read_text()
        block = source.split("SECRET_KEY = env_required(")[1].split(")")[0]
        self.assertNotIn("debug=True", block)
        self.assertNotIn("django-insecure", block)


class OneBadFileDoesNotCostTheBatch(TestCase):
    """
    `NudeNetChecker.score` runs `cv2.imread`, which returns None for a truncated
    or non-image file and then raises `AttributeError: 'NoneType' object has no
    attribute 'shape'`. Nothing caught it, so it left `check_images` entirely:
    every image after the bad one went unchecked, the contribution was never
    marked processed, and because `reconstructable_images` counts only `safe`
    rows the whole batch stayed invisible to the pipeline.
    """

    def setUp(self):
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image as PILImage

        from tirtha.models import Contribution, Image

        site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.contribution = Contribution.objects.create(
            site=site,
            contributor=Contributor.objects.create(
                name="T", email="t@example.org", active=True
            ),
        )
        buffer = io.BytesIO()
        PILImage.new("RGB", (16, 16), (7, 7, 7)).save(buffer, format="JPEG")
        self.images = []
        for name in ("a.jpg", "bad.jpg", "c.jpg"):
            img = Image(contribution=self.contribution, original_filename=name)
            img.image.save(name, SimpleUploadedFile(name, buffer.getvalue()))
            self.images.append(img)

    def _run(self):
        from tirtha.imageops import ImageOps

        bad = self.images[1]

        class _Checker:
            def score(self, path):
                if str(path).endswith(bad.image.name.split("/")[-1]):
                    raise AttributeError("'NoneType' object has no attribute 'shape'")
                return 0.0

        ImageOps(str(self.contribution.ID), safety_checker=_Checker()).check_images()

    def test_the_images_after_the_bad_one_are_still_checked(self):
        self._run()
        first, bad, last = (i.__class__.objects.get(pk=i.pk) for i in self.images)
        self.assertEqual(first.safety_status, "safe")
        self.assertEqual(last.safety_status, "safe")

    def test_the_unreadable_one_is_left_unchecked_not_published(self):
        self._run()
        bad = self.images[1].__class__.objects.get(pk=self.images[1].pk)
        self.assertEqual(bad.safety_status, "pending")
        self.assertIn("UNCHECKED", bad.remark)

    def test_the_contribution_is_still_marked_processed(self):
        from tirtha.models import Contribution

        self._run()
        self.assertTrue(Contribution.objects.get(pk=self.contribution.pk).processed)

    def test_a_missing_safety_model_still_aborts_everything(self):
        """The other direction must not have been weakened."""
        from tirtha.imageops import ImageOps, SafetyUnavailable
        from tirtha.models import Contribution

        class _Down:
            def score(self, path):
                raise SafetyUnavailable("model not loaded")

        with self.assertRaises(SafetyUnavailable):
            ImageOps(str(self.contribution.ID), safety_checker=_Down()).check_images()
        self.assertFalse(Contribution.objects.get(pk=self.contribution.pk).processed)


class ContributionTasksAreNotLostOnRestart(TestCase):
    """
    The deployed settings module is the only one of six that omits
    `CELERY_TASK_ACKS_LATE`, so in production a message is acked on delivery and
    a worker restart between the ack and the work destroys it. For these two
    that means an upload is accepted, stored, and then never checked -- with
    nothing recording that it did not happen.
    """

    def test_the_ingest_tasks_that_carry_a_contribution_ack_late(self):
        from tirtha.tasks import extract_video_frames_task, post_save_contrib_imageops

        self.assertTrue(post_save_contrib_imageops.acks_late)
        self.assertTrue(extract_video_frames_task.acks_late)

    def test_the_reconstruction_task_deliberately_does_not(self):
        """
        Late-acking `recon` would redeliver a killed reconstruction on the next
        start -- racing the startup sweep that has just marked that run Error,
        and turning a task that reliably crashes the worker into one that
        crashes it for ever.
        """
        # Asserted on the declaration, not the effective value: the dev
        # settings this suite runs under set CELERY_TASK_ACKS_LATE globally, so
        # the resolved attribute is True there. What matters is that the recon
        # tasks do not ask for it themselves, and the ingest two do.
        import inspect
        import re

        from tirtha import tasks

        source = inspect.getsource(tasks)
        by_task = {
            name: args
            for args, name in re.findall(r"@app\.task\(([^)]*)\)\s*\ndef (\w+)", source)
        }
        for name in ("recon_runner_task", "gaussian_cut_task"):
            self.assertNotIn("acks_late", by_task[name], f"{name} asks for acks_late")
        for name in ("post_save_contrib_imageops", "extract_video_frames_task"):
            self.assertIn("acks_late=True", by_task[name])


class SynchronousMailCannotHangAWorker(TestCase):
    def test_email_timeout_is_set(self):
        from django.conf import settings

        # `EmailBackend` opens a socket with no timeout unless given one, so an
        # SMTP host that accepts a connection and stops responding blocks
        # whatever is sending -- and some mail is still sent from the request.
        self.assertTrue(getattr(settings, "EMAIL_TIMEOUT", None))
        self.assertLessEqual(settings.EMAIL_TIMEOUT, 30)


class ArkResolutionDoesNotLoop(TestCase):
    """
    `resolveARK` bounced everything it could not resolve to n2t.net. NAAN 74218
    is ours and is registered at n2t as resolving here, so n2t sent the visitor
    straight back and we sent them to n2t again. 30 of the 102 ARKs minted so
    far land in that branch -- 20 whose run has been deleted, 10 pointing at
    hidden content.
    """

    def setUp(self):
        from django.conf import settings

        self.naan = str(settings.ARK_NAAN)
        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )

    def _ark_row(self, name):
        return ARK.objects.create(
            ark=f"{self.naan}/t1{name}",
            naan=self.naan,
            shoulder="/t1",
            assigned_name=name,
            url="https://example.org/models/scene.sog",
            metadata={},
            commitment="Permanent, unambiguous identifier.",
        )

    def _get(self, ark):
        return self.client.get(f"/project/tirtha/ark:/{ark}")

    def test_an_ark_whose_run_was_deleted_is_a_tombstone_not_a_redirect(self):
        self._ark_row("gone")
        response = self._get(f"{self.naan}/t1gone")
        self.assertEqual(response.status_code, 410)
        self.assertNotIn("n2t.net", response.get("Location", ""))

    def test_an_unknown_ark_under_our_naan_does_not_bounce_to_n2t(self):
        response = self._get(f"{self.naan}/t1neverminted")
        self.assertNotEqual(response.status_code, 302)
        self.assertIn(response.status_code, (403, 404))

    def test_a_hidden_run_answers_like_absent(self):
        ark = self._ark_row("hidden")
        hidden_site = Site.objects.create(
            name="H", country="India", state="X", district="Y", hidden=True
        )
        Run.objects.create(site=hidden_site, kind="GS", ark=ark)
        response = self._get(f"{self.naan}/t1hidden")
        self.assertIn(response.status_code, (403, 404))
        self.assertNotIn("n2t.net", response.get("Location", ""))

    def test_a_foreign_naan_still_goes_to_the_global_resolver(self):
        """n2t is the one place that might know, and cannot loop back here."""
        response = self._get("12345/t1somebodyelse")
        self.assertEqual(response.status_code, 302)
        self.assertIn("n2t.net", response["Location"])

    def test_a_resolvable_ark_still_reaches_its_run(self):
        ark = self._ark_row("live")
        run = Run.objects.create(site=self.site, kind="GS", ark=ark)
        response = self._get(f"{self.naan}/t1live")
        self.assertEqual(response.status_code, 302)
        self.assertIn(str(self.site.ID), response["Location"])
        self.assertIn(str(run.ID), response["Location"])
