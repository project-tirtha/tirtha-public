"""
Three deployments, one hostname, three cookie namespaces (internal design notes).

Production, the public demo and beta all answer on `smlab.niser.ac.in`, mounted
side by side under different `PRE_URL` prefixes -- and they run against
*different databases*. None of them overrode `SESSION_COOKIE_NAME` or
`SESSION_COOKIE_PATH`, so all three issued Django's default `sessionid` at
path `/`: one cookie, shared by three deployments that cannot read each other's
sessions.

That is not merely untidy. `SessionMiddleware.process_response` does

    if settings.SESSION_COOKIE_NAME in request.COOKIES and empty:
        response.delete_cookie(SESSION_COOKIE_NAME, path=SESSION_COOKIE_PATH, ...)

so a deployment that *receives* a session key it cannot find in its own database
concludes the session is empty and tells the browser to delete the cookie --
at path `/`, for the whole host. Any request that touches `request.session`
triggers the load, and `api.session` is fetched by the React shell.

The result: a signed-in contributor who opens the demo in another tab is
silently signed out of production, mid-upload, having done nothing but look.

Cookie identity is (name, domain, path), so distinct *names* -- not merely
distinct paths -- are what make these three independent. A distinct name also
removes the cross-deployment fixation surface the `verifyToken` comment
reasons about.
"""

import ast
from pathlib import Path

from django.test import SimpleTestCase

SETTINGS_DIR = Path(__file__).resolve().parents[2] / "tirtha_bk"


class _Settings:
    """
    The module's literal assignments, read without importing it.

    Beta refuses to import unless its restored database is present -- a
    deliberate guard, so that beta can never come up against an empty database
    pretending to be production. Parsing the source keeps that guard intact and
    also means these assertions cannot be satisfied by an environment variable
    that happens to be set on the machine running the tests.
    """

    def __init__(self, module: str):
        self.module = module
        path = SETTINGS_DIR / f"{module}.py"
        assert path.is_file(), f"no such settings module: {path}"
        self.values = {}
        for node in ast.parse(path.read_text()).body:
            if not isinstance(node, ast.Assign):
                continue
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                # Not a literal -- `CACHES` holds an `env(...)` call. Keep the
                # source text so a test can still assert on what it names.
                value = ast.unparse(node.value)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.values[target.id] = value

    def get(self, name, default=None):
        return self.values.get(name, default)


def _load(module: str):
    return _Settings(module.rsplit(".", 1)[-1])


class CookieNamespaceTests(SimpleTestCase):
    """Each deployment names its own cookies."""

    DEPLOYMENTS = ("tirtha_bk.local_settings_demo", "tirtha_bk.local_settings_beta")

    def test_each_deployment_names_its_own_session_cookie(self):
        seen = {}
        for module in self.DEPLOYMENTS:
            settings_module = _load(module)
            name = settings_module.get("SESSION_COOKIE_NAME", "sessionid")
            self.assertNotEqual(
                name,
                "sessionid",
                f"{module} uses Django's default session cookie name, which "
                f"production also uses on the same host",
            )
            self.assertNotIn(
                name,
                seen,
                f"{module} shares a session cookie name with {seen.get(name)}",
            )
            seen[name] = module

    def test_each_deployment_names_its_own_csrf_cookie(self):
        seen = {}
        for module in self.DEPLOYMENTS:
            settings_module = _load(module)
            name = settings_module.get("CSRF_COOKIE_NAME", "csrftoken")
            self.assertNotEqual(
                name,
                "csrftoken",
                f"{module} uses Django's default CSRF cookie name; a token "
                f"read here would be replayable against production's origin",
            )
            self.assertNotIn(name, seen, f"{module} shares a CSRF cookie name")
            seen[name] = module


class CookieHardeningTests(SimpleTestCase):
    """
    The flags every internet-facing deployment owes a cookie.

    The demo is public and served over HTTPS, but set none of these, so a
    request that reached it over plain HTTP carried both cookies in cleartext.
    """

    DEPLOYMENTS = ("tirtha_bk.local_settings_demo", "tirtha_bk.local_settings_beta")

    def test_cookies_are_https_only(self):
        for module in self.DEPLOYMENTS:
            with self.subTest(module=module):
                s = _load(module)
                self.assertTrue(s.get("SESSION_COOKIE_SECURE", False))
                self.assertTrue(s.get("CSRF_COOKIE_SECURE", False))

    def test_the_session_cookie_is_not_readable_from_script(self):
        for module in self.DEPLOYMENTS:
            with self.subTest(module=module):
                s = _load(module)
                self.assertTrue(s.get("SESSION_COOKIE_HTTPONLY", False))

    def test_cookies_are_not_sent_on_cross_site_requests(self):
        for module in self.DEPLOYMENTS:
            with self.subTest(module=module):
                s = _load(module)
                self.assertEqual(s.get("SESSION_COOKIE_SAMESITE"), "Lax")
                self.assertEqual(s.get("CSRF_COOKIE_SAMESITE"), "Lax")
