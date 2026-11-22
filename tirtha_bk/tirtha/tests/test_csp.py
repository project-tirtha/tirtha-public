"""
Content-Security-Policy (internal design notes).

There was no CSP at all before this: `SecurityHeadersMiddleware` set
Referrer-Policy, COEP, COOP and CORP and stopped there. That mattered more once
the map shipped, because the map is the first page that loads anything from a
third party -- raster tiles from two tile hosts.

The policy is enforced on the React front end and report-only everywhere else.
That split is the whole design and is what these tests are mostly about: the
legacy templates load jQuery, three.js and model-viewer from unpkg and run
inline scripts, and the Django admin has its own inline handlers, so one
enforced policy would either break the operator UI or be watered down until it
meant nothing.

What is asserted here is the *shape* of the policy -- which directives are
enforced where, and which sources are absent -- rather than a byte-for-byte
string, so adding a directive does not fail every test.
"""

import re
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse

from tirtha.middleware import TILE_ORIGINS

ENFORCED = "Content-Security-Policy"
REPORT = "Content-Security-Policy-Report-Only"


def directive(policy: str, name: str) -> str:
    for part in policy.split(";"):
        part = part.strip()
        if part.split(" ")[0] == name:
            return part
    return ""


class PolicyContentTests(TestCase):
    """What the policy allows, asserted against what the bundle actually needs."""

    def setUp(self):
        self.policy = self.client.get(reverse("shell"))[ENFORCED]

    def test_scripts_are_first_party_only(self):
        scripts = directive(self.policy, "script-src")
        self.assertIn("'self'", scripts)
        self.assertNotIn("'unsafe-inline'", scripts)
        # 'wasm-unsafe-eval' is needed by the PlayCanvas viewer; the broad
        # 'unsafe-eval' is not, and would re-enable eval for the whole page.
        self.assertIn("'wasm-unsafe-eval'", scripts)
        self.assertNotIn("'unsafe-eval'", scripts.replace("'wasm-unsafe-eval'", ""))

    def test_tile_hosts_are_allowed_as_images_and_as_fetches(self):
        """MapLibre fetches raster tiles with `fetch()` where it can."""
        for origin in TILE_ORIGINS:
            self.assertIn(origin, directive(self.policy, "img-src"))
            self.assertIn(origin, directive(self.policy, "connect-src"))

    def test_maplibre_can_start_its_blob_worker(self):
        """Without this the map initialises and then renders nothing."""
        self.assertIn("blob:", directive(self.policy, "worker-src"))

    def test_style_attributes_are_allowed_but_stylesheets_are_not_inline(self):
        """
        The cluster pie is a computed `conic-gradient` written to a style
        attribute, which `style-src-attr` governs -- not `style-src`.
        """
        self.assertIn("'unsafe-inline'", directive(self.policy, "style-src-attr"))
        self.assertNotIn("'unsafe-inline'", directive(self.policy, "style-src"))

    def test_the_usual_hardening_directives_are_present(self):
        self.assertIn("'none'", directive(self.policy, "object-src"))
        self.assertIn("'self'", directive(self.policy, "base-uri"))
        self.assertIn("'self'", directive(self.policy, "default-src"))

    def test_google_is_a_navigation_target_and_nothing_more(self):
        """
        The weakening this used to record has been undone.

        For a while the newer UI ran Google Identity Services: a script from
        accounts.google.com, its button in an iframe, an XHR back with the
        id_token. That needed the origin in three directives instead of one, and
        this test asserted the three -- a real reduction in the policy's
        strength, recorded as one.

        The flow never worked (it posted to an endpoint this backend does not
        have; sign-in here is a server-side authlib redirect), and it is gone.
        So `script-src` names no third party again, and Google is what it was
        before: somewhere the browser navigates to.
        """
        self.assertIn(
            "https://accounts.google.com", directive(self.policy, "form-action")
        )
        for name in ("script-src", "frame-src", "connect-src"):
            with self.subTest(directive=name):
                self.assertNotIn(
                    "https://accounts.google.com", directive(self.policy, name)
                )

    def test_no_third_party_may_run_scripts(self):
        """Nothing but this origin, and it stays that way."""
        allowed = {"'self'", "'wasm-unsafe-eval'"}
        sources = set(directive(self.policy, "script-src").split()[1:])
        self.assertEqual(sources - allowed, set())

    def test_nothing_may_be_framed(self):
        # There is no iframe on any page now that Google's button is gone.
        self.assertIn("'none'", directive(self.policy, "frame-src"))

    def test_the_map_and_geocoder_origins_are_named(self):
        for origin in (
            "https://tiles.openfreemap.org",
            "https://nominatim.openstreetmap.org",
        ):
            with self.subTest(origin=origin):
                self.assertIn(origin, directive(self.policy, "connect-src"))


class EnforcementScopeTests(TestCase):
    """Enforced on what survives the cutover, report-only on what does not."""

    def test_the_shell_is_enforced(self):
        response = self.client.get(reverse("shell"))
        self.assertIn(ENFORCED, response)
        self.assertNotIn(REPORT, response)

    def test_a_language_prefixed_shell_route_is_also_enforced(self):
        """
        The regression this test exists for: matching `app/` against the path
        after the prefix enforces on /app/ but silently report-only's on
        /or/app/ and /hi/app/ (internal design notes).
        """
        for language in ("or", "hi"):
            response = self.client.get(reverse("shell-lang", args=[language]))
            self.assertIn(ENFORCED, response, f"{language} route not enforced")

    def test_the_deployment_root_is_enforced_now(self):
        """
        There is no report-only surface left.

        This asserted the opposite -- that `index` served report-only, because
        the Django templates loaded jQuery and model-viewer from unpkg and
        enforcing would break them. Those templates are no longer routed: the
        React app is served at the deployment root, so the split the policy was
        divided along has gone.

        The report-only half of the middleware stays. It costs nothing, and the
        moment anything is mounted outside `tirtha.shell` again it is what
        keeps that page working while its policy is worked out.
        """
        response = self.client.get(reverse("index"))
        self.assertIn(ENFORCED, response)
        self.assertNotIn(REPORT, response)

    def test_every_page_a_visitor_can_reach_is_enforced(self):
        # The whole point of moving the app to the root: one policy, no
        # surface left over that quietly reports instead of enforcing.
        for name in ("shell", "index"):
            with self.subTest(route=name):
                self.assertIn(ENFORCED, self.client.get(reverse(name)))


class EmbedTests(TestCase):
    def test_an_embed_may_be_framed_by_anyone(self):
        """
        `frame-ancestors 'none'` would silently undo the whole sharing feature
        (internal design notes) -- the embed renders, in nobody's page.
        """
        from tirtha.middleware import SecurityHeadersMiddleware, _policy

        policy = _policy(**{"frame-ancestors": ["*"]})
        self.assertIn("frame-ancestors *", policy)
        # ...and it is still the same policy in every other respect.
        self.assertEqual(
            directive(policy, "script-src"),
            directive(_policy(), "script-src"),
        )
        self.assertTrue(hasattr(SecurityHeadersMiddleware, "_is_embeddable"))

    def test_everything_else_refuses_to_be_framed(self):
        policy = self.client.get(reverse("shell"))[ENFORCED]
        self.assertIn("frame-ancestors 'none'", policy)


class NonDocumentTests(TestCase):
    def test_json_responses_carry_no_policy(self):
        """
        CSP governs documents. Sending it with the API, every tile and each of
        ~19,300 media files is bytes no browser acts on.
        """
        response = self.client.get(reverse("api-sites"))
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertNotIn(ENFORCED, response)
        self.assertNotIn(REPORT, response)


@override_settings(PRE_URL="")
class NoPrefixTests(TestCase):
    """A deployment can be mounted at the root; the path split must still work."""

    def test_shell_is_enforced_without_a_url_prefix(self):
        response = self.client.get("/")
        if response.status_code == 404:
            self.skipTest("URLconf is built from PRE_URL at import time")
        self.assertIn(ENFORCED, response)


class InlineStyleNonce(TestCase):
    """
    The two Django-rendered pages that carry a `<style>` block of their own.

    `style-src` is `'self'` -- deliberately, since Vite emits real .css files
    and nothing in the SPA needs an inline stylesheet. But the embed's
    attribution strip (internal design notes) and the canonical document pages are
    Django templates with page-specific CSS, and both were being blocked: the
    embed rendered as black text on white with the credit line scrolled off the
    bottom, taking the model's attribution with it. That strip is the entire
    reason the embed feature is acceptable.

    A nonce, not `'unsafe-inline'`. The latter would let any injected `<style>`
    run, and an injected stylesheet reads out form values one character at a
    time through attribute selectors with `background-image` URLs.
    """

    def _page(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, path)
        return response

    def test_the_style_block_carries_the_nonce_the_header_names(self):
        response = self._page("/project/tirtha/terms/")
        header = response.headers.get(
            "Content-Security-Policy"
        ) or response.headers.get("Content-Security-Policy-Report-Only")

        match = re.search(r"'nonce-([A-Za-z0-9_-]+)'", header)
        self.assertIsNotNone(match, "no nonce in style-src")
        self.assertIn(
            f'<style nonce="{match.group(1)}">',
            response.content.decode(),
            "the page's <style> does not carry the nonce from its own header",
        )

    def test_the_nonce_changes_between_responses(self):
        # A fixed nonce is the same as no nonce: an injected <style> could carry
        # it too.
        nonces = set()
        for _ in range(3):
            response = self._page("/project/tirtha/terms/")
            header = response.headers.get(
                "Content-Security-Policy"
            ) or response.headers.get("Content-Security-Policy-Report-Only")
            nonces.add(re.search(r"'nonce-([A-Za-z0-9_-]+)'", header).group(1))
        self.assertEqual(len(nonces), 3)

    def test_unsafe_inline_is_not_how_this_was_solved(self):
        response = self._page("/project/tirtha/terms/")
        header = response.headers.get(
            "Content-Security-Policy"
        ) or response.headers.get("Content-Security-Policy-Report-Only")
        style_src = directive(header, "style-src")
        self.assertNotIn("'unsafe-inline'", style_src)


class ThirdPartyStyleInjection(TestCase):
    """
    The one library that injects its own `<style>`, and how it is allowed to.

    `@mkkellogg/gaussian-splats-3d` builds three stylesheets in its `Viewer`
    constructor -- `.progressBarOuterContainer`, `.spinnerOuterContainer0`,
    `.infoPanel` -- for a loading UI this app disables outright
    (`showLoadingUI: false`). `style-src` refused all three, so every model load
    printed six CSP errors in a browser. Nothing broke; the styles belong to
    widgets nobody sees. The cost was noise in the exact channel `the internal cutover notes`
    tells an operator to watch for real violations, which is how a real one gets
    missed.

    Found by opening the app in Chromium, not by reading anything -- the same
    lesson as the artifact format.

    The fix stamps the nonce the server already issues onto style elements as
    they are created, for the duration of that one constructor. Not
    `'unsafe-inline'`: that would admit *any* injected stylesheet, and an
    injected stylesheet reads form values out one character at a time through
    attribute selectors with `background-image` URLs.
    """

    FRONTEND = Path(__file__).resolve().parents[3] / "frontend" / "src"

    def test_the_shell_publishes_the_nonce(self):
        response = self.client.get(reverse("shell"))
        body = response.content.decode()
        header = response.headers.get(ENFORCED) or response.headers.get(REPORT)
        nonce = re.search(r"'nonce-([A-Za-z0-9_-]+)'", header)
        self.assertIsNotNone(nonce, "no nonce in style-src")
        self.assertIn(
            f'<meta name="csp-nonce" content="{nonce.group(1)}">',
            body,
            "the page does not publish the nonce its own header names",
        )

    def test_the_nonce_is_applied_only_around_the_one_constructor(self):
        """
        Scoped, and restored in a `finally`.

        A permanent `document.createElement` patch would bless a stylesheet
        injected by anything, at any time -- which is `'unsafe-inline'` wearing
        a disguise.
        """
        shim = (self.FRONTEND / "utils" / "styleNonce.js").read_text()
        self.assertIn("finally", shim)
        self.assertIn("document.createElement = create", shim)

        viewer = (self.FRONTEND / "components" / "ModelViewer3D.jsx").read_text()
        self.assertIn("withStyleNonce(", viewer)
        self.assertEqual(
            viewer.count("withStyleNonce("), 1, "the shim is used more than once"
        )

    def test_style_src_still_refuses_unsafe_inline(self):
        response = self.client.get(reverse("shell"))
        header = response.headers.get(ENFORCED) or response.headers.get(REPORT)
        self.assertNotIn("'unsafe-inline'", directive(header, "style-src"))


class NoThirdPartyFonts(TestCase):
    """
    No font origin is allowed, because none is used.

    `fonts.googleapis.com` and `fonts.gstatic.com` were in the policy for the
    Material Icons font — which the deployed page never requested. The `<link>`
    lived in Vite's `index.html`, and Django serves `shell.html`, which injects
    only what the build manifest lists. So the origin was permitted for a request
    that was never made, while all 33 icons rendered their own ligature name as
    text: "menu", "close", "search" scattered through the interface.

    internal design notes wanted this gone anyway — a Google Fonts link discloses every
    visitor's IP to a third party on every page load, and 11.2 takes DPDP
    obligations seriously. It was recorded there as a defect rather than a
    decision. Inline SVG icons are what let it go.
    """

    FRONTEND = Path(__file__).resolve().parents[3] / "frontend"

    def test_no_font_origin_is_allowed(self):
        policy = self.client.get(reverse("shell")).headers[ENFORCED]
        for name in ("font-src", "style-src"):
            with self.subTest(directive=name):
                sources = directive(policy, name)
                self.assertNotIn("fonts.googleapis.com", sources)
                self.assertNotIn("fonts.gstatic.com", sources)

    def test_font_src_is_self_and_data_only(self):
        policy = self.client.get(reverse("shell")).headers[ENFORCED]
        sources = set(directive(policy, "font-src").split()[1:])
        self.assertEqual(sources, {"'self'", "data:"})

    def test_nothing_links_a_google_font(self):
        """
        Including Vite's own entry — that is where the link was, and where it
        was invisible to every check that looked at what Django serves.
        """
        offenders = []
        for path in (
            [self.FRONTEND / "index.html"]
            + list((self.FRONTEND / "src").rglob("*.css"))
            + list((self.FRONTEND / "src").rglob("*.jsx"))
        ):
            body = re.sub(r"<!--.*?-->|/\*.*?\*/", "", path.read_text(), flags=re.S)
            if "fonts.googleapis.com" in body or "fonts.gstatic.com" in body:
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_the_icons_do_not_depend_on_a_font(self):
        icon = (self.FRONTEND / "src" / "components" / "Icon.jsx").read_text()
        self.assertIn("<svg", icon)
        self.assertIn("currentColor", icon)

        # And no component still renders a ligature the font would have needed.
        strays = [
            p.name
            for p in (self.FRONTEND / "src").rglob("*.jsx")
            if p.name != "Icon.jsx" and 'className="material-icons"' in p.read_text()
        ]
        self.assertEqual(strays, [], "these still need the icon font")


class DocumentPagesAreEnforced(TestCase):
    """
    The canonical documents are React-era pages, not legacy templates.

    `_is_new_frontend` matched on path shape -- `app/`, `embed/`, `oembed`,
    `api/` -- and the document routes added by unit 6.9 match none of them, so
    `/terms/`, `/privacy/` and `/acknowledgment/` shipped **report-only**, in
    all three languages. That is the same asymmetry 7.1 records finding for
    `/or/app/`, one unit later.

    No live XSS: these pages render fixed template files with no user data. The
    protection people believe is on simply was not. This class asserts the
    *enforced* header specifically -- the existing tests read
    `headers.get(ENFORCED) or headers.get(REPORT)`, which is why they checked
    the nonce was right without noticing the header was the wrong one.
    """

    PATHS = (
        "/project/tirtha/terms/",
        "/project/tirtha/privacy/",
        "/project/tirtha/acknowledgment/",
        "/project/tirtha/or/terms/",
        "/project/tirtha/hi/privacy/",
    )

    def test_each_document_page_enforces_the_policy(self):
        for path in self.PATHS:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(ENFORCED, response.headers, "report-only, not enforced")
                self.assertNotIn(REPORT, response.headers)

    def test_the_deployment_root_is_enforced_too(self):
        # This asserted `index` was report-only, which was true while the
        # Django templates owned the root. The app owns it now.
        response = self.client.get(reverse("index"))
        self.assertIn(ENFORCED, response.headers)
        self.assertNotIn(REPORT, response.headers)


class BlobWorkersCanFetch(TestCase):
    """
    A worker spawned from a blob URL fetches through `connect-src`.

    `worker-src` already allowed `blob:` so MapLibre could start its worker.
    The KTX2 transcoder that decodes the published meshes' textures then made
    138 blocked requests per model load -- "Fetch API cannot load blob:" -- and
    the mesh rendered untextured with nothing on the page to say why.

    Allowing the worker and refusing what the worker then reads is the kind of
    half-permission that produces a silent failure rather than an error.
    """

    def _policy(self):
        response = self.client.get(reverse("shell"))
        return response.get(ENFORCED) or response.get(REPORT) or ""

    def test_connect_src_allows_blob(self):
        directive = next(
            (
                d
                for d in self._policy().split(";")
                if d.strip().startswith("connect-src")
            ),
            "",
        )
        self.assertIn("blob:", directive, "a blob worker cannot fetch what it needs")

    def test_worker_src_still_allows_blob(self):
        directive = next(
            (
                d
                for d in self._policy().split(";")
                if d.strip().startswith("worker-src")
            ),
            "",
        )
        self.assertIn("blob:", directive)

    def test_blob_is_not_allowed_to_run_scripts(self):
        # The worker is started from a blob; that is `worker-src`. `script-src`
        # staying blob-free is what keeps an injected blob from executing.
        directive = next(
            (
                d
                for d in self._policy().split(";")
                if d.strip().startswith("script-src")
            ),
            "",
        )
        self.assertNotIn("blob:", directive)
