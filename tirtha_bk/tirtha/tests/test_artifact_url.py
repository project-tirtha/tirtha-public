"""
Offering a viewer a file it can actually open (internal design notes).

Unit 5.4 made SOG the published GS format and 6.4 replaced the viewer with one
that reads `.glb` and `.sog`. Neither converted what was already on disk: at the
2026-08-29 snapshot 56 of 101 ARKs pointed at `.splat` and no `.sog` existed
anywhere, so 24 of the 30 published sites rendered an empty canvas.

`_artifact_url` now offers a converted `.sog` where one has been made. The rule
these tests exist to hold is that **the ARK is never rewritten** -- it is a
persistence commitment recorded on the row, and it keeps resolving to the file
it always named. Only what the API hands a viewer changes.
"""

import re
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from tirtha.api import _artifact_url, _sibling

BASE = "https://smlab.niser.ac.in/project/tirtha/static/models/abc/published"
SPLAT = f"{BASE}/abc_run.splat"
SOG = f"{BASE}/abc_run.sog"
GLB = f"{BASE}/abc_run.glb"


class _Root(SimpleTestCase):
    """A static tree with one converted artifact in it."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.published = Path(self.root) / "models" / "abc" / "published"
        self.published.mkdir(parents=True)

    def convert(self, name="abc_run.sog"):
        (self.published / name).write_bytes(b"sog")


# Every test in this class asks what a **SOG-reading** viewer is offered.
@override_settings(VIEWER_ARTIFACT_FORMAT="sog")
class SogSiblingTests(_Root):
    def test_a_converted_sibling_is_offered(self):
        self.convert()
        with override_settings(ARTIFACT_ROOT=self.root):
            self.assertEqual(_artifact_url(SPLAT), f"{BASE}/abc_run.sog")

    def test_nothing_is_offered_when_no_conversion_exists(self):
        """The common case before the batch runs, and it must be a no-op."""
        with override_settings(ARTIFACT_ROOT=self.root):
            self.assertEqual(_artifact_url(SPLAT), SPLAT)

    def test_unset_root_rewrites_nothing(self):
        """
        The default. Pointing this at the wrong tree would offer a URL that
        404s, so silence is the safe behaviour, not a guess.
        """
        self.convert()
        with override_settings(ARTIFACT_ROOT=""):
            self.assertEqual(_artifact_url(SPLAT), SPLAT)

    def test_a_glb_is_left_alone(self):
        with override_settings(ARTIFACT_ROOT=self.root):
            self.assertEqual(_artifact_url(GLB), GLB)

    def test_a_url_with_no_static_segment_is_left_alone(self):
        with override_settings(ARTIFACT_ROOT=self.root):
            odd = "https://example.org/elsewhere/abc_run.splat"
            self.assertEqual(_artifact_url(odd), odd)

    def test_the_prefix_of_the_ark_url_is_preserved(self):
        """
        ARK URLs carry the prefix of whichever deployment minted them. The
        rewrite swaps the extension and nothing else -- inventing a prefix here
        is how a demo ends up serving 404s.
        """
        self.convert()
        with override_settings(ARTIFACT_ROOT=self.root):
            out = _artifact_url(SPLAT)
        self.assertTrue(out.startswith("https://smlab.niser.ac.in/project/tirtha/"))
        self.assertEqual(out.rsplit("/", 1)[-1], "abc_run.sog")

    def test_case_is_not_a_way_to_miss_a_conversion(self):
        with override_settings(ARTIFACT_ROOT=self.root):
            self.assertIsNone(_sibling(GLB.upper(), ".sog", ".splat"))

    def test_a_traversal_in_the_url_cannot_escape_the_root(self):
        """The URL is data from the database; treat it as untrusted input."""
        with override_settings(ARTIFACT_ROOT=self.root):
            evil = "https://x/static/../../../../etc/passwd.splat"
            self.assertEqual(_artifact_url(evil), evil)


@override_settings(VIEWER_ARTIFACT_FORMAT="splat")
class SplatSiblingTests(_Root):
    """
    The other direction, which is the one this deployment is in.

    5.4 made SOG the published format and 6.4 targeted a viewer that reads it;
    the UI now shipping reads `.splat` natively and cannot read `.sog` at all
    (internal design notes). Every newly reconstructed site publishes a `.sog`, so
    without this each one would report "no 3D model yet" in the interface people
    use, while the file sat on disk.

    Rewriting in one fixed direction is wrong for exactly half of the
    deployments, which is why the direction is now read from
    `VIEWER_ARTIFACT_FORMAT` rather than compiled in.
    """

    def test_a_legacy_sibling_is_offered_for_a_sog_ark(self):
        (self.published / "abc_run.splat").write_bytes(b"splat")
        with override_settings(ARTIFACT_ROOT=self.root):
            self.assertEqual(_artifact_url(SOG), SPLAT)

    def test_a_splat_ark_is_left_alone(self):
        # It is already the format this viewer reads.
        self.convert()
        with override_settings(ARTIFACT_ROOT=self.root):
            self.assertEqual(_artifact_url(SPLAT), SPLAT)

    def test_nothing_is_offered_when_no_legacy_copy_exists(self):
        with override_settings(ARTIFACT_ROOT=self.root):
            self.assertEqual(_artifact_url(SOG), SOG)

    def test_the_two_directions_do_not_both_apply(self):
        """
        Both files present. A viewer must be offered exactly one of them, and it
        must be the one it can read -- not whichever the code happened to try
        first.
        """
        self.convert()
        (self.published / "abc_run.splat").write_bytes(b"splat")
        with override_settings(ARTIFACT_ROOT=self.root):
            self.assertEqual(_artifact_url(SOG), SPLAT)
            self.assertEqual(_artifact_url(SPLAT), SPLAT)
        with override_settings(ARTIFACT_ROOT=self.root, VIEWER_ARTIFACT_FORMAT="sog"):
            self.assertEqual(_artifact_url(SPLAT), SOG)
            self.assertEqual(_artifact_url(SOG), SOG)


@override_settings(VIEWER_ARTIFACT_FORMAT="sog")
class ExplicitRewriteTests(_Root):
    def test_an_explicit_rewrite_still_wins(self):
        """A deployment holding copies somewhere unrelated overrides the rule."""
        self.convert()
        with override_settings(
            ARTIFACT_ROOT=self.root,
            DEMO_ARTIFACT_REWRITES=[(SPLAT, "https://elsewhere/x.sog")],
        ):
            self.assertEqual(_artifact_url(SPLAT), "https://elsewhere/x.sog")

    def test_containment_is_enforced_not_merely_lucky(self):
        """
        The traversal case above passes because the target does not exist.
        This one puts a real file outside the root and proves the check is
        what refuses it.
        """
        outside = Path(tempfile.mkdtemp())
        (outside / "escaped.sog").write_bytes(b"sog")
        depth = len(Path(self.root).resolve().parts)
        climb = "/".join([".."] * (depth + 2))
        url = f"https://x/static/{climb}{outside}/escaped.splat"
        with override_settings(ARTIFACT_ROOT=self.root):
            self.assertEqual(_artifact_url(url), url)


class ViewerFormatCoupling(SimpleTestCase):
    """
    The rewrites are only an improvement for a viewer that can read the target.

    `.sog` was converted for the PlayCanvas GSplat viewer (internal design notes). The
    React UI now shipping reads `.splat` natively through
    `@mkkellogg/gaussian-splats-3d` and cannot read `.sog` at all -- so with the
    rewrites on, all 56 converted runs are offered to a viewer that skips them,
    and sites with a published model since 2023 report "no 3D model yet".

    Worth a test rather than a comment because the two halves are in different
    repositories' worth of code: a settings module and a React component. The
    only thing that connects them is somebody remembering.
    """

    def _demo_settings(self) -> str:
        return (
            Path(__file__).resolve().parents[2] / "tirtha_bk" / "local_settings_demo.py"
        ).read_text()

    def _service(self) -> str:
        return (
            Path(__file__).resolve().parents[3]
            / "frontend"
            / "src"
            / "services"
            / "sites.service.js"
        ).read_text()

    def test_the_demo_rewrites_are_gated_on_the_viewers_formats(self):
        source = self._demo_settings()
        self.assertIn(
            '".sog" in VIEWER_ARTIFACT_FORMATS',
            source,
            "the demo's SOG rewrites are no longer gated on what the viewer reads",
        )

    def test_the_two_sides_agree_on_what_the_viewer_reads(self):
        """
        The settings module and the front end describe the same fact.

        Nothing connects them at runtime, and getting them out of step is
        silent: the API hands over a format the client then skips, and every
        affected site reports "no 3D model yet" with the file on disk.
        """
        server = set(
            re.findall(
                r'"(\.[a-z]+)"',
                self._demo_settings()
                .split("VIEWER_ARTIFACT_FORMATS = (")[1]
                .split(")")[0],
            )
        )
        client = set(
            re.findall(
                r"'(\.[a-z]+)'",
                self._service().split("const VIEWABLE = [")[1].split("]")[0],
            )
        )
        self.assertEqual(server, client, "the server and the client disagree")

    def test_the_frontend_does_not_claim_to_read_sog(self):
        """
        The whole reason `run_postprocess` publishes a second artifact.
        `@mkkellogg/gaussian-splats-3d` has no SOG loader; claiming otherwise
        would mean offering it a file it opens as an empty canvas.
        """
        self.assertNotIn("'.sog'", self._service())


class ViewerFormatSet(_Root):
    """
    The archive holds four formats; the viewer reads three of them.

    A single "which format" setting could not express that, and the version that
    tried came in two halves that disagreed: `_artifact_url` hardcoded
    `.splat` -> `.sog`, while the demo's rewrite list went the same way
    unconditionally. What a deployment actually knows is *what its viewer can
    draw*, so that is what it configures.
    """

    def publish(self, name, body=b"x"):
        (self.published / name).write_bytes(body)

    def test_a_sog_run_is_offered_its_splat_sibling(self):
        """
        The case every new reconstruction is in. `run_postprocess` publishes
        `.splat` beside `.sog` precisely so this can happen.
        """
        self.publish("abc_run.splat")
        with override_settings(ARTIFACT_ROOT=self.root):
            self.assertEqual(_artifact_url(SOG), SPLAT)

    def test_an_spz_is_not_offered_by_default(self):
        """
        The viewer has an SPZ loader and still cannot use one from this
        pipeline: it accepts versions 1-2, splat-transform writes 4 and refuses
        below 3. Offering it turns "no model" into "failed to load", which is
        further from working, not closer.
        """
        self.publish("abc_run.spz")
        with override_settings(ARTIFACT_ROOT=self.root):
            self.assertEqual(_artifact_url(SOG), SOG)

    def test_an_already_readable_ark_is_untouched(self):
        self.publish("abc_run.sog")
        with override_settings(ARTIFACT_ROOT=self.root):
            self.assertEqual(_artifact_url(SPLAT), SPLAT)
            self.assertEqual(_artifact_url(GLB), GLB)

    def test_preference_order_decides_when_several_siblings_exist(self):
        self.publish("abc_run.splat")
        self.publish("abc_run.ply")
        with override_settings(
            ARTIFACT_ROOT=self.root,
            VIEWER_ARTIFACT_FORMATS=(".ply", ".splat"),
        ):
            self.assertEqual(_artifact_url(SOG), f"{BASE}/abc_run.ply")
        with override_settings(
            ARTIFACT_ROOT=self.root,
            VIEWER_ARTIFACT_FORMATS=(".splat", ".ply"),
        ):
            self.assertEqual(_artifact_url(SOG), SPLAT)

    def test_a_sog_reading_deployment_gets_the_sog(self):
        # The direction the previous implementation hardcoded, still available.
        self.publish("abc_run.sog")
        with override_settings(
            ARTIFACT_ROOT=self.root, VIEWER_ARTIFACT_FORMATS=(".sog", ".glb")
        ):
            self.assertEqual(_artifact_url(SPLAT), SOG)

    def test_the_older_single_value_setting_still_works(self):
        self.publish("abc_run.sog")
        with override_settings(ARTIFACT_ROOT=self.root, VIEWER_ARTIFACT_FORMAT="sog"):
            self.assertEqual(_artifact_url(SPLAT), SOG)

    def test_nothing_readable_serves_the_ark_verbatim(self):
        """
        Better a file the viewer cannot draw, with the download links beside it,
        than a URL that 404s.
        """
        with override_settings(ARTIFACT_ROOT=self.root):
            self.assertEqual(_artifact_url(SOG), SOG)

    def test_a_url_with_no_extension_is_left_alone(self):
        with override_settings(ARTIFACT_ROOT=self.root):
            bare = (
                "https://smlab.niser.ac.in/project/tirtha/static/models/abc/published/x"
            )
            self.assertEqual(_artifact_url(bare), bare)
