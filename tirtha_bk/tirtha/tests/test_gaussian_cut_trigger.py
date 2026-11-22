"""
Cutting a reconstruction that has already been published.

Masks arrive after the model does, so the cut has to be firable at a run that
already exists. The danger in that is not the cut: it is everything the normal
failure path does on the way out. `_handle_error` marks the Run *and its Site*
as Error, stamps `ended_at`, rewrites `notes` and emails the admins -- correct
for a reconstruction that failed, catastrophic for one that is already serving.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from tirtha.models import Run, Site
from tirtha.workers import GSOps


class BindingToAnExistingRun(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="Konark", country="India", state="Odisha", district="Puri"
        )
        self.run = Run.objects.create(site=self.site, kind="GS")

    def test_it_does_not_create_another_run(self):
        # `__init__` creates a Run and its directory, which is the whole reason
        # this alternate constructor exists: a second Run would retrain from
        # scratch and produce a different model from the one being cut.
        before = Run.objects.count()
        GSOps.for_existing_run(self.run)
        self.assertEqual(Run.objects.count(), before)

    def test_it_binds_to_the_model_the_run_actually_trained_on(self):
        # Not the newest model in the site's workspace. Continuation writes
        # sparse/1, sparse/2, ... so reading the workspace now could hand the
        # cut poses that never produced these Gaussians.
        ops = GSOps.for_existing_run(self.run)
        self.assertEqual(ops.colmapModel.parts[-3:], ("dataset", "sparse", "0"))
        self.assertEqual(Path(ops.splatSource).name, "reconstruction.ply")

    def test_a_failure_does_not_retire_the_run_or_its_site(self):
        """The property this whole class exists for."""
        ops = GSOps.for_existing_run(self.run)

        with self.assertRaises(RuntimeError):
            ops._handle_error(RuntimeError("a bad mask"), "run_gaussian_cut")

        self.run.refresh_from_db()
        self.site.refresh_from_db()
        self.assertNotEqual(self.run.status, "Error")
        self.assertNotEqual(self.site.status, "Error")
        self.assertIsNone(self.run.ended_at)

    def test_the_real_handler_still_retires_a_run(self):
        # The control. If `_handle_error` stopped doing this for everyone, the
        # test above would pass for the wrong reason.
        ops = GSOps.for_existing_run(self.run)
        with (
            patch.object(GSOps, "_update_site_status"),
            patch.object(GSOps, "_update_run_status") as update_run,
            patch.object(
                GSOps, "_resolve_contribution_details", return_value=(None, None)
            ),
        ):
            del ops._handle_error  # fall back to the class's real one
            with self.assertRaises(RuntimeError):
                ops._handle_error(RuntimeError("a real failure"), "run_frgs")
        update_run.assert_called_once_with("Error")


class TheAdminAction(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="Konark", country="India", state="Odisha", district="Puri"
        )

    def _action(self, runs):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory

        from tirtha.admin import RunAdmin

        request = RequestFactory().post("/")
        request.session = "s"
        request._messages = _Messages()
        RunAdmin(Run, AdminSite()).trigger_gaussian_cut(
            request, Run.objects.filter(ID__in=[r.ID for r in runs])
        )
        return request._messages.sent

    def test_an_av_run_is_skipped_rather_than_queued(self):
        # There are no Gaussians in a mesh to cut, and the recon queue has one
        # slot -- spending it to fail is worse than saying no here.
        run = Run.objects.create(site=self.site, kind="aV")
        with patch("tirtha.tasks.gaussian_cut_task.delay") as delay:
            messages = self._action([run])
        delay.assert_not_called()
        self.assertTrue(any("is a aV run" in m for m in messages))

    def test_a_run_with_no_splat_is_skipped(self):
        run = Run.objects.create(site=self.site, kind="GS")
        with patch("tirtha.tasks.gaussian_cut_task.delay") as delay:
            messages = self._action([run])
        delay.assert_not_called()
        self.assertTrue(any("reconstruction.ply" in m for m in messages))


class _Messages:
    """The minimum `message_user` needs, without the middleware stack."""

    def __init__(self):
        self.sent = []

    def add(self, level, message, extra_tags=""):
        self.sent.append(str(message))
