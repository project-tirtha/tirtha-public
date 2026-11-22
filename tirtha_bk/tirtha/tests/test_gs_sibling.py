"""
The second published artifact, and why there is one.

Unit 5.4 made SOG the published format; unit 6.4 targeted a viewer that reads
it. The front end that shipped (internal design notes) reads `.splat`, `.spz` and
`.ply` and cannot read `.sog` at all -- so without a second artifact, every
newly reconstructed site reports "no 3D model yet" with the file on disk.

Three things have to be right, and each was wrong at some point:

- **The format has to be one splat-transform writes *and* the viewer reads.**
  `.splat` was rejected on the strength of 3.3.3's README, which marks it
  input-only and is wrong -- `writeSource` dispatches `case 'splat'` to
  `writeSplatStreaming`, and running the tool writes 32 bytes per gaussian.
  The replacement, `.spz`, encodes and publishes and then fails to load:
  splat-transform writes SPZ v4 and refuses below 3, while
  `@mkkellogg/gaussian-splats-3d` 0.4.7 rejects above 2.
- The encode has to happen.
- The result has to reach the published tree rather than the archive.

These are source greps, which is a weak instrument -- they pin the shape, not
the behaviour. The behaviour was settled by running splat-transform 3.3.3 and
reading the header of what it produced, and that is the only thing that settled
it: both wrong turns above came from reading documentation instead.
"""

from pathlib import Path

from django.test import SimpleTestCase

# `workers.py` is now the `workers/` package; its files, joined, are the same
# text the greps below were written against.
WORKERS = "\n".join(
    path.read_text()
    for path in sorted((Path(__file__).resolve().parents[1] / "workers").glob("*.py"))
)


class TheEncodeStep(SimpleTestCase):
    def test_it_asks_for_a_format_splat_transform_can_write(self):
        # Verified by running 3.3.3, not by reading it: the README's format
        # table marks `.splat` input-only and the tool writes one anyway.
        self.assertIn("st.to_splat(", WORKERS)

    def test_it_does_not_ask_for_spz(self):
        # splat-transform writes SPZ v4 and refuses below 3; the viewer rejects
        # above 2. A `.spz` sibling encodes, publishes, and fails to load --
        # which is the original bug, one layer further down.
        self.assertNotIn("st.to_spz(", WORKERS)
        self.assertNotIn('GS_SIBLING_SUFFIX = ".spz"', WORKERS)

    def test_the_suffix_is_one_the_viewer_reads(self):
        self.assertIn('GS_SIBLING_SUFFIX = ".splat"', WORKERS)

    def test_it_is_on_by_default(self):
        # Off by default would mean the feature exists and nothing uses it.
        self.assertIn('getattr(settings, "GS_EMIT_SIBLING", True)', WORKERS)


class ThePublishStep(SimpleTestCase):
    """
    The half that was missing.

    `run_cleanup` copies exactly one file out of the run directory and archives
    the rest, so the encoded sibling went to the archive and never reached the
    tree `api._sibling` looks in. The encode happened, the file existed, and the
    API still had nothing to offer.
    """

    def test_the_sibling_is_copied_beside_the_published_artifact(self):
        self.assertIn("dest.with_suffix(GS_SIBLING_SUFFIX)", WORKERS)

    def test_a_missing_sibling_fails_the_run(self):
        """
        Required, not best-effort (review 2026-11-22 §3). The viewer cannot
        read `.sog`, so a run published without its `.splat` showed an empty
        canvas while reporting success. A missing sibling now raises, and the
        sibling is copied *before* the `.sog`, so a failure cannot leave a new
        `.sog` published beside no `.splat`.

        Only the `.gz` beside the sibling stays best-effort. The behaviour is
        exercised in `test_recon_pipeline_fixes.PublishingAndRecut`; this pins
        the order of the steps.
        """
        cleanup = WORKERS[WORKERS.index("def _publish_artifacts") :]
        block = cleanup[cleanup.index("GS_SIBLING_SUFFIX") :]
        block = block[: block.index("self._atomic_copy(src, dest)")]
        self.assertIn("if not sibling_src.is_file():", block)
        self.assertIn("raise FileNotFoundError(", block)
        self.assertIn("self._atomic_copy(sibling_src, sibling_dest)", block)
        # The one `except OSError` left guards the precompress, not the copy.
        self.assertEqual(block.count("except OSError"), 1)
        self.assertLess(
            block.index("self._atomic_copy(sibling_src, sibling_dest)"),
            block.index("self._precompress(sibling_dest)"),
        )
        self.assertLess(
            block.index("self._precompress(sibling_dest)"),
            block.index("except OSError"),
        )

    def test_it_only_applies_to_gaussian_runs(self):
        # The photogrammetry path publishes a `.glb`, which the viewer reads
        # directly; there is nothing to pair it with.
        self.assertIn('if kind == "GS" and GS_EMIT_SIBLING:', WORKERS)


class TheViewerCanActuallyReadIt(SimpleTestCase):
    """
    The check that would have caught both wrong turns.

    Everything above is a source grep on our own code, which pins the shape and
    not the fact. The fact lives in two places we do not own -- what
    splat-transform writes and what the viewer reads -- and both were got wrong
    from documentation: `.splat` was abandoned because a README table said it
    was input-only (it is not), and `.spz` was adopted because both sides list
    it (they do, at versions that do not overlap).

    So this reads the *installed* viewer, not its docs.
    """

    LIBRARY = (
        Path(__file__).resolve().parents[3]
        / "frontend"
        / "node_modules"
        / "@mkkellogg"
        / "gaussian-splats-3d"
        / "build"
        / "gaussian-splats-3d.module.js"
    )

    def setUp(self):
        if not self.LIBRARY.is_file():
            self.skipTest("the viewer library is not installed (npm ci)")
        self.source = self.LIBRARY.read_text(errors="ignore")

    def test_the_sibling_suffix_is_a_format_the_loader_dispatches_on(self):
        import re

        suffix = re.search(r'GS_SIBLING_SUFFIX = "([^"]+)"', WORKERS).group(1)
        dispatch = re.findall(r"path\.endsWith\('(\.[a-z]+)'\)", self.source)
        self.assertIn(
            suffix,
            dispatch,
            f"the pipeline publishes {suffix}, which the viewer does not load. "
            f"It dispatches on {sorted(set(dispatch))}.",
        )

    def test_spz_is_not_offered_because_the_versions_do_not_meet(self):
        """
        The viewer does dispatch on `.spz`, so the check above would pass for it
        -- and it would still be broken. Its loader bounds the format version,
        and splat-transform's floor is above that ceiling.

        Pinned as a fact about the installed library so that if a release ever
        raises the bound, this test says so rather than silently allowing an
        `.spz` nobody re-checked.
        """
        import re

        bound = re.search(
            r"header\.version < (\d+) \|\| header\.version > (\d+)", self.source
        )
        self.assertIsNotNone(bound, "the SPZ version check has moved or gone")
        low, high = int(bound.group(1)), int(bound.group(2))

        # splat-transform 3.3.3: `--spz-version <3|4>`, default 4.
        writable_floor = 3
        self.assertLess(
            high,
            writable_floor,
            "the viewer now reads an SPZ version splat-transform can write "
            f"(reads {low}-{high}, writes {writable_floor}+). Re-evaluate "
            "GS_SIBLING_SUFFIX and VIEWER_ARTIFACT_FORMATS.",
        )
