"""
Tests that the settings shim routes correctly.

Settings are already imported by the time a test runs, so this spawns a
subprocess -- the only way to exercise import-time behaviour, which is exactly
when a misrouted or missing setting should surface.

The production guards themselves are covered by test_env.py: the manual and
docker templates are deliberately *not* importable modules (they are copied to
local_settings.py at deploy time), so their behaviour is tested through the
env_required helper they call.

What the tracked modules share lives in `settings_base.py`; `SharedBase` below
pins the two rules that keep that safe.
"""

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

# .../tirtha_bk -- the Django project root, where manage.py lives.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_settings(env: dict):
    """Import Django settings in a clean subprocess. Returns (returncode, output)."""
    code = (
        "import django;"
        "django.setup();"
        "from django.conf import settings;"
        "print('DEBUG=%s' % settings.DEBUG);"
        "print('ENGINE=%s' % settings.DATABASES['default']['ENGINE'])"
    )
    child = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
            ("TIRTHA_", "SECRET_KEY", "DB_", "GOOGLE_", "EMAIL_", "DEBUG")
        )
    }
    child.update(env)
    child["DJANGO_SETTINGS_MODULE"] = "tirtha_bk.settings"
    # Extend rather than replace: tirtha_recon may be on the path via an editable
    # install or PYTHONPATH, and settings import it transitively.
    existing = os.environ.get("PYTHONPATH", "")
    child["PYTHONPATH"] = (
        f"{PROJECT_ROOT}{os.pathsep}{existing}" if existing else str(PROJECT_ROOT)
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env=child,
        timeout=120,
    )
    return proc.returncode, proc.stdout + proc.stderr


class DevSettingsRoutingTests(SimpleTestCase):
    def test_tirtha_dev_selects_the_dev_module_with_no_secrets_present(self):
        # Regression test for internal design notes: settings.py imported
        # `.local_settings_dev` while the file was named `local_settings.dev.py`,
        # so TIRTHA_DEV=1 raised ModuleNotFoundError and the documented dev path
        # could never have worked. It must now load with an empty environment.
        rc, out = _load_settings({"TIRTHA_DEV": "1"})
        self.assertEqual(rc, 0, out)
        self.assertIn("DEBUG=True", out)
        self.assertIn("sqlite3", out)

    def test_dev_settings_never_point_at_the_production_database(self):
        # A dev run must not be able to reach prod: internal design notes freezes
        # production, and the upgrade clone has no local_settings.py at all.
        rc, out = _load_settings({"TIRTHA_DEV": "1"})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("postgresql", out)


class UrlPrefixShape(SimpleTestCase):
    """
    Every settings module spells `STATIC_URL` and `MEDIA_URL` the same way.

    `PRE_URL` already ends in a slash, and two modules added another:
    `local_settings_dev.py` produced `/project/tirtha//static/`, and
    `local_settings.manual.py` -- the template `the internal cutover notes` step B says to
    base the new production config on -- did the same.

    A doubled slash is not cosmetic here. `vite.py` normalises around it, but
    `SecurityHeadersMiddleware._is_embeddable` decides whether a request is for
    static or media by `request.path.startswith(settings.STATIC_URL)`, and a
    prefix that no real path matches means the cross-origin headers the embed
    route depends on are never applied to the assets it loads. `MEDIA_URL` had
    the mirror-image bug in three modules: no leading slash at all, so it
    matched nothing either.

    Checked as text rather than by importing, because importing a deployment's
    settings requires that deployment's secrets.
    """

    MODULES = (
        "local_settings_dev.py",
        "local_settings_demo.py",
        "local_settings_beta.py",
        "local_settings.manual.py",
    )

    def _source(self, name):
        path = Path(__file__).resolve().parents[2] / "tirtha_bk" / name
        if not path.is_file():
            self.skipTest(f"{name} is not present")
        return path.read_text()

    def test_no_module_doubles_the_slash(self):
        for name in self.MODULES:
            source = self._source(name)
            for setting in ("STATIC_URL", "MEDIA_URL"):
                with self.subTest(module=name, setting=setting):
                    self.assertNotIn(
                        f'{setting} = "/" + PRE_URL + "/',
                        source,
                        f"{name}: PRE_URL already ends in a slash",
                    )
                    self.assertNotIn(
                        f'{setting} = os.getenv("{setting}", "/" + PRE_URL + "/',
                        source,
                    )

    def test_every_url_has_a_leading_slash(self):
        for name in self.MODULES:
            source = self._source(name)
            for setting in ("STATIC_URL", "MEDIA_URL"):
                with self.subTest(module=name, setting=setting):
                    match = re.search(rf"^{setting} = (.+)$", source, re.M)
                    self.assertIsNotNone(match, f"{name} does not set {setting}")
                    self.assertIn(
                        '"/" + PRE_URL',
                        match.group(1),
                        f"{name}: {setting} must start with a slash -- "
                        "`request.path` always does, so a prefix without one "
                        "matches nothing",
                    )


class SharedBase(SimpleTestCase):
    """
    `settings_base.py` holds what every tracked settings module shares.

    Two rules keep that a pure deduplication rather than a source of silent
    differences, and both fail quietly if broken -- the value a deployment ends
    up with is simply not the one its own file appears to set:

    * Each module imports the base *before* it assigns anything, so its own
      values override the base and never the other way round.
    * The base reads nothing a deployment sets. `PRE_URL`, `TIRTHA_ROOT` and
      the directory roots are assigned after the base has run, so a path built
      from one of them there would be built from a name that does not exist
      yet -- or, worse, from an earlier module's value.
    """

    SETTINGS_DIR = PROJECT_ROOT / "tirtha_bk"
    MODULES = (
        "local_settings_dev.py",
        "local_settings_demo.py",
        "local_settings_beta.py",
        "local_settings.manual.py",
        "local_settings.docker.py",
    )
    PER_DEPLOYMENT = {
        "PRE_URL",
        "TIRTHA_ROOT",
        "PROD_DIR",
        "NFS_DIR",
        "LOG_DIR",
        "ARCHIVE_ROOT",
        "STATIC_ROOT",
        "MEDIA_ROOT",
        "DEBUG",
    }

    def test_every_module_imports_the_base_before_assigning_anything(self):
        for name in self.MODULES:
            with self.subTest(module=name):
                body = ast.parse((self.SETTINGS_DIR / name).read_text()).body
                base = [
                    i
                    for i, node in enumerate(body)
                    if isinstance(node, ast.ImportFrom)
                    and node.module == "settings_base"
                    and node.level == 1
                    and [a.name for a in node.names] == ["*"]
                ]
                self.assertEqual(len(base), 1, f"{name} does not import the base")
                first_assign = next(
                    i
                    for i, node in enumerate(body)
                    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
                )
                self.assertLess(base[0], first_assign, name)

    def test_the_base_reads_no_per_deployment_value(self):
        tree = ast.parse((self.SETTINGS_DIR / "settings_base.py").read_text())
        read = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        self.assertEqual(read & self.PER_DEPLOYMENT, set())
