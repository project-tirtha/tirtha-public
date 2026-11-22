"""
The 23-minute outage, as a test.

`npm run build` writes new hashes; a request asks whether one has been
collected and the answer is cached; `collectstatic` then copies it. If the
negative answer was cached, the shell keeps serving the "not built" notice
until the process is restarted -- which is what happened, twice.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase

from tirtha import vite


class ANegativeAnswerIsNeverCached(TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        vite._COLLECTED.clear()
        self.addCleanup(vite._COLLECTED.clear)

    def test_it_recovers_once_the_file_is_collected(self):
        rel = "assets/index-abc123.js"
        self.assertFalse(vite._is_collected(str(self.root), rel))

        # collectstatic runs.
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("//")

        # No restart, no cache clearing: the next request must see it.
        self.assertTrue(vite._is_collected(str(self.root), rel))

    def test_a_positive_answer_is_remembered(self):
        rel = "assets/index-def456.js"
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("//")
        self.assertTrue(vite._is_collected(str(self.root), rel))
        self.assertIn((str(self.root), rel), vite._COLLECTED)

        # Still true from cache even if the file goes away, which cannot
        # happen inside one process's life and is the reason caching it is safe.
        target.unlink()
        self.assertTrue(vite._is_collected(str(self.root), rel))

    def test_no_static_root_means_nothing_to_be_out_of_sync_with(self):
        self.assertTrue(vite._is_collected("", "assets/whatever.js"))
        self.assertTrue(vite._is_collected("   ", "assets/whatever.js"))

    def test_the_cache_is_bounded(self):
        (self.root / "assets").mkdir(parents=True, exist_ok=True)
        for i in range(vite._COLLECTED_CAP + 5):
            rel = f"assets/f{i}.js"
            (self.root / rel).write_text("//")
            vite._is_collected(str(self.root), rel)
        self.assertLessEqual(len(vite._COLLECTED), vite._COLLECTED_CAP)
