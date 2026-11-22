"""
Every endpoint the front end calls is an endpoint this backend serves.

This is the test that would have caught the port's largest single defect. The
React UI was written against a different Django project, and six of the URLs it
calls do not exist here:

    /api/auth/current-contributor/     -> no such route
    /api/auth/google-login/            -> a different authentication design
    /api/auth/logout/                  -> no such route
    /api/contributions/upload-check/   -> is `preUpload/`, with other parameters
    /api/contributions/upload/         -> is `upload/`, with other field names
    /api/site-requests/submit/         -> is `api/v1/site-requests/`
    /api/platform/statistics/          -> is `api/v1/stats/`
    /api/proxy-model/                  -> never existed; would be an SSRF sink

Each failed silently. `fetch` does not throw on a 404, and every call site
either swallowed the error or read a field off an error page and got
`undefined`. The sign-in button never rendered, the upload button stayed
disabled for approved contributors, and the site-request form reported
"submitted successfully" for a submission that was never stored.

Nothing about the type system or the build catches that -- the two halves are
only connected at runtime, by strings. So they are connected here instead.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import Resolver404, resolve

FRONTEND_SRC = Path(__file__).resolve().parents[3] / "frontend" / "src"

# A URL path in a string or template literal, absolute, at least two segments
# deep. One segment ('/') is a router path, not an endpoint.
URL_LITERAL = re.compile(r"""['"`](/(?:api|auth|accounts)/[A-Za-z0-9_/\-]*)""")

# What the code interpolates in front of a path. `getApiBaseUrl()` already ends
# in the deployment prefix plus `/api/v1`, and `deploymentPrefix()` in the
# prefix alone -- so a literal starting `/api/` after either is a doubled
# segment, which is exactly how `/api/v1/api/platform/statistics/` happened.
PREFIXED_CALL = re.compile(
    r"(?:getApiBaseUrl\(\)|deploymentPrefix\(\))\s*[+`]?\s*[`'\"]?(/[A-Za-z0-9_/\-]*)"
)

# A path assembled from constants and `${...}` holes, which the literal check
# cannot see -- `${base}${SITES_API_PATH}${id}/stats/` is how the per-site stats
# endpoint went unnoticed while the sidebar showed zero images for every site.
# Each hole becomes a single segment, which is what a site id or a run id is.
TEMPLATE_PATH = re.compile(r"`\$\{getApiBaseUrl\(\)\}([^`]*)`")
SEGMENT_HOLE = re.compile(r"\$\{[^}]*\}")

PREFIX = "/project/tirtha/"

# `/api/v1` is the base every other path is appended to, not a call in its own
# right. It is the only such value; anything else two segments deep is a real
# endpoint that has to resolve.
BASES = {"/api/v1/"}


def _sources() -> list[Path]:
    return sorted(
        p
        for p in FRONTEND_SRC.rglob("*.js*")
        if p.suffix in {".js", ".jsx"} and ".test." not in p.name
    )


def _strip_comments(text: str) -> str:
    """
    Comments are not calls.

    Learned the hard way twice: this file describes the dead endpoints by name
    in its own docstring, and the source files that used to call them now carry
    comments saying so. A grep that does not strip comments fails on the
    explanation of the bug it is meant to catch.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?<!:)//[^\n]*", "", text)


class FrontendEndpointsResolve(SimpleTestCase):
    def test_no_frontend_url_literal_is_unroutable(self):
        unroutable: list[str] = []

        for source in _sources():
            text = _strip_comments(source.read_text(encoding="utf-8"))
            for match in URL_LITERAL.finditer(text):
                path = match.group(1)
                if not path.endswith("/"):
                    path += "/"
                if path in BASES:
                    continue
                try:
                    resolve(PREFIX.rstrip("/") + path)
                except Resolver404:
                    unroutable.append(f"{source.name}: {path}")

        self.assertEqual(
            unroutable,
            [],
            "These paths are called by the front end and served by nothing:\n  "
            + "\n  ".join(unroutable),
        )

    def test_every_templated_api_path_resolves(self):
        """
        The same check, for paths built by interpolation rather than written out.

        `fetchSiteStats` asked `${getApiBaseUrl()}${SITES_API_PATH}${siteId}/stats/`
        -- a real request, to a route that did not exist -- and the sidebar
        reported zero images and no contributors for every site, silently,
        because the `.catch` treated a 404 like a network blip.
        """
        unroutable: list[str] = []

        for source in _sources():
            text = _strip_comments(source.read_text(encoding="utf-8"))
            constants = dict(
                re.findall(r"const\s+([A-Z_]+)\s*=\s*['\"]([^'\"]+)['\"]", text)
            )

            for match in TEMPLATE_PATH.finditer(text):
                body = match.group(1)
                for name, value in constants.items():
                    body = body.replace("${" + name + "}", value)
                # Anything still interpolated is an identifier: one segment.
                path = SEGMENT_HOLE.sub("x", body)
                if not path or not path.startswith("/"):
                    continue
                if not path.endswith("/"):
                    path += "/"
                try:
                    resolve(PREFIX.rstrip("/") + "/api/v1" + path)
                except Resolver404:
                    unroutable.append(f"{source.name}: /api/v1{path}")

        self.assertEqual(
            unroutable,
            [],
            "These paths are built and called by the front end, and served by "
            "nothing:\n  " + "\n  ".join(unroutable),
        )

    def test_paths_appended_to_a_base_url_are_not_doubled(self):
        """
        `getApiBaseUrl()` ends in `/api/v1`. A literal beginning `/api/` after it
        produces `/api/v1/api/...`, which resolves nowhere -- and which the test
        above cannot see, because the doubled string never appears in the source.
        """
        doubled: list[str] = []

        for source in _sources():
            text = _strip_comments(source.read_text(encoding="utf-8"))
            for match in PREFIXED_CALL.finditer(text):
                path = match.group(1)
                if path.startswith("/api"):
                    doubled.append(f"{source.name}: {path}")

        self.assertEqual(
            doubled,
            [],
            "These are appended to a base URL that already contains the prefix:\n  "
            + "\n  ".join(doubled),
        )


class UploadContract(SimpleTestCase):
    """
    The field names, which a URL check cannot see.

    `preUpload/` reads `mesh_vid` and answers `allowupload`; `upload/` reads
    `mesh_vid`, `images`, `videos` and `compress`. The front end sent `site_id`,
    `allow_full_resolution` and `metadata_json`, and read `allow_upload`. Every
    one of those is a working HTTP request that does the wrong thing, so no
    status code reveals it.
    """

    def _contribute_source(self) -> str:
        # The dialog, and the module that builds its (batched) request bodies.
        return "\n".join(
            _strip_comments(path.read_text(encoding="utf-8"))
            for path in (
                FRONTEND_SRC / "components" / "ContributeModal.jsx",
                FRONTEND_SRC / "utils" / "batchUpload.js",
            )
        )

    def test_upload_identifies_the_site_by_verbose_id(self):
        source = self._contribute_source()
        self.assertIn("'mesh_vid'", source)
        self.assertNotIn("formData.append('site_id'", source)

    def test_pre_upload_reads_the_key_the_view_writes(self):
        source = self._contribute_source()
        self.assertIn("allowupload", source)

    def test_compression_uses_the_parameter_the_view_reads(self):
        # `compress`, and inverted: full resolution is the default (internal design notes
        # 17), so a client that omits it gets the better behaviour.
        source = self._contribute_source()
        self.assertIn("'compress'", source)
        self.assertNotIn("allow_full_resolution'", source)
