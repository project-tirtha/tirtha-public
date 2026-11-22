"""
A run whose worker died must not wedge its site for ever.

`_handle_error` moves a failed run to Error, and only runs when the exception
reaches it. A reboot, a `systemctl restart`, an OOM, or a `colmap` missing from
the unit's PATH all kill the process first -- and the row keeps saying
`Processing`, as does the Site. `prerun_check` then refuses to start anything
for that site, so contributors keep uploading and nothing is ever reconstructed
again. It happened twice in one day.
"""

from __future__ import annotations

from types import SimpleNamespace

from django.test import TestCase

from tirtha.celery import reconcile_orphaned_runs
from tirtha.models import Run, Site


class TheStartupSweep(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="S",
            country="India",
            state="Odisha",
            district="Khordha",
            status="Processing",
        )
        self.run = Run.objects.create(site=self.site, kind="GS", status="Processing")

    def _fire(self, hostname):
        reconcile_orphaned_runs(sender=SimpleNamespace(hostname=hostname))

    def test_the_recon_worker_releases_an_orphaned_run(self):
        self._fire("recon@host")
        self.run.refresh_from_db()
        self.site.refresh_from_db()
        self.assertEqual(self.run.status, "Error")
        self.assertEqual(self.site.status, "Pending")
        self.assertIsNotNone(self.run.ended_at)

    def test_it_says_why_in_the_notes(self):
        # An operator reading the run later needs to know this was a sweep and
        # not a reconstruction that genuinely failed.
        self._fire("recon@host")
        self.run.refresh_from_db()
        self.assertIn("startup sweep", self.run.notes.lower())

    def test_the_ingest_worker_does_not_sweep(self):
        """
        The distinction that makes this safe.

        `recon` is one slot across the machine, so a recon worker that has just
        started knows nothing is in flight. `ingest` restarts independently and a
        reconstruction may genuinely be running while it does -- sweeping from
        there would kill a live run.
        """
        self._fire("ingest@host")
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "Processing")

    def test_a_finished_run_is_left_alone(self):
        done = Run.objects.create(site=self.site, kind="GS", status="Archived")
        self._fire("recon@host")
        done.refresh_from_db()
        self.assertEqual(done.status, "Archived")
