"""
Which filesystem the Site signals are allowed to touch.

`post_save_site` creates directories under STATIC_ROOT and MEDIA_ROOT, and
`post_del_site` rmtrees them. Django's test runner substitutes the database and
nothing else, so both ran against the live deployment's trees: a suite that
creates sites left thousands of stray directories behind, and one that deletes
them -- `post_migrate` recreates the default site under a *fixed* ID, so the
collision is not luck -- deleted a real published model, preview and thumbnail.
Rollback reverts the rows; nothing reverts the filesystem.

Two halves, one per test class: the roots a test run sees are scratch ones, and
the receivers read them when they run rather than when they were imported.
"""

import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from tirtha.models import Site


class TestRunRootTests(SimpleTestCase):
    def test_neither_root_is_the_deployments_own(self):
        # `manage.py test` on the deployment host -- which DEPLOY.md asks for
        # after a deploy -- must not be able to reach what the deployment
        # serves. Both roots are replaced with a per-run scratch tree in
        # settings.py, before the app imports them.
        scratch = tempfile.gettempdir()
        for name in ("STATIC_ROOT", "MEDIA_ROOT"):
            with self.subTest(setting=name):
                self.assertTrue(
                    str(Path(getattr(settings, name))).startswith(scratch),
                    f"{name} still points at {getattr(settings, name)} during a "
                    "test run; a deleted Site would rmtree it.",
                )


class SiteDirectoryTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.static = Path(self._tmp.name) / "static"
        self.media = Path(self._tmp.name) / "media"
        self._override = override_settings(
            STATIC_ROOT=str(self.static), MEDIA_ROOT=str(self.media)
        )
        self._override.enable()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._override.disable)

    def test_the_directories_follow_the_roots_in_force_when_the_site_is_saved(self):
        site = Site.objects.create(name="Signal Path Temple", country="India")

        # Bound at import, the receiver would have built these under whatever
        # root `signals.py` first saw, and `override_settings` would have been
        # decoration.
        self.assertTrue((self.media / f"models/{site.ID}/images/nsfw").is_dir())
        self.assertTrue((self.static / f"models/{site.ID}/published").is_dir())

    def test_deleting_a_site_removes_only_its_own_directories(self):
        site = Site.objects.create(name="Signal Path Temple", country="India")
        neighbour = self.static / "models/some-other-site"
        neighbour.mkdir(parents=True)

        site.delete()

        self.assertFalse((self.media / f"models/{site.ID}").exists())
        self.assertFalse((self.static / f"models/{site.ID}").exists())
        self.assertTrue(neighbour.is_dir())

    def test_deleting_a_site_whose_directories_are_gone_is_not_an_error(self):
        # An unguarded rmtree here raised FileNotFoundError out of `delete()`,
        # which in admin is a 500 over a directory that is already in the state
        # the caller asked for.
        site = Site.objects.create(name="Signal Path Temple", country="India")
        for root in (self.media, self.static):
            (root / f"models/{site.ID}").rename(root / f"models/moved-{site.ID}")

        site.delete()

        self.assertFalse(Site.objects.filter(ID=site.ID).exists())
