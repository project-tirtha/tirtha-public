"""
Guards on how the frontend is built and deployed (internal design notes).

Both of these encode the same incident. The demo was rebuilt without
`TIRTHA_STATIC_BASE`, so Vite baked production's `/project/tirtha/static/` into
every lazily-imported chunk. Django rewrites the *entry* paths from the
manifest, so the page looked fine and returned 200 -- but the map chunk 404'd,
the `lazy()` import rejected, and with no error boundary anywhere the whole app
rendered white. Nothing appeared in the server log.
"""

import json
import re
import unittest
from pathlib import Path

from django.test import TestCase

REPO = Path(__file__).resolve().parents[3]
FRONTEND = REPO / "frontend" / "src"
BUILD = REPO / "build"


@unittest.skipUnless(
    (BUILD / "refresh-demo.sh").exists(),
    "build/refresh-demo.sh is internal (not in the public tree)",
)
class AssetBaseTests(TestCase):
    def test_the_demo_build_script_sets_the_asset_base(self):
        # vite.config.ts defaults `base` to production's prefix. Any deployment
        # script that does not override it ships chunks pointing at /project/tirtha/.
        script = (BUILD / "refresh-demo.sh").read_text()
        self.assertIn("TIRTHA_STATIC_BASE", script)
        self.assertRegex(script, r'export TIRTHA_STATIC_BASE="/\$\{PRE_URL\}static/"')

    def test_the_demo_build_script_verifies_what_it_built(self):
        # A bundle built against the wrong prefix looks completely normal until
        # a lazy route is reached in a browser, so the script checks rather than
        # trusting the export.
        script = (BUILD / "refresh-demo.sh").read_text()
        self.assertIn("Not deploying", script)


def _strip_comments(text: str) -> str:
    """
    Source with comments removed, for tests that compare positions.

    A comment mentioning `<Routes>` while explaining why the boundary sits
    outside it would otherwise fail the ordering check -- the test would be
    reading prose as if it were markup.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


def _components(root=None):
    """
    Every shipped component, whichever extension this front end uses.

    Test files are excluded, and that exclusion is the point rather than tidiness.
    These checks are about what the browser loads: that the route tree the app
    renders is wrapped, and that a lazily imported chunk which 404s reports
    itself instead of blanking the page. A test that renders `<Routes>` to
    exercise routing, or mounts a lazy component behind a probe, is neither --
    it is scaffolding that never reaches a user.

    Without this, `Home.test.jsx` rendering `<Routes>` to prove a run can be
    chosen from the URL failed `test_the_route_tree_is_wrapped`, and the only
    ways to pass were to wrap a test in an ErrorBoundary that catches nothing,
    or to stop testing the routing. Both make the suite worse to satisfy a
    check that was measuring the wrong files.
    """
    root = root or FRONTEND
    for pattern in ("*.jsx", "*.tsx"):
        for path in root.rglob(pattern):
            if ".test." in path.name or ".spec." in path.name:
                continue
            yield path


class ErrorBoundaryTests(TestCase):
    def test_an_error_boundary_exists(self):
        found = [p for p in _components() if p.stem == "ErrorBoundary"]
        self.assertTrue(found, "no ErrorBoundary component")

    def test_the_route_tree_is_wrapped(self):
        """
        Whichever component declares `<Routes>` must wrap it.

        Ordering matters: a boundary *inside* the route tree cannot catch a
        route element that fails to load, which is the case that matters --
        a lazily imported chunk that 404s renders a blank page and nothing
        else reports it.
        """
        declaring = [
            p for p in _components() if "<Routes>" in _strip_comments(p.read_text())
        ]
        self.assertTrue(declaring, "nothing declares <Routes>")
        for path in declaring:
            with self.subTest(component=path.name):
                text = _strip_comments(path.read_text())
                self.assertIn("<ErrorBoundary>", text)
                self.assertLess(text.index("<ErrorBoundary>"), text.index("<Routes>"))

    def test_every_lazy_import_sits_under_a_boundary(self):
        """
        A `<Suspense>` fallback covers the pending state, not the rejected one.
        Only an error boundary catches a chunk that fails to load.

        The boundary has to be *local* to the lazy import, not merely somewhere
        above it. The root boundary would catch the failure and take the whole
        page down with it; a scoped one leaves the rest of the page usable while
        the piece that failed reports itself.
        """
        for path in _components():
            text = path.read_text()
            if not re.search(r"\blazy\(", text):
                continue
            with self.subTest(component=path.name):
                own = "ErrorBoundary" in text
                # App wraps the whole tree, so a page it renders is covered.
                covered = own or path.stem in ("App", "Approutes")
                self.assertTrue(
                    covered, f"{path.name} lazy-imports without an ErrorBoundary"
                )


class LocaleTests(TestCase):
    def test_the_error_panel_speaks_every_language(self):
        locales = FRONTEND / "i18n" / "locales"
        for code in ("en", "or", "hi"):
            with self.subTest(lang=code):
                data = json.loads((locales / f"{code}.json").read_text())
                self.assertIn("error", data)
                for key in ("title", "body", "reload"):
                    self.assertIn(key, data["error"])


class RuntimeAssetPaths(TestCase):
    """
    Assets fetched at runtime must go through the bundle's base.

    Vite rewrites the paths it can see -- an `import`, a `url()` in a
    stylesheet, a `<img src>` in JSX. It cannot see a string handed to `fetch`.
    So `fetch('/India/INDIA_STATES.geojson')` stayed a domain-root path, while
    the file it names is served from `/project/tirtha/static/India/...`, and the
    state outlines never drew on any real deployment. The `catch` reported it
    only in a dev build, where the path happens to be right.

    `import.meta.env.BASE_URL` is what Vite substitutes at build time, and it
    tracks `TIRTHA_STATIC_BASE` -- so one build serves production, beta and the
    demo, which is the whole reason the base is configurable.
    """

    # `/api/...` is the API, which is a Django route and correctly absolute --
    # `getApiBaseUrl()` and `deploymentPrefix()` handle its prefix, and
    # `test_frontend_endpoints` resolves those against `urls.py`.
    STATIC_ROOT_FETCH = re.compile(
        r"""fetch\(\s*['"`](/(?!api/)[A-Za-z0-9_][A-Za-z0-9_/.-]*)"""
    )

    def test_no_component_fetches_from_the_domain_root(self):
        offenders = []
        for path in _components():
            if ".test." in path.name:
                continue
            for match in self.STATIC_ROOT_FETCH.finditer(
                _strip_comments(path.read_text())
            ):
                offenders.append(f"{path.name}: {match.group(1)}")

        self.assertEqual(
            offenders,
            [],
            "these are fetched from the domain root rather than the asset base:\n  "
            + "\n  ".join(offenders),
        )

    def test_the_public_asset_that_prompted_this_still_goes_through_the_base(self):
        """Guards the check above from passing because the fetch was deleted."""
        source = (FRONTEND / "components" / "MapView.jsx").read_text()
        self.assertIn("import.meta.env.BASE_URL", source)
        self.assertIn("INDIA_STATES.geojson", source)
