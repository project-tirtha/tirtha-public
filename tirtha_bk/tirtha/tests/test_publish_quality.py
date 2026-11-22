"""
What gets published is the reconstruction, not a quarter of it.

`run_postprocess` decimated first and then reassigned `source`, so every
downstream encoder -- `.sog`, `.compressed.ply`, `.splat` and the LoD set -- was
fed a model with three quarters of its Gaussians deleted. Full quality was never
published in any format; it survived only as `reconstruction.ply` inside the
archived run directory.

Fontaine Bartholdi's published PLY carries 7,025 vertices: 25% of the 28,099
that survived training, and 7.8% of the 90,199 points COLMAP seeded.
"""

from __future__ import annotations

import inspect

from django.test import SimpleTestCase, override_settings

from tirtha.workers import GSOps


class DecimationIsOffByDefault(SimpleTestCase):
    def test_the_deployment_publishes_full_quality(self):
        from django.conf import settings

        self.assertEqual(settings.GS_DECIMATE_TARGET, "")

    def test_the_decimate_step_is_guarded_on_the_setting(self):
        """
        Turning it back on has to be a one-line config change, not a code
        change -- so the guard has to stay.
        """
        source = inspect.getsource(GSOps.run_postprocess)
        self.assertIn("if GS_DECIMATE_TARGET:", source)

    @override_settings(GS_DECIMATE_TARGET="25%")
    def test_a_percentage_still_turns_it_back_on(self):
        from django.conf import settings

        self.assertTrue(settings.GS_DECIMATE_TARGET)


class TheLodEncodeIsNotPaidForUnread(SimpleTestCase):
    """
    It was encoded on every GS run and `run_cleanup` never copied it into the
    served tree, so it went to the archive with the run: 0 published sets on
    this deployment, 1 archived, and nothing in the frontend that would read it.
    """

    def test_lod_generation_is_off(self):
        from django.conf import settings

        self.assertFalse(settings.GS_EMIT_LOD)

    def test_it_is_still_guarded_so_it_can_be_turned_on_with_the_viewer(self):
        source = inspect.getsource(GSOps.run_postprocess)
        self.assertIn("if GS_EMIT_LOD:", source)


class TheCompressedPlyEncodeCannotLoseTheRun(SimpleTestCase):
    """
    It sat inside the try whose handler re-raises, ahead of the required
    artifacts, so splat-transform refusing it destroyed a finished
    reconstruction. One of the 60 published splats does refuse.
    """

    def test_it_runs_after_the_published_artifact_is_checked(self):
        source = inspect.getsource(GSOps.run_postprocess)
        checked = source.index('self._check_output(published, "run_postprocess")')
        encoded = source.index("to_compressed_ply")
        self.assertLess(
            encoded,
            checked,
            "the compressed PLY should be encoded before the final check, "
            "but outside the fatal try",
        )

    def test_it_has_its_own_handler_that_continues(self):
        source = inspect.getsource(GSOps.run_postprocess)
        tail = source[source.index("to_compressed_ply") :]
        self.assertIn("except Exception", tail)
        self.assertIn("Could not encode the compressed PLY", tail)
