"""
Follow-ups to review 2026-11-22: the admin, the failure path and the embed
catching up with backend changes made in the same review.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from tirtha import admin as tadmin
from tirtha.models import ARK, Contribution, Contributor, Image, Run, Site


def _site(**kw):
    return Site.objects.create(
        name="Konark", country="India", state="Odisha", district="Puri", **kw
    )


def _superuser():
    return get_user_model().objects.create_superuser("root", "root@example.org", "pw")


class _Messages:
    def __init__(self):
        self.sent = []

    def add(self, level, message, extra_tags=""):
        self.sent.append(str(message))


def _request(user):
    request = RequestFactory().post("/")
    request.user = user
    request.session = "s"
    request._messages = _Messages()
    return request


class TheImageOpsRerunRechecks(TestCase):
    def test_the_action_asks_for_a_recheck(self):
        # Without `recheck=True` the task looks only at never-checked images,
        # so the operator's re-run silently did nothing.
        site = _site()
        contributor = Contributor.objects.create(name="A", email="a@example.org")
        contribution = Contribution.objects.create(site=site, contributor=contributor)
        model_admin = tadmin.ContributionAdmin(Contribution, AdminSite())
        with patch("tirtha.admin.post_save_contrib_imageops.delay") as delay:
            model_admin.trigger_imageops(
                _request(_superuser()), Contribution.objects.filter(pk=contribution.pk)
            )
        delay.assert_called_once_with(
            str(contribution.ID), recons_type="all", recheck=True
        )


class TheAdminShowsTheNewState(TestCase):
    def setUp(self):
        self.client.force_login(_superuser())

    def test_the_image_page_shows_when_it_was_moderated(self):
        self.assertIn("safety_moderated_at", tadmin.ImageAdmin.readonly_fields)
        site = _site()
        contributor = Contributor.objects.create(name="A", email="a@example.org")
        contribution = Contribution.objects.create(site=site, contributor=contributor)
        image = Image.objects.create(contribution=contribution, image="img/a.jpg")
        response = self.client.get(
            reverse("admin:tirtha_image_change", args=[image.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "safety_moderated_at")

    def test_the_site_page_shows_the_scheduler_state_read_only(self):
        for field in (
            "recon_queued_at",
            "recon_failure_state",
            "recon_failures_reset_at",
        ):
            self.assertIn(field, tadmin.SiteAdmin.readonly_fields)
        site = _site()
        for _ in range(3):
            Run.objects.create(site=site, kind="GS", status="Error")
        response = self.client.get(reverse("admin:tirtha_site_change", args=[site.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Consecutive failed runs")
        self.assertContains(response, "backed off")


class ResettingTheBackOff(TestCase):
    def test_it_sets_the_reset_instant_and_the_scheduler_honours_it(self):
        from tirtha.scheduler import consecutive_failures

        site = _site()
        for _ in range(3):
            Run.objects.create(site=site, kind="GS", status="Error")
        self.assertEqual(consecutive_failures(site), 3)

        before = timezone.now()
        model_admin = tadmin.SiteAdmin(Site, AdminSite())
        model_admin.reset_recon_backoff(
            _request(_superuser()), Site.objects.filter(pk=site.pk)
        )
        site.refresh_from_db()
        self.assertGreaterEqual(site.recon_failures_reset_at, before)
        self.assertEqual(consecutive_failures(site), 0)

    def test_it_needs_the_change_permission(self):
        self.assertEqual(
            list(tadmin.SiteAdmin.reset_recon_backoff.allowed_permissions), ["change"]
        )
        model_admin = tadmin.SiteAdmin(Site, AdminSite())
        user = get_user_model().objects.create_user(
            "viewer", "v@example.org", "pw", is_staff=True
        )
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tirtha", codename="view_site"
            )
        )
        user = get_user_model().objects.get(pk=user.pk)
        request = RequestFactory().get("/")
        request.user = user
        self.assertNotIn("reset_recon_backoff", model_admin.get_actions(request))


class DeletingAContributorWithContributions(TestCase):
    """
    `Contribution.contributor` is PROTECT now. The admin must answer that with
    Django's "cannot delete" page, not a 500 from an uncaught ProtectedError.
    """

    def setUp(self):
        self.client.force_login(_superuser())
        self.contributor = Contributor.objects.create(name="A", email="a@example.org")
        Contribution.objects.create(site=_site(), contributor=self.contributor)

    def test_the_delete_page_explains_instead_of_crashing(self):
        url = reverse("admin:tirtha_contributor_delete", args=[self.contributor.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "would require deleting the following protected")

        response = self.client.post(url, {"post": "yes"})
        self.assertIn(response.status_code, (200, 403))
        self.assertTrue(Contributor.objects.filter(pk=self.contributor.pk).exists())

    def test_the_bulk_action_explains_instead_of_crashing(self):
        response = self.client.post(
            reverse("admin:tirtha_contributor_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [self.contributor.pk],
                "post": "yes",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "protected")
        self.assertTrue(Contributor.objects.filter(pk=self.contributor.pk).exists())

    def test_a_contributor_with_nothing_contributed_still_deletes(self):
        loner = Contributor.objects.create(name="B", email="b@example.org")
        response = self.client.post(
            reverse("admin:tirtha_contributor_delete", args=[loner.pk]),
            {"post": "yes"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Contributor.objects.filter(pk=loner.pk).exists())


class AFailedRerunKeepsALiveSiteLive(TestCase):
    """
    `_handle_error` wrote Error to the site unconditionally, so one failed
    nightly re-run took a site with a published, served model off the map.
    """

    def _ops(self, site, run):
        from tirtha.workers import GSOps

        ops = GSOps.__new__(GSOps)
        ops.logger = logging.getLogger("test-handle-error")
        ops.site = site
        ops.siteStr = str(site.ID)
        ops.run = run
        ops.runID = run.ID
        ops.kind = "GS"
        ops.contribution = None
        ops.contrib_id = None
        # The notification lookups are not what is under test.
        ops._resolve_contribution_details = lambda: ("Unknown", "Unknown")
        return ops

    def _fail(self, site, run):
        ops = self._ops(site, run)
        with (
            patch("tirtha.email_utils.send_reconstruction_failure_email"),
            patch("tirtha.email_utils.send_contributor_run_failed_email"),
        ):
            with self.assertRaises(RuntimeError):
                ops._handle_error(RuntimeError("boom"), "run_frgs")
        run.refresh_from_db()
        site.refresh_from_db()
        return ops

    def _published(self, site, **kw):
        ark = ARK.objects.create(
            ark="99999/t1",
            naan="99999",
            shoulder="/",
            assigned_name="t1",
            url="https://example.org/static/models/x/published/x.splat",
            metadata={},
        )
        return Run.objects.create(
            site=site, kind="GS", status="Archived", ark=ark, **kw
        )

    def test_a_site_with_a_published_run_stays_live(self):
        site = _site(status="Processing")
        self._published(site)
        run = Run.objects.create(site=site, kind="GS", status="Processing")
        ops = self._fail(site, run)
        self.assertEqual(run.status, "Error")
        self.assertEqual(site.status, "Live")
        self.assertEqual(ops.site.status, "Live")

    def test_a_site_with_nothing_published_is_still_error(self):
        site = _site(status="Processing")
        run = Run.objects.create(site=site, kind="GS", status="Processing")
        self._fail(site, run)
        self.assertEqual(run.status, "Error")
        self.assertEqual(site.status, "Error")

    def test_a_hidden_published_run_does_not_count(self):
        site = _site(status="Processing")
        self._published(site, hidden=True)
        run = Run.objects.create(site=site, kind="GS", status="Processing")
        self._fail(site, run)
        self.assertEqual(site.status, "Error")
