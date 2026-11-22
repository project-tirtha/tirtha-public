"""
Tests for RunStage wiring (internal design notes).

2.8 built the model; this is the wiring. It sits in `_run_all` rather than in
each stage method, so one place covers both pipelines and a new stage is
tracked simply by appearing in `_run_order`.

The property that matters most is the one that is easy to get wrong: stage
recording is *observability*, and must never be able to fail a reconstruction.
Losing hours of GPU work because a bookkeeping row could not be written would
invert the priority entirely.
"""

from unittest.mock import patch

from django.test import TestCase

from tirtha.models import Run, RunStage, Site
from tirtha.workers import BaseOps, RunCancelledError


class _Recorder(BaseOps):
    """A BaseOps with the constructor bypassed -- no files, no GPU, no Run setup."""

    def __init__(self, run, order, behaviour=None):
        self.run = run
        self._run_order = order
        self._stage_metrics = {}
        self._stage_artifacts = []
        self._behaviour = behaviour or {}
        self.calls = []
        self.logger = type(
            "L",
            (),
            {
                "info": lambda *a, **k: None,
                "warning": lambda *a, **k: None,
                "error": lambda *a, **k: None,
                "_log_file": "/tmp/x.log",
            },
        )()

    def __getattr__(self, name):
        # Stage methods are looked up by name from _run_order.
        if name.startswith("run_") or name.startswith("stage_"):

            def _step():
                self.calls.append(name)
                action = self._behaviour.get(name)
                if action:
                    action(self)

            return _step
        raise AttributeError(name)

    def _handle_error(self, excep, caller):
        self.handled = (excep, caller)


class _Base(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.run = Run.objects.create(site=self.site, kind="GS")


class StageRecordingTests(_Base):
    def test_every_step_gets_a_stage_row(self):
        ops = _Recorder(self.run, ["stage_a", "stage_b", "stage_c"])
        ops._run_all()
        self.assertEqual(RunStage.objects.filter(run=self.run).count(), 3)

    def test_stages_are_ordered_by_execution(self):
        ops = _Recorder(self.run, ["stage_a", "stage_b"])
        ops._run_all()
        rows = RunStage.objects.filter(run=self.run).order_by("ordinal")
        self.assertEqual([r.name for r in rows], ["stage_a", "stage_b"])
        self.assertEqual([r.ordinal for r in rows], [0, 1])

    def test_a_completed_stage_is_timed(self):
        _Recorder(self.run, ["stage_a"])._run_all()
        stage = RunStage.objects.get(run=self.run)
        self.assertEqual(stage.status, "Completed")
        self.assertIsNotNone(stage.duration_s)
        self.assertIsNotNone(stage.started_at)
        self.assertIsNotNone(stage.ended_at)

    def test_metrics_recorded_by_a_stage_land_on_its_row(self):
        def measure(ops):
            ops.record_stage_metrics(registered_images=281, images=297)

        ops = _Recorder(self.run, ["stage_a"], {"stage_a": measure})
        ops._run_all()
        stage = RunStage.objects.get(run=self.run)
        self.assertEqual(stage.metrics["registered_images"], 281)

    def test_metrics_do_not_leak_between_stages(self):
        def measure(ops):
            ops.record_stage_metrics(only_here=1)

        ops = _Recorder(self.run, ["stage_a", "stage_b"], {"stage_a": measure})
        ops._run_all()
        second = RunStage.objects.get(run=self.run, name="stage_b")
        self.assertEqual(second.metrics, {})

    def test_declared_artifacts_are_recorded_for_resume(self):
        def declare(ops):
            ops.record_stage_artifacts("/out/sparse/0")

        ops = _Recorder(self.run, ["stage_a"], {"stage_a": declare})
        ops._run_all()
        self.assertEqual(
            RunStage.objects.get(run=self.run).artifacts, ["/out/sparse/0"]
        )


class FailureTests(_Base):
    def test_a_failing_stage_is_marked_failed(self):
        def boom(ops):
            raise RuntimeError("CUDA out of memory")

        ops = _Recorder(self.run, ["stage_a"], {"stage_a": boom})
        ops._run_all()
        self.assertEqual(RunStage.objects.get(run=self.run).status, "Failed")

    def test_stages_after_a_failure_are_not_started(self):
        def boom(ops):
            raise RuntimeError("boom")

        ops = _Recorder(self.run, ["stage_a", "stage_b"], {"stage_a": boom})
        ops._run_all()
        self.assertEqual(RunStage.objects.filter(run=self.run).count(), 1)

    def test_the_failure_still_reaches_the_error_handler(self):
        # Recording must not swallow the failure -- the run has to error.
        def boom(ops):
            raise RuntimeError("boom")

        ops = _Recorder(self.run, ["stage_a"], {"stage_a": boom})
        ops._run_all()
        self.assertEqual(ops.handled[1], "_run_all")

    def test_a_cancelled_run_leaves_its_stage_marked_failed_not_completed(self):
        def cancel(ops):
            raise RunCancelledError("too few registered images")

        ops = _Recorder(self.run, ["stage_a"], {"stage_a": cancel})
        ops._run_all()
        self.assertEqual(RunStage.objects.get(run=self.run).status, "Failed")


class ObservabilityNeverBreaksTheRunTests(_Base):
    """
    Stage recording is observability. Losing a reconstruction because a
    bookkeeping row could not be written would invert the priority entirely.
    """

    def test_the_pipeline_runs_even_if_the_stage_row_cannot_be_created(self):
        ops = _Recorder(self.run, ["stage_a", "stage_b"])
        with patch(
            "tirtha.models.RunStage.objects.create",
            side_effect=RuntimeError("db down"),
        ):
            ops._run_all()
        self.assertEqual(ops.calls, ["stage_a", "stage_b"])
        self.assertFalse(hasattr(ops, "handled"))

    def test_the_pipeline_runs_even_if_the_stage_row_cannot_be_closed(self):
        ops = _Recorder(self.run, ["stage_a"])
        with patch(
            "tirtha.models.RunStage.finish", side_effect=RuntimeError("db down")
        ):
            ops._run_all()
        self.assertEqual(ops.calls, ["stage_a"])
        self.assertFalse(hasattr(ops, "handled"))
