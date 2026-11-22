"""
Tests for continual reconstruction (internal design notes).

COLMAP is incremental where FRGS is not: adding 3 images to a 300-image site
should extend the existing sparse model, not rebuild it. This is the payoff for
the durable per-site workspace built in 5.5.

The subtlest property tested here is the FRGS dataset view. FRGS resolves its
model as `<dir>/sparse/0`, hardcoded -- so once continuation starts writing
`sparse/1`, handing it the workspace directly would train on the stale model
silently, with no error anywhere.
"""

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase
from tirtha_recon.colmap import (
    build_continuation_plan,
    known_images,
    latest_model,
)


class _Workspace(TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.images = self.root / "images"
        self.sparse = self.root / "colmap" / "sparse"
        self.database = self.root / "colmap" / "database.db"
        self.images.mkdir(parents=True)
        self.sparse.mkdir(parents=True)

    def _register(self, *names):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS images (image_id INTEGER, name TEXT)"
            )
            conn.executemany("INSERT INTO images VALUES (?, ?)", list(enumerate(names)))

    def _model(self, name):
        model = self.sparse / name
        model.mkdir(parents=True, exist_ok=True)
        (model / "cameras.bin").write_bytes(b"")
        return model

    def _link(self, *names):
        for name in names:
            (self.images / name).write_bytes(b"jpeg")


class NewImageDetectionTests(_Workspace):
    def test_new_images_are_those_colmap_has_not_seen(self):
        self._register("00000000_a.jpg", "00000001_b.jpg")
        self._link("00000000_a.jpg", "00000001_b.jpg", "00000002_c.jpg")
        known = known_images(self.database)
        on_disk = sorted(p.name for p in self.images.iterdir())
        self.assertEqual([n for n in on_disk if n not in known], ["00000002_c.jpg"])

    def test_a_link_from_a_failed_earlier_run_counts_as_new(self):
        # The database is the only authority on what COLMAP knows: a symlink
        # can exist from a run that died before extraction.
        self._register("00000000_a.jpg")
        self._link("00000000_a.jpg", "00000001_b.jpg")
        known = known_images(self.database)
        self.assertIn(
            "00000001_b.jpg",
            [p.name for p in self.images.iterdir() if p.name not in known],
        )

    def test_nothing_new_yields_an_empty_set(self):
        self._register("00000000_a.jpg")
        self._link("00000000_a.jpg")
        known = known_images(self.database)
        self.assertEqual(
            [p.name for p in self.images.iterdir() if p.name not in known], []
        )


class ContinuationPlanTests(_Workspace):
    def test_a_continuation_extends_the_newest_model(self):
        self._model("0")
        self._model("1")
        plan = build_continuation_plan(
            database=self.database,
            image_dir=self.images,
            sparse_dir=self.sparse,
            new_images=["00000002_c.jpg"],
            image_list_path=self.root / "new.txt",
        )
        mapper = dict(plan.stages)["mapper"]
        self.assertTrue(mapper[mapper.index("--input_path") + 1].endswith("/1"))
        self.assertTrue(mapper[mapper.index("--output_path") + 1].endswith("/2"))

    def test_the_full_pipeline_is_not_re_run(self):
        # The point of the whole path: features already extracted are kept.
        self._model("0")
        plan = build_continuation_plan(
            database=self.database,
            image_dir=self.images,
            sparse_dir=self.sparse,
            new_images=["x.jpg"],
            image_list_path=self.root / "new.txt",
        )
        names = [n for n, _ in plan.stages]
        self.assertNotIn("exhaustive_matcher", names)
        self.assertNotIn("sequential_matcher", names)
        self.assertNotIn("transitive_matcher", names)


class FrgsDatasetViewTests(_Workspace):
    """
    FRGS reads `<dir>/sparse/0`, hardcoded. Continuation writes higher numbers,
    so the run needs a view that points at the right one -- otherwise training
    silently uses the stale model.
    """

    def _build_view(self, run_dir, current):
        dataset = run_dir / "dataset"
        (dataset / "sparse").mkdir(parents=True, exist_ok=True)
        for link, target in (
            (dataset / "images", self.images),
            (dataset / "sparse" / "0", current),
        ):
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(target, target_is_directory=True)
        return dataset

    def test_sparse_zero_points_at_the_current_model(self):
        self._model("0")
        current = self._model("1")
        run_dir = self.root / "run"
        dataset = self._build_view(run_dir, current)
        self.assertEqual((dataset / "sparse" / "0").resolve(), current.resolve())

    def test_the_view_does_not_disturb_the_workspace(self):
        # The durable workspace is what continuation depends on; a per-run view
        # must not touch it.
        self._model("0")
        current = self._model("1")
        before = sorted(p.name for p in self.sparse.iterdir())
        self._build_view(self.root / "run", current)
        self.assertEqual(sorted(p.name for p in self.sparse.iterdir()), before)

    def test_rebuilding_the_view_replaces_a_stale_link(self):
        first = self._model("0")
        run_dir = self.root / "run"
        self._build_view(run_dir, first)
        second = self._model("1")
        dataset = self._build_view(run_dir, second)
        self.assertEqual((dataset / "sparse" / "0").resolve(), second.resolve())

    def test_latest_model_is_what_the_view_should_track(self):
        self._model("0")
        self._model("1")
        self.assertEqual(latest_model(self.sparse).name, "1")
