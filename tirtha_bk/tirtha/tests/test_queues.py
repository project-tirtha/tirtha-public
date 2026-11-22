"""
Tests for the queue split (internal design notes).

One reconstruction takes essentially the whole machine, so `recon` runs one job
at a time across the entire system. `ingest` is CPU-only since 5.1 moved NSFW to
ONNX, so it never contends for the GPU and can run concurrently.

This is what replaces the single --concurrency=1 worker shared by everything,
which forced image checks to inherit the reconstruction bottleneck -- the
condition behind issue #37.
"""

from django.test import TestCase

from tirtha.tasks import (
    QUEUE_INGEST,
    QUEUE_RECON,
    backup_task,
    db_cleanup_task,
    post_save_contrib_imageops,
    recon_runner_task,
)


class RoutingTests(TestCase):
    def test_image_checks_go_to_ingest(self):
        self.assertEqual(post_save_contrib_imageops.queue, QUEUE_INGEST)

    def test_reconstruction_goes_to_recon(self):
        # The GPU queue, concurrency 1, globally.
        self.assertEqual(recon_runner_task.queue, QUEUE_RECON)

    def test_housekeeping_does_not_occupy_the_gpu_queue(self):
        # A nightly backup blocking a reconstruction, or waiting behind one,
        # would be absurd in both directions.
        self.assertEqual(backup_task.queue, QUEUE_INGEST)
        self.assertEqual(db_cleanup_task.queue, QUEUE_INGEST)

    def test_the_two_queues_are_distinct(self):
        self.assertNotEqual(QUEUE_INGEST, QUEUE_RECON)

    def test_every_task_declares_a_queue(self):
        # An unrouted task lands on the default queue by accident rather than
        # by decision; on a GPU box that is how something ends up sharing the
        # reconstruction worker again.
        for task in (
            post_save_contrib_imageops,
            recon_runner_task,
            backup_task,
            db_cleanup_task,
        ):
            with self.subTest(task=task.name):
                self.assertIn(task.queue, {QUEUE_INGEST, QUEUE_RECON})


class SettingsTests(TestCase):
    def test_missing_queues_are_created_rather_than_swallowing_tasks(self):
        from django.conf import settings

        # Without this, a task routed to a queue no worker consumes disappears
        # silently instead of failing loudly.
        self.assertTrue(getattr(settings, "CELERY_TASK_CREATE_MISSING_QUEUES", False))

    def test_the_default_queue_is_the_cpu_one(self):
        from django.conf import settings

        # If a new task forgets to declare a queue, landing on ingest is the
        # safe mistake; landing on recon would block reconstructions.
        self.assertEqual(settings.CELERY_TASK_DEFAULT_QUEUE, QUEUE_INGEST)
