"""
The viewer is handed the smallest format it can read.

`VIEWER_ARTIFACT_FORMATS` is "what this deployment's viewer understands", best
first. `_artifact_url` returned the ARK's own URL the moment it ended in *any*
readable format, so the list ordered the fallbacks and nothing else: an ARK
naming a 288 MB `.splat` was served that `.splat` even with a 146 MB
`.compressed.ply` of the same run sitting beside it.

`.compressed.ply` is PlayCanvas's chunked, quantised PLY. The shipped viewer
reads it with no change -- `PlyParserUtils` sniffs `element chunk` in the header
and dispatches to `PlayCanvasCompressedPlyParser`.

**"A uniform 50% of the `.splat`" stopped being true.** That held across the six
models it was measured on, 1 MB to 288 MB. At 1.5M Gaussians with full spherical
harmonics the same encoding came out at 91.9 MB against the `.splat`'s 48.0 MB,
so a fixed order chose the largest readable file. The choice is made by
measuring now, and these tests therefore write files of *different* sizes: with
equal sizes every assertion below passes on the rank tie-break alone and proves
nothing.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings

from tirtha.api import _artifact_url

BASE = "https://example.org/project/tirtha/static/models/S1/published/run"


class TheSmallestReadableFormatWins(TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.published = self.root / "models" / "S1" / "published"
        self.published.mkdir(parents=True)

    def _write(self, *names):
        """Equal-size files, for the tests that are about availability."""
        for name in names:
            (self.published / name).write_bytes(b"x")

    def _write_named(self, **sizes):
        """
        Files of given sizes. `run__compressed__ply=432` writes
        `run.compressed.ply` of 432 bytes.

        Size has to vary for a test of size selection to mean anything: with
        every file one byte long, the rank tie-break decides and the assertion
        passes whatever the selection logic does.
        """
        for key, size in sizes.items():
            (self.published / key.replace("__", ".")).write_bytes(b"x" * size)

    def _url(self, url):
        with override_settings(
            ARTIFACT_ROOT=str(self.root),
            VIEWER_ARTIFACT_FORMATS=(".compressed.ply", ".splat", ".ply", ".glb"),
        ):
            return _artifact_url(url)

    def test_a_splat_ark_is_offered_a_smaller_compressed_ply(self):
        self._write_named(run__splat=200, run__compressed__ply=100)
        self.assertTrue(self._url(f"{BASE}.splat").endswith(".compressed.ply"))

    def test_a_splat_ark_keeps_its_splat_when_the_compressed_ply_is_larger(self):
        """
        The 2026-09-12 inversion. `.compressed.ply` is first in the configured
        order, and at high density it is nearly twice the `.splat`.
        """
        self._write_named(run__splat=100, run__compressed__ply=200)
        self.assertTrue(self._url(f"{BASE}.splat").endswith(".splat"))

    def test_a_compressed_ply_ark_is_offered_a_smaller_splat(self):
        """
        `.compressed.ply` ends with `.ply`.

        The `.ply` iteration claimed such a URL as "its own format", could not
        measure it -- the stem it built named a different, absent file -- and
        returned the URL unchanged, discarding the smaller candidates already
        collected. Measured on a real site: a 432,327 B `.compressed.ply` served
        with a 224,800 B `.splat` beside it.
        """
        self._write_named(run__compressed__ply=432, run__splat=224)
        self.assertTrue(self._url(f"{BASE}.compressed.ply").endswith(".splat"))

    def test_a_sog_ark_is_offered_the_smallest_readable_sibling(self):
        """The one ARK in the archive that names a `.sog`."""
        self._write_named(run__sog=300, run__splat=100, run__compressed__ply=200)
        self.assertTrue(self._url(f"{BASE}.sog").endswith(".splat"))

    def test_equal_sizes_fall_back_to_the_configured_order(self):
        self._write_named(run__splat=100, run__compressed__ply=100)
        self.assertTrue(self._url(f"{BASE}.splat").endswith(".compressed.ply"))

    def test_without_one_the_splat_is_still_served(self):
        self._write_named(run__splat=100)
        self.assertTrue(self._url(f"{BASE}.splat").endswith(".splat"))

    def test_a_missing_own_file_still_resolves_to_a_sibling(self):
        """
        A legacy `.splat` ARK whose file is gone but whose sibling is not.

        Nothing measurable means nothing to compare, and the answer must still
        be the readable file that exists rather than the URL that 404s.
        """
        self._write_named(run__compressed__ply=100)
        self.assertTrue(self._url(f"{BASE}.splat").endswith(".compressed.ply"))

    def test_a_mesh_is_never_answered_with_a_splat(self):
        """
        `.glb` comes from photogrammetry and the splats from Gaussian
        splatting. Both can sit in one directory, published by different runs.
        Only the encoding may improve, never the thing encoded.
        """
        self._write_named(run__glb=900, run__splat=1)
        self.assertTrue(self._url(f"{BASE}.glb").endswith(".glb"))

    def test_no_artifact_root_changes_nothing(self):
        self._write_named(run__splat=200, run__compressed__ply=100)
        with override_settings(ARTIFACT_ROOT=""):
            url = f"{BASE}.splat"
            self.assertEqual(_artifact_url(url), url)

    def test_a_glb_is_never_rewritten_to_a_splat(self):
        """Meshes and splats are not interchangeable; only size may improve."""
        self._write("run.glb", "run.splat")
        self.assertTrue(self._url(f"{BASE}.glb").endswith(".glb"))

    def test_nothing_readable_leaves_the_ark_verbatim(self):
        self._write("run.sog")
        self.assertEqual(self._url(f"{BASE}.sog"), f"{BASE}.sog")

    def test_the_ark_url_itself_is_never_changed(self):
        """
        An ARK is a persistence commitment recorded on the row. This changes
        what the API *offers*, never what the identifier resolves to.
        """
        from tirtha.models import ARK

        self._write("run.splat", "run.compressed.ply")
        ark = ARK.objects.create(
            ark="74218/t1pref",
            naan="74218",
            shoulder="/t1",
            assigned_name="pref",
            url=f"{BASE}.splat",
            metadata={},
            commitment="Permanent, unambiguous identifier.",
        )
        offered = self._url(ark.url)
        self.assertTrue(offered.endswith(".compressed.ply"))
        self.assertEqual(ARK.objects.get(ark=ark.ark).url, f"{BASE}.splat")

    def test_preference_order_is_honoured_not_just_availability(self):
        """Both exist; the one listed first is the one handed over."""
        self._write("run.splat", "run.compressed.ply", "run.ply")
        self.assertTrue(self._url(f"{BASE}.sog").endswith(".compressed.ply"))
