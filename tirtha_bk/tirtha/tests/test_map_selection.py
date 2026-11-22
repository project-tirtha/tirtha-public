"""
Two defects that were invisible to every test we had, and share one cause.

Both live on the *marker* path — clicking a pin — while the same site opened
from a shared link behaved correctly. Every check written so far starts from a
URL, so neither showed up.

`templeFeatures` used to serialise a hand-picked subset of each site into the
GeoJSON feature's `templeData` property and `JSON.parse` it back on click. The
subset was written once and fell behind the record it was copying:

* it lost `slug`, so `openTempleContribute` got `siteSlug: null` and the upload
  button was permanently disabled for any site reached by clicking its pin —
  which is how nearly everyone reaches one, and uploading is what this platform
  is for;
* it lost `descriptionHtml`, so the 27 sites with markup in their description
  rendered `</p> <ol> <li><a href=…>` as text on that path — the very defect
  internal design notes records as fixed, still live on two thirds of the
  ways in.

So what is pinned here is not "remember to add slug". It is that the feature
carries an id and nothing else that can go stale, and that one function opens a
site.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

FRONTEND = Path(__file__).resolve().parents[3] / "frontend" / "src"


class MapFeatureProperties(SimpleTestCase):
    def setUp(self):
        self.source = (FRONTEND / "components" / "MapView.jsx").read_text()

    def test_the_site_is_not_serialised_into_the_feature(self):
        self.assertNotIn("JSON.stringify(resolvedTemple)", self.source)
        self.assertNotRegex(self.source, r"templeData:\s*JSON\.stringify")

    def test_the_site_is_not_parsed_back_out_of_the_feature(self):
        self.assertNotIn("JSON.parse(feature.properties", self.source)

    def test_the_marker_looks_the_site_up_by_id(self):
        self.assertRegex(
            self.source, r"templeByIdRef\.current\.get\(feature\.properties\.id\)"
        )

    def test_the_feature_carries_only_what_the_map_renders(self):
        """
        `id` to find the site by, and `completionStatus` because the cluster
        expressions aggregate on it. Anything else is a copy waiting to drift.
        """
        block = re.search(r"properties: \{([\s\S]{0,240}?)\},", self.source)
        self.assertIsNotNone(block, "the feature's properties block has moved")
        keys = set(re.findall(r"^\s+([a-zA-Z]+)[,:]", block.group(1), re.M))
        self.assertEqual(keys, {"id", "completionStatus"})


class OneWayToOpenASite(SimpleTestCase):
    """
    The route effect, the marker click and the search result each ran their own
    subset of the same five steps, in their own order, and the differences were
    the bugs.

    The marker path navigated and the search path did not — so with `/site/A`
    open, choosing B from search set `selectedTemple` to B while `initialSiteId`
    stayed A, the route effect saw the mismatch and put A back. You searched for
    Konark and landed on Puri.
    """

    def setUp(self):
        self.source = (FRONTEND / "pages" / "Home.jsx").read_text()

    def test_there_is_one_function_that_opens_a_site(self):
        self.assertRegex(self.source, r"const selectSite = \(")

    def test_every_entry_point_goes_through_it(self):
        """
        Three callers: the route effect, the marker click, and search. (The
        declaration is `const selectSite = (`, which this pattern deliberately
        does not count.)
        """
        callers = re.findall(r"(?<!= )selectSite\(", self.source)
        self.assertGreaterEqual(len(callers), 3, "an entry point bypasses selectSite")

    def test_only_one_place_selects_a_site_wholesale(self):
        """
        Counting the *replacement* form specifically.

        `setSelectedTemple((current) => ...)` is a different operation -- the
        resolvers merging fetched runs or a resolved place name into whatever is
        already open -- and there are three of those, legitimately.
        `setSelectedTemple(null)` is closing. What must not proliferate is
        `setSelectedTemple({...})`, which is "open this site": that is the shape
        the three paths each grew their own version of.

        Two are expected: inside `selectSite`, and the custom-coordinate branch
        of search, which opens the request form rather than a viewer and has no
        URL to navigate to.
        """
        wholesale = re.findall(r"setSelectedTemple\(\{", self.source)
        self.assertLessEqual(
            len(wholesale),
            2,
            "a third place now opens a site wholesale -- route it through "
            "`selectSite` instead",
        )

    def test_clicking_a_marker_puts_the_site_in_the_url(self):
        self.assertIn("temple.slug", self.source)
        self.assertRegex(self.source, r"navigate\(\s*\n?\s*temple\.slug")

    def test_responding_to_the_url_does_not_navigate_again(self):
        # Otherwise every deep link pushes a redundant entry over itself.
        self.assertIn("navigateTo: false", self.source)


class TheContributeFlowCanReachASlug(SimpleTestCase):
    """
    The consequence, stated where someone changing either half will see it:
    `preUpload/` and `upload/` identify a site by `mesh_vid`, which the API
    publishes as `slug`. No slug, no upload.
    """

    def test_the_slug_survives_to_the_contribute_modal(self):
        home = (FRONTEND / "pages" / "Home.jsx").read_text()
        self.assertRegex(home, r"siteSlug: temple\?\.slug")
        # The dialog hands the slug to the batched-upload module, which writes
        # it into every request as `mesh_vid`.
        modal = (FRONTEND / "components" / "ContributeModal.jsx").read_text()
        self.assertIn("siteSlug,", modal)
        sender = (FRONTEND / "utils" / "batchUpload.js").read_text()
        self.assertIn("'mesh_vid'", sender)

    def test_normalize_site_publishes_the_slug_and_the_sanitised_html(self):
        service = (FRONTEND / "services" / "sites.service.js").read_text()
        self.assertIn("slug: site.slug", service)
        self.assertIn("descriptionHtml: site.description_html", service)
