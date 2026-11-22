"""
Review 2026-11-22 fixes in `workers.py` (internal review 2026-11-22 §1 #3, #4, #5, #8 and §3).

COLMAP and the GPU are never run: `_serialRunner` is replaced by a fake that
writes what each COLMAP stage would, so `run_colmap`'s decisions -- which model,
which path, which gate -- are executed for real against an on-disk workspace.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from django.test import TestCase
from tirtha_recon import colmap_model_io as cio
from tirtha_recon.colmap import COMPLETION, DONE, INCOMPLETE, mark_done, write_marker
from tirtha_recon.proc import StageTimeout

from tirtha import workers
from tirtha.models import Run, RunStage, Site
from tirtha.workers import GSOps, MeshOps, base, common, gsops, meshops, runner
from tirtha.workspace import Workspace

# Every module a workers constant can be read from. `workers` was one module;
# it is now a package whose submodules each bind the constants they read, so a
# patch on `tirtha.workers` alone would no longer reach the methods.
_CONSTANT_HOMES = (workers, common, base, meshops, gsops, runner)


def _patch_constant(name, value):
    """
    `patch.object(workers, name, value)` as it behaved when `workers` was a
    module: patched everywhere a method could read it, and on the package so a
    test that reads `workers.NAME` back sees the patched value too.
    """
    homes = [m for m in _CONSTANT_HOMES if name in vars(m)]
    assert workers in homes and common in homes, f"{name} is not a workers constant"
    return [patch.object(m, name, value) for m in homes]


def _model(path: Path, names) -> Path:
    cams, ims = {}, {}
    for i, name in enumerate(names, start=1):
        cams[i] = cio.Camera(i, "SIMPLE_RADIAL", 100, 100, [100.0, 50.0, 50.0, 0.0])
        ims[i] = cio.Image(
            i, np.array([1.0, 0, 0, 0]), np.array([float(i), 0, 0]), i, name
        )
    cio.write_model_bin(cio.Model(cams, ims, {}), path)
    return path


class _Base(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tirtha-pipeline-fixes-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for name, value in (
            ("LOG_DIR", self.tmp / "logs"),
            ("ARCHIVE_ROOT", self.tmp / "archive"),
        ):
            for p in _patch_constant(name, value):
                p.start()
                self.addCleanup(p.stop)
        self.site = Site.objects.create(
            name="Konark", country="India", state="Odisha", district="Puri"
        )
        self.run = Run.objects.create(site=self.site, kind="GS")
        self.ops = GSOps.for_existing_run(self.run)


# --------------------------------------------------------------------------- §1 #8
class SiteStatusDoesNotWriteAStaleSite(_Base):
    def test_an_admin_edit_during_the_run_survives(self):
        Site.objects.filter(pk=self.site.pk).update(
            name="Konark Sun Temple", hidden=True
        )
        before = Site.objects.get(pk=self.site.pk).updated_at

        self.ops._update_site_status("Error")

        fresh = Site.objects.get(pk=self.site.pk)
        self.assertEqual(fresh.status, "Error")
        self.assertEqual(fresh.name, "Konark Sun Temple")
        self.assertTrue(fresh.hidden)
        self.assertGreaterEqual(fresh.updated_at, before)  # auto_now applied by hand
        self.assertEqual(self.ops.site.status, "Error")  # in-memory copy follows

    def test_finalize_writes_reconstructed_at_the_same_way(self):
        Site.objects.filter(pk=self.site.pk).update(description="edited")
        self.ops.kind = "GS"
        self.ops.run_finalize()
        fresh = Site.objects.get(pk=self.site.pk)
        self.assertEqual((fresh.status, fresh.description), ("Live", "edited"))
        self.assertIsNotNone(fresh.reconstructed_at)


# --------------------------------------------------------------------------- §1 #5
class TheSerialRunner(_Base):
    def test_no_shell_interprets_the_command(self):
        log = self.tmp / "r.log"
        GSOps._serialRunner(["echo", f"a; touch {self.tmp / 'pwned'}"], log)
        self.assertFalse((self.tmp / "pwned").exists())
        self.assertIn("a; touch", log.read_text())

    def test_a_string_is_split_not_handed_to_a_shell(self):
        out = GSOps._serialRunner(
            "echo 'two words' $HOME", self.tmp / "r.log", capture=True
        )
        self.assertEqual(out, "two words $HOME")

    def test_output_reaches_the_log_before_a_hang_is_killed(self):
        log = self.tmp / "hang.log"
        with self.assertRaises(StageTimeout):
            GSOps._serialRunner(["sh", "-c", "echo mapping; sleep 30"], log, timeout=1)
        self.assertIn("mapping", log.read_text())

    def test_a_failure_still_raises_called_process_error(self):
        from subprocess import CalledProcessError

        with self.assertRaises(CalledProcessError):
            GSOps._serialRunner(["sh", "-c", "exit 2"], self.tmp / "f.log")

    def test_settings_have_generous_defaults(self):
        self.assertEqual(workers.COLMAP_STAGE_TIMEOUT_S, 6 * 3600)
        self.assertEqual(workers.FASTGS_TIMEOUT_S, 4 * 3600)
        self.assertEqual(workers.COLMAP_COMPLETION_TIMEOUT_S, 3 * 3600)

    def test_obj2gltf_paths_are_never_shell_parsed(self):
        ops = MeshOps.__new__(MeshOps)
        ops.runDir = self.tmp / "run; rm -rf x"
        ops.textured_path = self.tmp / "tex dir"
        ops.obj2gltf_exec = Path("/usr/bin/obj2gltf")
        ops.siteStr = "s"
        MeshOps.logger = self.ops.logger
        calls = []
        with (
            patch.object(
                MeshOps, "_serialRunner", side_effect=lambda cmd, log: calls.append(cmd)
            ),
            patch.object(MeshOps, "_check_output"),
        ):
            ops.run_obj2gltf()
        argv = calls[0]
        self.assertIsInstance(argv, list)
        self.assertIn(
            str(ops.textured_path / "texturedDecimatedMesh/texturedMesh.obj"), argv
        )
        self.assertEqual(argv[-2:], ["--secure", "true"])


# --------------------------------------------------------------------------- run_colmap
class _FakeColmap:
    """Stands in for `_serialRunner`: does on disk what each COLMAP stage would."""

    def __init__(self, test, *, components=None, continuation_writes=True, stats=None):
        self.test = test
        self.components = components or {}
        self.continuation_writes = continuation_writes
        self.stats = stats or "Images: 5\nRegistered images: 5"
        self.stages = []

    def __call__(self, cmd, log_file, timeout=None, capture=False):
        argv = [str(a) for a in cmd]
        stage = argv[1] if argv[1] != "-m" else argv[2]
        self.stages.append((stage, argv, timeout))
        arg = lambda k: argv[argv.index(k) + 1]  # noqa: E731
        if stage == "mapper" and "--input_path" not in argv:
            for name, names in self.components.items():
                _model(Path(arg("--output_path")) / name, names)
        elif stage == "mapper":
            if self.continuation_writes:
                prev = cio.read_model(arg("--input_path"))
                names = [im.name for im in prev.images.values()] + ["c.jpg"]
                _model(Path(arg("--output_path")), names)
        elif stage == "point_filtering":
            shutil.copytree(
                arg("--input_path"), arg("--output_path"), dirs_exist_ok=True
            )
        elif stage == "model_analyzer":
            return self.stats
        return None


class RunColmap(_Base):
    def setUp(self):
        super().setUp()
        root = self.tmp / "ws"
        self.ws = Workspace(
            root=root,
            images=root / "images",
            masks=root / "masks",
            database=root / "colmap" / "database.db",
            sparse=root / "colmap" / "sparse",
        )
        self.ws.ensure()
        self.ops.log_path = self.tmp / "logs"
        self.ops.log_path.mkdir(parents=True, exist_ok=True)
        self.gate = patch.object(GSOps, "_validate_colmap_registration")
        self.gate_mock = self.gate.start()
        self.addCleanup(self.gate.stop)
        for target, value in (
            (
                "tirtha.workspace.materialise_site",
                lambda site: (
                    self.ws,
                    SimpleNamespace(linked=0, already_present=3, missing_sources=[]),
                ),
            ),
            ("tirtha.workspace.withdrawn_links", lambda site, ws: set()),
        ):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)

    def _images(self, *names):
        for n in names:
            (self.ws.images / n).write_bytes(b"x")

    def _db(self, *names):
        with sqlite3.connect(self.ws.database) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS images (image_id INTEGER PRIMARY KEY, name TEXT)"
            )
            conn.executemany(
                "INSERT INTO images (name) VALUES (?)", [(n,) for n in names]
            )

    def _run(self, fake, **settings):
        with ExitStack() as stack:
            stack.enter_context(patch.object(GSOps, "_serialRunner", fake))
            for name, value in {"COLMAP_COMPLETION": False, **settings}.items():
                for p in _patch_constant(name, value):
                    stack.enter_context(p)
            self.ops.run_colmap()

    def test_a_crashed_continuation_is_rerun_not_republished(self):
        """§1 #3: c.jpg reached database.db, then the continuation died."""
        _model(self.ws.sparse / "0", ["a.jpg", "b.jpg"])
        self._images("a.jpg", "b.jpg", "c.jpg")
        self._db("a.jpg", "b.jpg", "c.jpg")
        fake = _FakeColmap(self)
        self._run(fake)
        self.assertTrue(self.ops.continuation)
        self.assertEqual(
            self.ops.colmapModel, self.ws.sparse / "1"
        )  # the plan's output dir
        self.assertTrue((self.ws.sparse / "1" / DONE).exists())
        self.assertFalse((self.ws.sparse / "1" / INCOMPLETE).exists())
        self.assertEqual(
            [s for s, _, _ in fake.stages],
            ["feature_extractor", "vocab_tree_matcher", "mapper", "model_analyzer"],
        )
        self.assertTrue(
            all(t == workers.COLMAP_STAGE_TIMEOUT_S for _, _, t in fake.stages)
        )

    def test_a_continuation_that_writes_nothing_fails(self):
        _model(self.ws.sparse / "0", ["a.jpg", "b.jpg"])
        self._images("a.jpg", "b.jpg", "c.jpg")
        self._db("a.jpg", "b.jpg")
        with self.assertRaisesRegex(RuntimeError, "wrote no model"):
            self._run(_FakeColmap(self, continuation_writes=False))
        # The empty directory stays marked and is never built on.
        self.assertTrue((self.ws.sparse / "1" / INCOMPLETE).exists())

    def test_nothing_new_reuses_the_model_and_still_gates_it(self):
        """§3: the reuse path used to skip the registration gate."""
        model = _model(self.ws.sparse / "0", ["a.jpg", "b.jpg"])
        self._images("a.jpg", "b.jpg")
        self._db("a.jpg", "b.jpg")
        fake = _FakeColmap(self, stats="Images: 2\nRegistered images: 2")
        self._run(fake)
        self.assertEqual(self.ops.colmapModel, model)
        self.assertEqual([s for s, _, _ in fake.stages], ["model_analyzer"])
        self.gate_mock.assert_called_once()
        self.assertEqual(
            self.gate_mock.call_args[0][0], {"images": 2.0, "registered_images": 2.0}
        )

    def test_completion_off_ignores_a_stored_completion_model(self):
        """§1 #4: the stored 4.2.1 model is not reused once the setting is off."""
        base = _model(self.ws.sparse / "0", ["a.jpg", "b.jpg"])
        stored = _model(self.ws.sparse / "1", ["a.jpg", "b.jpg", "c.jpg"])
        write_marker(stored, COMPLETION)
        mark_done(stored, attempted=["a.jpg", "b.jpg", "c.jpg"])
        self._images("a.jpg", "b.jpg")
        self._db("a.jpg", "b.jpg", "c.jpg")
        self._run(_FakeColmap(self))
        self.assertEqual(self.ops.colmapModel, base)
        self._run(_FakeColmap(self), COLMAP_COMPLETION=True)
        self.assertEqual(self.ops.colmapModel, stored)

    def test_a_fresh_build_starts_empty_and_post_processes_the_largest(self):
        """§3: old sparse/ moved aside; filtering/up axis/gate on the chosen model."""
        half = _model(self.ws.sparse / "0", ["a.jpg"])
        (half / INCOMPLETE).write_text("")  # nothing complete to build on
        self._images("a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg")
        self._db("a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg")
        fake = _FakeColmap(
            self, components={"0": ["a.jpg", "b.jpg"], "1": ["c.jpg", "d.jpg", "e.jpg"]}
        )
        self._run(fake, COLMAP_UP_AXIS="align_up")

        old = [
            p
            for p in self.ws.sparse.parent.iterdir()
            if p.name.startswith("sparse.old-")
        ]
        self.assertEqual(len(old), 1)
        self.assertTrue((old[0] / "0" / INCOMPLETE).exists())  # moved, never deleted
        chosen = self.ws.sparse / "1"
        self.assertEqual(self.ops.colmapModel, chosen)
        self.assertTrue((chosen / DONE).exists())
        self.assertFalse((self.ws.sparse / "0" / DONE).exists())
        by_stage = {s: argv for s, argv, _ in fake.stages}
        self.assertEqual(
            by_stage["point_filtering"][
                by_stage["point_filtering"].index("--input_path") + 1
            ],
            str(chosen),
        )
        self.assertEqual(by_stage["tirtha_recon.align_up"][3], str(chosen) + ".post")
        self.assertEqual(by_stage["model_analyzer"][-1], str(chosen))
        self.assertEqual(
            sorted(p.name for p in self.ws.sparse.iterdir()), ["0", "1", "REQUIRE_DONE"]
        )


# --------------------------------------------------------------------------- publishing, re-cut, pruning
class PublishingAndRecut(_Base):
    def _outputs(self, *names):
        opt = self.tmp / "published"
        opt.mkdir(exist_ok=True)
        for n in names:
            (opt / n).write_bytes(b"data")
        self.ops.opt_path = opt
        return opt

    def test_the_splat_is_required(self):
        self._outputs("scene.sog")
        with self.assertRaises(FileNotFoundError):
            self.ops._publish_artifacts()
        dest = (
            workers.STATIC
            / f"models/{self.site.ID}/published/{self.site.ID}_{self.run.ID}.sog"
        )
        self.assertFalse(dest.exists())  # nothing half-published

    def test_both_are_published(self):
        self._outputs("scene.sog", "scene.splat")
        dest = self.ops._publish_artifacts()
        self.assertTrue(dest.is_file() and dest.with_suffix(".splat").is_file())

    def test_the_recut_uses_the_runs_own_backend(self):
        RunStage.objects.create(
            run=self.run, name="run_frgs", metrics={"backend": "splatfacto"}
        )
        self.assertEqual(GSOps._recorded_backend(self.run, self.tmp), "splatfacto")

    def test_fastgs_runs_from_before_the_backend_was_recorded(self):
        RunStage.objects.create(
            run=self.run, name="run_frgs", metrics={"prepare_s": 12.0}
        )
        self.assertEqual(GSOps._recorded_backend(self.run, self.tmp), "fastgs")
        other = Run.objects.create(site=self.site, kind="GS")
        self.assertEqual(GSOps._recorded_backend(other, self.tmp), "legacy")

    def test_an_archived_run_resolves_to_the_archive(self):
        Run.objects.filter(pk=self.run.pk).update(
            status="Archived", directory="x/gscache/r1"
        )
        self.run.refresh_from_db()
        ops = GSOps.for_existing_run(self.run)
        self.assertEqual(ops.runDir, workers.ARCHIVE_ROOT / "x/gscache/r1")

    def test_a_recut_republishes_and_drops_the_viewer_frame(self):
        Run.objects.filter(pk=self.run.pk).update(
            status="Archived", directory="k/gscache/r2"
        )
        self.run.refresh_from_db()
        RunStage.objects.create(
            run=self.run, name="run_frgs", metrics={"backend": "fastgs"}
        )
        ops = GSOps.for_existing_run(self.run)
        (ops.runDir / "output").mkdir(parents=True)
        (ops.runDir / "output" / "reconstruction.ply").write_bytes(b"ply")

        def fake_runner(cmd, log_file, timeout=None, capture=False):
            out = Path(cmd[-1])
            if not out.is_absolute():  # a trailing flag, not an output path
                return
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"encoded")

        def fake_frame(src, dst, model, **kw):
            Path(dst).write_bytes(b"framed")

        with (
            patch.object(GSOps, "_serialRunner", side_effect=fake_runner),
            patch(
                "tirtha_recon.fastgs.to_viewer_frame", side_effect=fake_frame
            ) as framed,
        ):
            ops.run_postprocess()
        framed.assert_called_once()
        self.assertFalse((ops.runDir / "output" / "viewer_frame.ply").exists())
        dest = (
            workers.STATIC
            / f"models/{self.site.ID}/published/{self.site.ID}_{self.run.ID}.sog"
        )
        self.assertTrue(dest.is_file() and dest.with_suffix(".splat").is_file())

    def test_stage_rows_of_a_recut_get_a_new_attempt(self):
        first = self.ops._begin_stage("run_postprocess", 4)
        second = self.ops._begin_stage("run_postprocess", 5)
        self.assertEqual((first.attempt, second.attempt), (1, 2))

    def test_cancelled_runs_are_pruned_like_errors(self):
        doomed = []
        for status in ("Error", "Cancelled"):
            r = Run.objects.create(site=self.site, kind="GS")
            Run.objects.filter(pk=r.pk).update(status=status)
            doomed.append((r.pk, workers.STATIC / "models" / r.directory))
        self._outputs("scene.sog", "scene.splat")
        self.ops.runDir.mkdir(parents=True, exist_ok=True)
        (workers.ARCHIVE_ROOT / str(self.site.ID) / "gscache").mkdir(parents=True)
        self.ops.run_cleanup()
        for pk, path in doomed:
            self.assertFalse(Run.objects.filter(pk=pk).exists())
            self.assertFalse(path.exists())
