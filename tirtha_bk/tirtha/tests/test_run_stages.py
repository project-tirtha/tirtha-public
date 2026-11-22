"""
Tests for RunStage (internal design notes).

Today "which stage failed and how long did the others take" is answerable only
by reading rotating log files. These pin the properties that make it a query
instead: a stage belongs to exactly one owner, a retry does not erase the
failure it retried, and resume trusts the filesystem as well as the row.
"""

import os
import tempfile

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from tirtha.models import Contribution, Contributor, Run, RunStage, Site


class _Base(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="Konark Sun", country="India", state="Odisha", district="Puri"
        )
        self.contributor = Contributor.objects.create(name="T", email="t@example.org")
        self.contribution = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )
        self.run = Run.objects.create(site=self.site, kind="GS")


class ScopeTests(_Base):
    def test_a_reconstruction_stage_hangs_off_the_run(self):
        stage = RunStage.objects.create(run=self.run, name="mapper", ordinal=3)
        self.assertEqual(stage.scope, RunStage.Scope.RECON)
        self.assertEqual(stage.owner, self.run)
        self.assertIn(stage, self.run.stages.all())

    def test_an_ingest_stage_hangs_off_the_contribution(self):
        # Ingest runs per contribution and in parallel; reconstruction runs per
        # Run and serialised (internal design notes). One model, different FK.
        stage = RunStage.objects.create(
            contribution=self.contribution, name="nsfw", ordinal=1
        )
        self.assertEqual(stage.scope, RunStage.Scope.INGEST)
        self.assertEqual(stage.owner, self.contribution)
        self.assertIn(stage, self.contribution.stages.all())

    def test_a_stage_cannot_belong_to_both(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            RunStage.objects.create(
                run=self.run, contribution=self.contribution, name="confused"
            )

    def test_a_stage_cannot_belong_to_neither(self):
        # An unattached stage row is not a partial record, it is an
        # unanswerable one.
        with self.assertRaises(IntegrityError), transaction.atomic():
            RunStage.objects.create(name="orphan")


class UniquenessTests(_Base):
    def test_one_row_per_stage_attempt_within_a_run(self):
        RunStage.objects.create(run=self.run, name="mapper")
        with self.assertRaises(IntegrityError), transaction.atomic():
            RunStage.objects.create(run=self.run, name="mapper")

    def test_a_retry_is_a_new_row_not_an_overwrite(self):
        # The point of the table is that a failure stays on the record.
        first = RunStage.objects.create(run=self.run, name="mapper", attempt=1)
        first.finish(RunStage.Status.FAILED, exit_code=1)
        RunStage.objects.create(run=self.run, name="mapper", attempt=2).finish(
            RunStage.Status.COMPLETED, exit_code=0
        )
        rows = RunStage.objects.filter(run=self.run, name="mapper").order_by("attempt")
        self.assertEqual([r.status for r in rows], ["Failed", "Completed"])

    def test_the_same_stage_name_may_repeat_across_runs(self):
        other = Run.objects.create(site=self.site, kind="GS")
        RunStage.objects.create(run=self.run, name="mapper")
        RunStage.objects.create(run=other, name="mapper")
        self.assertEqual(RunStage.objects.filter(name="mapper").count(), 2)

    def test_run_and_contribution_scopes_do_not_collide(self):
        RunStage.objects.create(run=self.run, name="exif")
        RunStage.objects.create(contribution=self.contribution, name="exif")
        self.assertEqual(RunStage.objects.filter(name="exif").count(), 2)


class LifecycleTests(_Base):
    def test_stages_begin_pending_and_untimed(self):
        stage = RunStage.objects.create(run=self.run, name="feature_extractor")
        self.assertEqual(stage.status, RunStage.Status.PENDING)
        self.assertIsNone(stage.started_at)
        self.assertIsNone(stage.duration_s)

    def test_start_records_the_exact_command(self):
        # So a failed stage can be re-run by hand without reconstructing the
        # invocation from settings.
        stage = RunStage.objects.create(run=self.run, name="mapper")
        stage.start(command="colmap mapper --database_path db", log_path="/l/m.log")
        stage.refresh_from_db()
        self.assertEqual(stage.status, RunStage.Status.RUNNING)
        self.assertIsNotNone(stage.started_at)
        self.assertIn("colmap mapper", stage.command)
        self.assertEqual(stage.log_path, "/l/m.log")

    def test_finish_computes_duration_from_the_timestamps(self):
        stage = RunStage.objects.create(run=self.run, name="mapper").start()
        stage.finish(RunStage.Status.COMPLETED, exit_code=0)
        stage.refresh_from_db()
        self.assertIsNotNone(stage.ended_at)
        self.assertIsNotNone(stage.duration_s)
        self.assertGreaterEqual(stage.duration_s, 0)

    def test_a_self_reported_duration_is_not_overwritten(self):
        # A stage that measures itself (FRGS training time) is more accurate
        # than the wall clock around the subprocess.
        stage = RunStage.objects.create(run=self.run, name="frgs").start()
        stage.duration_s = 4321.0
        stage.finish(RunStage.Status.COMPLETED)
        stage.refresh_from_db()
        self.assertEqual(stage.duration_s, 4321.0)

    def test_failure_keeps_the_exit_code_and_the_reason(self):
        stage = RunStage.objects.create(run=self.run, name="mapper").start()
        stage.finish(RunStage.Status.FAILED, exit_code=137, notes="OOM-killed")
        stage.refresh_from_db()
        self.assertEqual(stage.exit_code, 137)
        self.assertIn("OOM", stage.notes)

    def test_metrics_merge_rather_than_replace(self):
        stage = RunStage.objects.create(
            run=self.run, name="mapper", metrics={"registered": 280}
        )
        stage.finish(RunStage.Status.COMPLETED, metrics={"mean_reproj_err": 0.71})
        stage.refresh_from_db()
        self.assertEqual(stage.metrics["registered"], 280)
        self.assertEqual(stage.metrics["mean_reproj_err"], 0.71)

    def test_ordering_follows_execution_order(self):
        for name, ordinal in [("mapper", 3), ("extract", 1), ("match", 2)]:
            RunStage.objects.create(run=self.run, name=name, ordinal=ordinal)
        self.assertEqual(
            [s.name for s in self.run.stages.all()], ["extract", "match", "mapper"]
        )


class ResumeTests(_Base):
    def test_completed_with_its_artifacts_present_is_skippable(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sparse")
            os.makedirs(path)
            stage = RunStage.objects.create(
                run=self.run,
                name="mapper",
                status=RunStage.Status.COMPLETED,
                artifacts=[path],
            )
            self.assertTrue(stage.resumable_complete())

    def test_completed_but_missing_its_artifacts_is_not_skippable(self):
        # A row can outlive the files it describes -- archived or hand-cleaned
        # run directories. Resuming on top of missing inputs fails later and
        # more confusingly than re-running the stage (internal design notes).
        stage = RunStage.objects.create(
            run=self.run,
            name="mapper",
            status=RunStage.Status.COMPLETED,
            artifacts=["/nonexistent/sparse"],
        )
        self.assertFalse(stage.resumable_complete())

    def test_an_unfinished_stage_is_never_skippable(self):
        for status in (
            RunStage.Status.PENDING,
            RunStage.Status.RUNNING,
            RunStage.Status.FAILED,
            RunStage.Status.CANCELLED,
        ):
            with self.subTest(status=status):
                stage = RunStage(run=self.run, name="x", status=status, artifacts=[])
                self.assertFalse(stage.resumable_complete())

    def test_a_completed_stage_declaring_nothing_is_skippable(self):
        # Vacuous truth, and the right answer: stages with no on-disk output
        # (a validation check) have nothing to verify.
        stage = RunStage(
            run=self.run, name="validate", status=RunStage.Status.COMPLETED
        )
        self.assertTrue(stage.resumable_complete())


class DeletionTests(_Base):
    def test_deleting_a_run_takes_its_stages(self):
        # Stages describe one execution; they are meaningless without it. The
        # ARK rule protects successful Runs from deletion (internal design notes) --
        # it is not this table's job to keep orphans alive.
        RunStage.objects.create(run=self.run, name="mapper")
        self.run.delete()
        self.assertEqual(RunStage.objects.count(), 0)

    def test_deleting_a_contribution_takes_its_ingest_stages(self):
        RunStage.objects.create(contribution=self.contribution, name="nsfw")
        self.contribution.delete()
        self.assertEqual(RunStage.objects.count(), 0)


class ValidationTests(_Base):
    def test_status_choices_are_enforced_by_full_clean(self):
        stage = RunStage(run=self.run, name="mapper", status="Halfway")
        with self.assertRaises(ValidationError):
            stage.full_clean()


class AdminSurfaceTests(_Base):
    """
    The Run changelist is where an operator looks first, so what it shows about
    a run's progress is part of the feature, not decoration (internal design notes).
    """

    def _admin(self):
        from django.contrib.admin.sites import site as admin_site

        return admin_site._registry[Run]

    def test_runs_without_stage_rows_render_a_placeholder(self):
        # Every existing run predates the wiring in 5.6.
        self.assertEqual(self._admin().progress(self.run), "--")

    def test_progress_counts_completed_stages(self):
        for i, status in enumerate(
            [
                RunStage.Status.COMPLETED,
                RunStage.Status.COMPLETED,
                RunStage.Status.PENDING,
            ]
        ):
            RunStage.objects.create(
                run=self.run, name=f"s{i}", ordinal=i, status=status
            )
        self.assertEqual(self._admin().progress(self.run), "2/3")

    def test_a_skipped_stage_counts_as_gotten_past(self):
        RunStage.objects.create(
            run=self.run, name="a", ordinal=0, status=RunStage.Status.SKIPPED
        )
        RunStage.objects.create(
            run=self.run, name="b", ordinal=1, status=RunStage.Status.PENDING
        )
        self.assertEqual(self._admin().progress(self.run), "1/2")

    def test_a_running_run_names_the_stage_it_is_in(self):
        # "Processing" alone cannot distinguish three minutes in from wedged.
        RunStage.objects.create(
            run=self.run, name="extract", ordinal=0, status=RunStage.Status.COMPLETED
        )
        RunStage.objects.create(
            run=self.run, name="mapper", ordinal=1, status=RunStage.Status.RUNNING
        )
        self.assertEqual(self._admin().progress(self.run), "1/2 -- mapper")

    def test_a_failed_run_names_the_stage_that_failed(self):
        RunStage.objects.create(
            run=self.run, name="extract", ordinal=0, status=RunStage.Status.COMPLETED
        )
        RunStage.objects.create(
            run=self.run, name="mapper", ordinal=1, status=RunStage.Status.FAILED
        )
        self.assertIn("failed at mapper", self._admin().progress(self.run))

    def test_stage_rows_cannot_be_edited_from_the_admin(self):
        # These rows are the record of what the pipeline did; an operator
        # editing them turns the audit trail into a guess.
        from django.contrib.admin.sites import site as admin_site

        stage_admin = admin_site._registry[RunStage]
        self.assertFalse(stage_admin.has_add_permission(None))
        self.assertFalse(stage_admin.has_change_permission(None))
