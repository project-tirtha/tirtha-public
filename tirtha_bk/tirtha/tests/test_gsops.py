"""
Tests for the GS pipeline after nerfstudio (internal design notes).

Nothing here runs COLMAP or FRGS -- both are subprocesses taking hours on a
GPU. What is checked is the wiring that broke when nerfstudio came out: which
images are fed in, that quarantined content cannot reach a reconstruction, and
that a run COLMAP could not register is cancelled rather than published.
"""

import inspect

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase

from tirtha import workers
from tirtha.models import Contribution, Contributor, Image, Site
from tirtha.workers import GSOps, MeshOps, prerun_check


class ImageSelectionTests(TestCase):
    """
    `prerun_check` counted `os.listdir(images/good)`. That folder stopped
    existing in 5.1, so the count is now a query -- and it has to mean
    "images we may reconstruct from", not "files on disk".
    """

    def setUp(self):
        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.contributor = Contributor.objects.create(name="T", email="t@example.org")
        self.contribution = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )

    def _images(self, n, status):
        for i in range(n):
            Image.objects.create(
                contribution=self.contribution,
                image=SimpleUploadedFile(
                    f"{status}{i}.jpg", b"x", content_type="image/jpeg"
                ),
                safety_status=status,
            )

    def test_safe_images_count_towards_the_minimum(self):
        self._images(30, "safe")
        ok, message = prerun_check(str(self.contribution.ID), "GS")
        self.assertTrue(ok, message)

    def test_rejected_images_do_not_count(self):
        # Withheld content must not make a site look ready to reconstruct.
        self._images(30, "rejected")
        ok, message = prerun_check(str(self.contribution.ID), "GS")
        self.assertFalse(ok)
        self.assertIn("images", message.lower())

    def test_unchecked_images_do_not_count(self):
        # An unchecked image is not publishable, so it cannot contribute to
        # readiness either (internal design notes).
        self._images(30, "pending")
        ok, _ = prerun_check(str(self.contribution.ID), "GS")
        self.assertFalse(ok)

    def test_a_mixed_set_counts_only_the_safe_ones(self):
        self._images(3, "safe")
        self._images(30, "rejected")
        ok, _ = prerun_check(str(self.contribution.ID), "GS")
        self.assertFalse(ok)


class ColmapValidationTests(TestCase):
    """
    A run COLMAP could not register is cancelled, not published.

    This used to regex-scrape a nerfstudio log for "Colmap matched N images".
    It now reads model_analyzer's numbers, so a nerfstudio release rewording
    its output cannot silently disable the check.
    """

    def _validator(self, stats):
        from tirtha.workers import GSOps

        cancelled = {}

        def fake_cancel(reason, caller, log_excerpt=None, log_file_path=None):
            cancelled["reason"] = reason

        ops = GSOps.__new__(GSOps)
        ops._cancel_run = fake_cancel
        ops.logger = type(
            "L", (), {"warning": lambda *a, **k: None, "info": lambda *a, **k: None}
        )()
        GSOps._validate_colmap_registration(ops, stats, log_path=None)
        return cancelled

    def test_a_healthy_registration_is_not_cancelled(self):
        self.assertEqual(self._validator({"images": 300, "registered_images": 281}), {})

    def test_a_poor_registration_rate_cancels_the_run(self):
        cancelled = self._validator({"images": 300, "registered_images": 10})
        self.assertIn("Cancelling run", cancelled["reason"])
        self.assertIn("300", cancelled["reason"])

    def test_too_few_registered_images_cancels_the_run(self):
        cancelled = self._validator({"images": 6, "registered_images": 2})
        self.assertIn("Cancelling run", cancelled["reason"])

    def test_missing_statistics_do_not_cancel_a_run(self):
        # Absent numbers mean "unknown", not "bad". Cancelling on a parse
        # failure would throw away hours of GPU work over a log format change.
        self.assertEqual(self._validator({}), {})


class PipelineShapeTests(TestCase):
    """
    The nerfstudio stages are gone as *methods*, not merely as strings.

    An earlier version of this test grepped the class source for "ns-train",
    which failed on the docstring explaining what FRGS replaced -- the comment
    is worth keeping, so the test checks structure instead.
    """

    def test_the_nerfstudio_stages_no_longer_exist(self):
        from tirtha.workers import GSOps

        self.assertFalse(hasattr(GSOps, "run_splatfacto"))
        # The log-scraping validator went with it: model_analyzer reports the
        # same facts as numbers.
        self.assertFalse(hasattr(GSOps, "_validate_colmap_matches"))

    def test_the_replacement_stages_exist(self):
        from tirtha.workers import GSOps

        self.assertTrue(callable(GSOps.run_colmap))
        self.assertTrue(callable(GSOps.run_frgs))
        self.assertTrue(callable(GSOps._validate_colmap_registration))

    def test_postprocess_reads_the_file_frgs_actually_writes(self):
        # FRGS writes reconstruction.ply; splat.ply was ns-export's output, and
        # leaving that path would have failed only at the end of a GPU run.
        import inspect

        from tirtha.workers import GSOps

        source = inspect.getsource(GSOps.run_postprocess)
        self.assertIn("reconstruction.ply", source)
        self.assertNotIn('"output/splat.ply"', source)


class OpsRunnerConstruction(SimpleTestCase):
    """
    `ops_runner` can actually build the class it dispatches to.

    It could not. `op = OP(siteID=siteID)` was called against
    `MeshOps.__init__(self, siteID, contrib_id)` and
    `GSOps.__init__(self, siteID, contrib_id)`, neither of which defaults
    `contrib_id` -- only `BaseOps` does. So every automatic reconstruction
    raised `TypeError: __init__() missing 1 required positional argument`
    on its first statement.

    Nothing recorded that. `_handle_error`, which writes `status="Error"`,
    is a method on the instance that was never constructed, and the outer
    handler (workers.py:1469) only prints and emails before re-raising. The
    run therefore died leaving no Run row at all -- which is why the archive
    contains zero runs with status 'Error' despite the pipeline having been
    triggered, and why four sites show as awaiting a run rather than failed.

    Asserting the call binds is the whole test: it is a signature mismatch,
    and reaching it for real would start a reconstruction on the shared GPU.
    """

    def test_the_dispatch_call_binds_to_both_ops_signatures(self):
        from inspect import signature

        for kind, cls in {"aV": MeshOps, "GS": GSOps}.items():
            with self.subTest(kind=kind):
                # Exactly the call ops_runner makes.
                signature(cls.__init__).bind(
                    self, siteID="site-id", contrib_id="contrib-id"
                )

    def test_ops_runner_passes_the_contribution_it_was_given(self):
        source = inspect.getsource(workers.ops_runner)
        self.assertIn("OP(siteID=siteID, contrib_id=contrib_id)", source)
        self.assertNotIn("OP(siteID=siteID)\n", source)


class ReuseBranchLeavesTheModelSet(SimpleTestCase):
    """
    The "nothing new, reuse the model" path has to name the model it reused.

    `run_colmap` assigns `self.colmapModel` once, after the early return this
    branch takes, and `run_frgs` reads it unconditionally. So the shortcut
    internal design notes added specifically so that a run with no new images skips
    hours of SfM instead raised `AttributeError: 'GSOps' object has no
    attribute 'colmapModel'` -- and it is the path taken on an operator
    re-trigger and on a retry after FRGS died with COLMAP already done, which
    is exactly when skipping SfM is the point.

    Read from the source: reaching the branch for real needs COLMAP, a
    populated workspace and hours on the shared GPU.
    """

    def test_the_early_return_is_preceded_by_the_assignment(self):
        source = inspect.getsource(workers.GSOps.run_colmap)
        branch = source[source.index("Nothing new.") :]
        # Everything up to the branch's own `return`.
        branch = branch[: branch.index("\n            return")]
        self.assertIn("self.colmapModel = previous_model", branch)

    def test_every_branch_that_returns_names_a_model_first(self):
        """
        The general form of the bug, checked properly.

        This counted `self.colmapModel =` and asserted "at least two", under a
        docstring promising "any return that leaves it unset" -- a claim the
        assertion could not support. Two assignments and three returns would
        have passed.

        Walking the AST answers the actual question: for each `return` in the
        function body, has an assignment to `self.colmapModel` appeared before
        it? Reaching this for real needs COLMAP and hours on the shared GPU,
        which is why it is read from the source rather than executed.
        """
        import ast

        tree = ast.parse(inspect.getsource(workers.GSOps.run_colmap).strip())
        function = tree.body[0]

        assigned_by = []
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == "colmapModel"
                    ):
                        assigned_by.append(node.lineno)

        self.assertTrue(assigned_by, "run_colmap never sets colmapModel at all")

        for node in ast.walk(function):
            if isinstance(node, ast.Return):
                self.assertTrue(
                    any(line < node.lineno for line in assigned_by),
                    f"the return at line {node.lineno} of run_colmap leaves "
                    "colmapModel unset, which is an AttributeError in run_frgs",
                )
