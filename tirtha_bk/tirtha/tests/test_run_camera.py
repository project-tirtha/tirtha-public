"""
Tests for the unified camera model (internal design notes).

82 runs carry framing an operator tuned by hand, in two viewer-specific
vocabularies: rotaX/Y/Z for Google <model-viewer> on the photogrammetry path,
and camUp*/initCamPos*/initCamLookAt* for gsplat on the GS path. Neither viewer
understood the other's numbers. One viewer now serves both formats, so the
framing is one set of numbers.

The legacy columns are deliberately kept: they are the only record of that
hand-tuning, and the derivation reads them, so a bad mapping can be corrected
and re-run.
"""

import importlib
import json

from django.test import TestCase
from django.urls import reverse

from tirtha.models import Run, Site

_0022 = importlib.import_module("tirtha.migrations.0022_derive_run_camera")


class _Apps:
    @staticmethod
    def get_model(app_label, model_name):
        return {"Run": Run}[model_name]


class DerivationTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )

    def _run(self, **kw):
        return Run.objects.create(site=self.site, kind=kw.pop("kind", "GS"), **kw)

    def test_gsplat_framing_becomes_position_and_target(self):
        run = self._run(
            initCamPosX=1.0,
            initCamPosY=2.0,
            initCamPosZ=3.0,
            initCamLookAtX=0.0,
            initCamLookAtY=0.5,
            initCamLookAtZ=0.0,
        )
        _0022.derive_camera(_Apps, None)
        run.refresh_from_db()
        self.assertEqual(run.cam_position, [1.0, 2.0, 3.0])
        self.assertEqual(run.cam_target, [0.0, 0.5, 0.0])

    def test_model_viewer_rotation_is_carried_across(self):
        run = self._run(kind="aV", rotaX=90, rotaY=0, rotaZ=180)
        _0022.derive_camera(_Apps, None)
        run.refresh_from_db()
        self.assertEqual(run.model_rotation, [90.0, 0.0, 180.0])

    def test_rotation_is_carried_for_gs_runs_too(self):
        # rotaX/Y/Z were only meaningful on the aV path, but a GS run with
        # non-zero values had them set deliberately.
        run = self._run(kind="GS", rotaX=45)
        _0022.derive_camera(_Apps, None)
        run.refresh_from_db()
        self.assertEqual(run.model_rotation[0], 45.0)

    def test_the_up_vector_defaults_to_y_up_when_unset(self):
        run = self._run(camUpX=None, camUpY=None, camUpZ=None)
        _0022.derive_camera(_Apps, None)
        run.refresh_from_db()
        self.assertEqual(run.cam_up, [0.0, 1.0, 0.0])

    def test_stored_defaults_are_carried_across_not_discarded(self):
        # initCamPos* defaults to (0, 0, 5) and is non-null, so "never framed"
        # and "framed to exactly the default" are indistinguishable in the
        # data. That is fine: whatever value the old viewer used is the framing
        # that produced the look people have already seen and shared.
        run = self._run()
        _0022.derive_camera(_Apps, None)
        run.refresh_from_db()
        self.assertEqual(run.cam_position, [0.0, 0.0, 5.0])

    def test_an_all_null_position_falls_back_rather_than_inventing_one(self):
        run = self._run(initCamPosX=None, initCamPosY=None, initCamPosZ=None)
        _0022.derive_camera(_Apps, None)
        run.refresh_from_db()
        self.assertEqual(run.cam_position, [])

    def test_the_legacy_columns_are_left_intact(self):
        # They are the only record of the hand-tuning, and the derivation reads
        # them -- dropping them would make it unrepeatable.
        run = self._run(initCamPosX=1.0, rotaX=90)
        _0022.derive_camera(_Apps, None)
        run.refresh_from_db()
        self.assertEqual(run.initCamPosX, 1.0)
        self.assertEqual(run.rotaX, 90)

    def test_running_twice_is_stable(self):
        run = self._run(initCamPosX=1.0, initCamPosY=2.0, initCamPosZ=3.0)
        _0022.derive_camera(_Apps, None)
        run.refresh_from_db()
        first = run.cam_position
        _0022.derive_camera(_Apps, None)
        run.refresh_from_db()
        self.assertEqual(run.cam_position, first)


class ApiShapeTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.run = Run.objects.create(
            site=self.site,
            kind="GS",
            cam_position=[1.0, 2.0, 3.0],
            cam_target=[0.0, 0.0, 0.0],
            cam_up=[0.0, 1.0, 0.0],
            model_rotation=[0.0, 90.0, 0.0],
        )

    def test_the_api_exposes_one_camera_model(self):
        data = json.loads(
            self.client.get(reverse("api-run", kwargs={"run_id": self.run.ID})).content
        )
        self.assertEqual(data["camera"]["position"], [1.0, 2.0, 3.0])
        self.assertEqual(data["camera"]["rotation"], [0.0, 90.0, 0.0])
        self.assertIn("fov", data["camera"])

    def test_the_viewer_specific_legacy_fields_are_not_exposed(self):
        # A client choosing between `rotaX` and `initCamPos*` by run kind is
        # exactly the duplication this replaces.
        body = self.client.get(
            reverse("api-run", kwargs={"run_id": self.run.ID})
        ).content.decode()
        for legacy in ("rotaX", "initCamPos", "camUpX", "sphDegree", "focalAdjustment"):
            self.assertNotIn(legacy, body)

    def test_an_unframed_run_reports_null_rather_than_a_guess(self):
        bare = Run.objects.create(site=self.site, kind="aV")
        data = json.loads(
            self.client.get(reverse("api-run", kwargs={"run_id": bare.ID})).content
        )
        self.assertIsNone(data["camera"]["position"])
        self.assertEqual(data["camera"]["up"], [0.0, 1.0, 0.0])
