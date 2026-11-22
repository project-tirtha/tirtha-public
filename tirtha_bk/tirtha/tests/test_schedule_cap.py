"""
One nightly pass must not enqueue more than the queue can run.

`recon` is a single slot across the machine and a reconstruction is hours, so
anything queued beyond a night's work is a backlog that cannot be reordered:
the queue is FIFO and the candidate list is deliberately oldest-first.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase, override_settings

from tirtha.scheduler import Candidate
from tirtha.tasks import schedule_reconstructions_task


def _candidates(n):
    return [
        Candidate(
            site=type("S", (), {"name": f"site-{i}"})(),
            contribution_id=f"c{i}",
            pending_images=50,
            waiting_days=float(100 - i),
            reason="test",
        )
        for i in range(n)
    ]


class TheNightlyPassIsCapped(TestCase):
    def _run(self, count):
        with (
            patch(
                "tirtha.scheduler.sites_awaiting_reconstruction",
                return_value=_candidates(count),
            ),
            patch("tirtha.tasks.recon_runner_task.apply_async") as enqueue,
        ):
            schedule_reconstructions_task()
        return enqueue

    @override_settings(RECON_MAX_ENQUEUE_PER_RUN=2)
    def test_only_the_cap_is_enqueued(self):
        enqueue = self._run(9)
        self.assertEqual(enqueue.call_count, 2)

    @override_settings(RECON_MAX_ENQUEUE_PER_RUN=2)
    def test_the_oldest_waiting_are_the_ones_taken(self):
        enqueue = self._run(9)
        taken = [c.kwargs["args"][0] for c in enqueue.call_args_list]
        self.assertEqual(taken, ["c0", "c1"])

    @override_settings(RECON_MAX_ENQUEUE_PER_RUN=2)
    def test_fewer_candidates_than_the_cap_all_run(self):
        self.assertEqual(self._run(1).call_count, 1)

    @override_settings(RECON_MAX_ENQUEUE_PER_RUN=0)
    def test_a_zero_cap_means_no_limit(self):
        self.assertEqual(self._run(9).call_count, 9)

    def test_the_default_is_a_small_number_not_unlimited(self):
        """A deployment that configures nothing must still be bounded."""
        self.assertEqual(self._run(9).call_count, 2)
