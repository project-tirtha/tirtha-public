"""
The turntable's arithmetic and its refusals (internal design notes).

Nothing here renders. A frame takes ~2.7 s of CPU and the whole orbit 65 s, and
the maintainer's standing rule is that a test must not start real pipeline work
-- so what is tested is everything around the subprocess: which camera is
chosen, where the orbit goes, which adapter is picked, and every case that must
refuse rather than fall back onto the shared GPU.

The rendering itself was verified by hand against the published artifact of
`nHjUmrWgjsdQnjEH`: 24 frames, 65.4 s, 17,242 bytes, `nvidia-smi` unchanged.
"""

import math
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from tirtha.turntable import (
    DEFAULT_CAMERA_POSITION,
    Camera,
    TurntableUnavailable,
    _recentre,
    camera_for_run,
    camera_from_stats,
    orbit_positions,
    software_adapter,
    turntable_path,
)


class _Run:
    """Only the camera fields; `camera_for_run` reads nothing else."""

    def __init__(self, **fields):
        self.cam_position = fields.get("cam_position")
        self.cam_target = fields.get("cam_target")
        self.cam_up = fields.get("cam_up")
        self.cam_fov = fields.get("cam_fov")


def _listing(*adapters):
    """`--list-gpus` output, in the shape the real tool prints it."""
    lines = "\n".join(f"[{i}] {name}" for i, name in enumerate(adapters))
    return f"Enumerating available GPU adapters...\n{lines}\nUse -g <index>"


class AdapterSelection(SimpleTestCase):
    """
    The single most important behaviour in the module.

    The GPU is shared with another lab and was at 98% with 8.9 GB resident
    throughout this work; a frame asked of it did not return in 120 s while the
    software rasteriser produced the same frame in 2.4 s. Picking the wrong
    adapter is not a slow preview, it is taking someone else's card to be
    thirty times slower.
    """

    def _adapter(self, stdout):
        with patch("tirtha.turntable.subprocess.run") as run:
            run.return_value.stdout = stdout
            return software_adapter("splat-transform")

    def test_finds_llvmpipe_by_name(self):
        self.assertEqual(
            self._adapter(
                _listing("NVIDIA RTX A6000", "llvmpipe (LLVM 20.1.2, 256 bits)")
            ),
            1,
        )

    def test_does_not_assume_the_index(self):
        # The index is host-specific. A hardcoded 1 on this listing would be
        # the A6000.
        self.assertEqual(
            self._adapter(_listing("llvmpipe (LLVM 20.1.2)", "NVIDIA RTX A6000")),
            0,
        )

    def test_recognises_the_other_software_rasterisers(self):
        for name in ("lavapipe", "SwiftShader Device", "softpipe"):
            with self.subTest(name=name):
                self.assertEqual(self._adapter(_listing("NVIDIA RTX A6000", name)), 1)

    def test_refuses_rather_than_falling_back_to_the_shared_gpu(self):
        """
        A host with no software adapter must skip the preview.

        Returning 0 here would be the quiet failure that matters: every
        published run would start queueing frames behind a multi-hour training
        job on a card this project does not own.
        """
        with self.assertRaises(TurntableUnavailable) as caught:
            self._adapter(_listing("NVIDIA RTX A6000"))
        self.assertIn("refusing", str(caught.exception))

    def test_refuses_when_the_tool_lists_nothing(self):
        with self.assertRaises(TurntableUnavailable):
            self._adapter("")


class CameraChoice(SimpleTestCase):
    def test_uses_a_hand_tuned_camera(self):
        # 32 of the 81 production runs carry one, from the viewer.
        camera = camera_for_run(
            _Run(
                cam_position=[10.19612, -6.96443, -10.26819],
                cam_target=[1.64407, 0.01542, -1.04566],
                cam_up=[0.04343, -0.99905, 0.00245],
                cam_fov=60.0,
            )
        )
        self.assertEqual(camera.position[0], 10.19612)
        self.assertEqual(camera.fov, 60.0)

    def test_rejects_the_migration_placeholder(self):
        """
        `[0, 0, 5]` is the absence of a framing, not a framing.

        Migration 0021 wrote it wherever it could not derive one -- 49 of 81
        runs. Rendering from it put the camera inside the temple: a haze of
        background gaussians at 28% coverage, which passes a coverage check and
        is still unusable. It has to be recognised by value, here, rather than
        caught downstream.
        """
        self.assertIsNone(
            camera_for_run(_Run(cam_position=list(DEFAULT_CAMERA_POSITION)))
        )

    def test_rejects_a_run_with_no_camera_at_all(self):
        self.assertIsNone(camera_for_run(_Run()))

    def test_fills_in_the_parts_a_run_may_be_missing(self):
        camera = camera_for_run(_Run(cam_position=[3.0, 1.0, 2.0]))
        self.assertEqual(camera.target, (0.0, 0.0, 0.0))
        self.assertEqual(camera.fov, 60.0)


class DerivedCamera(SimpleTestCase):
    """
    The fallback, for the runs with no real framing.

    The box, capped at four sigma, rather than the median: the median is where
    *most gaussians* are, which on a scene with a broad ground plane is the
    paving. It framed one model with the monument along the bottom edge and a
    frame of empty sky above it.
    """

    STATS = {
        "min": [-1.0, -1.0, -1.0] + [0] * 11,
        "max": [1.0, 1.0, 1.0] + [0] * 11,
        "median": [0.0, 0.0, 0.0] + [0] * 11,
        "stdDev": [0.5, 0.5, 0.5] + [0] * 11,
    }

    def test_looks_at_the_middle_of_the_scene(self):
        camera = camera_from_stats(self.STATS)
        for value in camera.target:
            self.assertAlmostEqual(value, 0.0)

    def test_stands_outside_the_scene(self):
        camera = camera_from_stats(self.STATS)
        # The scene's visible half-width; the camera has to be beyond it or it
        # is inside the model.
        self.assertGreater(math.dist(camera.position, camera.target), 1.0)

    def test_takes_the_up_axis_it_is_given(self):
        """
        Up genuinely varies between models -- one rendered correctly with
        (0, -1, 0) and another showed only paving until it was flipped -- so
        this does arithmetic and `probe_camera` decides by looking.
        """
        for up in ((0.0, -1.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)):
            with self.subTest(up=up):
                self.assertEqual(camera_from_stats(self.STATS, up=up).up, up)

    def test_frames_a_wider_scene_from_further_away(self):
        wide = {
            "min": [-10.0, -10.0, -10.0] + [0] * 11,
            "max": [10.0, 10.0, 10.0] + [0] * 11,
            "median": [0.0, 0.0, 0.0] + [0] * 11,
            "stdDev": [5.0, 5.0, 5.0] + [0] * 11,
        }
        self.assertGreater(
            math.dist(camera_from_stats(wide).position, camera_from_stats(wide).target),
            math.dist(
                camera_from_stats(self.STATS).position,
                camera_from_stats(self.STATS).target,
            ),
        )

    def test_ignores_a_halo_that_reaches_far_past_the_monument(self):
        """
        A splat scene carries sparse background gaussians well outside the
        subject. Framing to include them frames mostly empty space, so the
        extent is capped at four sigma.
        """
        haloed = {
            "min": [-100.0, -1.0, -1.0] + [0] * 11,
            "max": [100.0, 1.0, 1.0] + [0] * 11,
            "median": [0.0, 0.0, 0.0] + [0] * 11,
            "stdDev": [0.5, 0.5, 0.5] + [0] * 11,
        }
        self.assertLess(
            math.dist(
                camera_from_stats(haloed).position, camera_from_stats(haloed).target
            ),
            20.0,
        )


class Recentring(SimpleTestCase):
    """
    Sliding the target so the monument sits in the middle of the frame.

    The probe frame says where the content actually is, in pixels; the box only
    said where it might be.
    """

    CAMERA = Camera(
        position=(0.0, 0.0, 5.0), target=(0.0, 0.0, 0.0), up=(0.0, 1.0, 0.0), fov=60.0
    )

    def test_leaves_a_centred_frame_alone(self):
        moved = _recentre(self.CAMERA, (0.0, 0.0), (320, 320))
        for got, want in zip(moved.target, self.CAMERA.target):
            self.assertAlmostEqual(got, want)

    def test_moves_the_target_towards_content_low_in_the_frame(self):
        # Content 80px below centre: the target must come down with it, in the
        # direction opposite `up`.
        moved = _recentre(self.CAMERA, (0.0, 80.0), (320, 320))
        self.assertLess(moved.target[1], self.CAMERA.target[1])

    def test_moves_the_camera_with_the_target(self):
        """
        A turntable, not a pan: shifting only the target would tilt the camera
        and change the orbit's radius over the loop.
        """
        moved = _recentre(self.CAMERA, (40.0, 80.0), (320, 320))
        for axis in range(3):
            self.assertAlmostEqual(
                moved.position[axis] - self.CAMERA.position[axis],
                moved.target[axis] - self.CAMERA.target[axis],
                places=9,
            )

    def test_shifts_further_for_a_more_distant_camera(self):
        # The same pixel offset spans more world at greater distance.
        near = _recentre(self.CAMERA, (0.0, 80.0), (320, 320))
        far = _recentre(
            Camera(
                position=(0.0, 0.0, 50.0),
                target=(0.0, 0.0, 0.0),
                up=(0.0, 1.0, 0.0),
                fov=60.0,
            ),
            (0.0, 80.0),
            (320, 320),
        )
        self.assertGreater(abs(far.target[1]), abs(near.target[1]))


class Orbit(SimpleTestCase):
    CAMERA = Camera(
        position=(3.0, 0.0, 0.0), target=(0.0, 0.0, 0.0), up=(0.0, 1.0, 0.0), fov=60.0
    )

    def test_returns_one_position_per_frame(self):
        self.assertEqual(len(orbit_positions(self.CAMERA, frames=24)), 24)

    def test_starts_where_the_stored_camera_stands(self):
        first = orbit_positions(self.CAMERA, frames=8)[0]
        for got, want in zip(first, self.CAMERA.position):
            self.assertAlmostEqual(got, want)

    def test_keeps_every_frame_the_same_distance_from_the_target(self):
        """
        A turntable, not a fly-through.

        Rotating the stored camera preserves whatever distance and elevation a
        curator chose in the viewer; only the bearing changes. If the radius
        drifted, the model would pulse in and out over the loop.
        """
        for position in orbit_positions(self.CAMERA, frames=24):
            self.assertAlmostEqual(
                math.dist(position, self.CAMERA.target), 3.0, places=6
            )

    def test_goes_all_the_way_round(self):
        positions = orbit_positions(self.CAMERA, frames=4)
        # Quarter turn about +Y from (3,0,0) is (0,0,-3).
        self.assertAlmostEqual(positions[1][0], 0.0, places=6)
        self.assertAlmostEqual(positions[1][2], -3.0, places=6)
        # Half turn.
        self.assertAlmostEqual(positions[2][0], -3.0, places=6)

    def test_preserves_the_elevation_the_curator_chose(self):
        # An off-axis camera, as every real one is.
        camera = Camera(
            position=(3.0, 2.0, 0.0),
            target=(0.0, 0.0, 0.0),
            up=(0.0, 1.0, 0.0),
            fov=60.0,
        )
        for position in orbit_positions(camera, frames=12):
            self.assertAlmostEqual(position[1], 2.0, places=6)

    def test_handles_an_arbitrary_up_vector(self):
        """
        The stored ups are things like (0.043, -0.999, 0.002), not basis
        vectors -- which is why this rotates by Rodrigues rather than by a
        fixed-axis special case.
        """
        camera = Camera(
            position=(10.19612, -6.96443, -10.26819),
            target=(1.64407, 0.01542, -1.04566),
            up=(0.04343, -0.99905, 0.00245),
            fov=60.0,
        )
        radius = math.dist(camera.position, camera.target)
        for position in orbit_positions(camera, frames=24):
            self.assertAlmostEqual(math.dist(position, camera.target), radius, places=5)

    def test_refuses_a_zero_up_vector(self):
        camera = Camera(
            position=(3.0, 0.0, 0.0),
            target=(0.0, 0.0, 0.0),
            up=(0.0, 0.0, 0.0),
            fov=60.0,
        )
        with self.assertRaises(TurntableUnavailable):
            orbit_positions(camera)


class Naming(SimpleTestCase):
    def test_sits_beside_the_thumbnail(self):
        # Same convention as `preview` and `card`: the thumbnail's own path
        # with a suffix, so there is no migration and nothing to keep in step.
        self.assertEqual(
            turntable_path("models/abc/abc_thumb.png"),
            "models/abc/abc_thumb__turntable.mp4",
        )

    def test_is_not_a_thumbnail_derivative(self):
        """
        Everything in `THUMBNAIL_DERIVATIVES` is handed to Pillow, which will
        not make an MP4 out of a JPEG.
        """
        from tirtha.models import THUMBNAIL_DERIVATIVES

        self.assertNotIn("turntable", THUMBNAIL_DERIVATIVES)


class Refusals(SimpleTestCase):
    def test_will_not_try_to_render_a_mesh(self):
        """
        `.glb` is not renderable by this tool, and the published mesh is not
        renderable by any headless loader available here: it is gltfpack output
        declaring KHR_mesh_quantization, EXT_meshopt_compression and
        KHR_texture_basisu. Refusing early is how the 11 mesh-only sites keep
        their still image instead of logging a subprocess failure per run.
        """
        from tirtha.turntable import render_turntable

        with self.assertRaises(TurntableUnavailable) as caught:
            render_turntable(
                Path("/nonexistent/model.glb"),
                Path("/tmp/out.mp4"),
                Orbit.CAMERA,
                Path("/tmp/work"),
            )
        self.assertIn(".glb", str(caught.exception))
