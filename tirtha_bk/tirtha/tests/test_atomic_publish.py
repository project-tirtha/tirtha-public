"""
A re-cut must not rewrite a live model underneath the people downloading it.

`run_postprocess` wrote straight into `published/`, the served directory.
Encoding a few hundred megabytes takes minutes, and `GSOps.for_existing_run`
runs it against a run that is already published.
"""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase

from tirtha.workers import GSOps


class _Ops:
    """Just enough of GSOps to exercise the swap."""

    def __init__(self):
        import logging

        self.logger = logging.getLogger("test-swap")

    _swap_in_published = GSOps._swap_in_published


class TheSwapIsAtomic(TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.final = self.root / "published"
        self.staging = self.root / ".published-staging"

    def _write(self, directory: Path, **files):
        directory.mkdir(parents=True, exist_ok=True)
        for name, body in files.items():
            (directory / name).write_text(body)

    def test_a_first_publish_moves_staging_into_place(self):
        self._write(self.staging, **{"scene.sog": "new"})
        _Ops()._swap_in_published(self.staging, self.final)
        self.assertFalse(self.staging.exists())
        self.assertEqual((self.final / "scene.sog").read_text(), "new")

    def test_a_republish_replaces_the_whole_set(self):
        self._write(self.final, **{"scene.sog": "old", "scene.splat": "old"})
        self._write(self.staging, **{"scene.sog": "new", "scene.splat": "new"})
        _Ops()._swap_in_published(self.staging, self.final)
        self.assertEqual((self.final / "scene.sog").read_text(), "new")
        self.assertEqual((self.final / "scene.splat").read_text(), "new")

    def test_no_artifact_from_the_old_set_survives_into_the_new_one(self):
        """
        The failure this prevents: a cut .sog beside an uncut .splat, two
        artifacts of one run disagreeing about which reconstruction they are.
        """
        self._write(self.final, **{"scene.sog": "old", "stale.splat": "old"})
        self._write(self.staging, **{"scene.sog": "new"})
        _Ops()._swap_in_published(self.staging, self.final)
        self.assertEqual(sorted(p.name for p in self.final.iterdir()), ["scene.sog"])

    def test_a_leftover_previous_from_an_interrupted_swap_is_cleaned_up(self):
        self._write(self.final.with_name("published.previous"), **{"x": "junk"})
        self._write(self.final, **{"scene.sog": "old"})
        self._write(self.staging, **{"scene.sog": "new"})
        _Ops()._swap_in_published(self.staging, self.final)
        self.assertFalse(self.final.with_name("published.previous").exists())
        self.assertEqual((self.final / "scene.sog").read_text(), "new")

    def test_staging_is_a_sibling_so_the_rename_cannot_cross_filesystems(self):
        """
        The whole guarantee rests on this: `os.replace` is atomic only within
        one filesystem, and it is only guaranteed to be one filesystem because
        staging sits beside the directory it replaces.
        """
        self._write(self.staging, **{"scene.sog": "new"})
        self.assertEqual(self.staging.parent, self.final.parent)
        self.assertEqual(
            os.stat(self.staging).st_dev, os.stat(self.final.parent).st_dev
        )
