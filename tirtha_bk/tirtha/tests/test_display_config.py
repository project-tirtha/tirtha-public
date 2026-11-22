"""
`config/display.json` — the settings a maintainer changes without a diff.

Every value in that file is something this upgrade changed its mind about at
least once: whether the hover tile animates, whether the Beta badge shows,
which languages the switcher offers, how close pins must be to merge. The point
of the file is that revisiting those does not need a code change; the point of
these tests is that a mistake in it cannot take the site down.
"""

import json
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from tirtha import display


class ReadingTheFile(SimpleTestCase):
    def setUp(self):
        display.display_config.cache_clear()

    def tearDown(self):
        display.display_config.cache_clear()

    def test_the_shipped_file_parses_and_covers_every_default(self):
        # The real file, not a fixture: a trailing comma in it is a site-wide
        # failure and this is the cheapest place to catch one.
        raw = json.loads(display.CONFIG_PATH.read_text())
        keys = {k for k in raw if not k.startswith("_")}
        self.assertEqual(keys, set(display.DEFAULTS))

    def test_a_missing_file_falls_back_to_the_defaults(self):
        with patch.object(
            display, "CONFIG_PATH", display.CONFIG_PATH.parent / "gone.json"
        ):
            display.display_config.cache_clear()
            self.assertEqual(display.display_config(), display.DEFAULTS)

    def test_malformed_json_falls_back_rather_than_raising(self):
        """
        A stray comma must not be a blank site.

        This is read on every shell render, so an exception here is every page
        at once -- and the person who broke it is editing a config file, not
        deploying code.
        """
        with patch.object(display, "_from_file", side_effect=None) as _:
            pass
        with patch("pathlib.Path.read_text", return_value="{not json"):
            display.display_config.cache_clear()
            self.assertEqual(display.display_config(), display.DEFAULTS)

    def test_an_unknown_key_is_ignored_and_reported(self):
        with patch.object(display, "_from_file", return_value={"noSuchSetting": 1}):
            display.display_config.cache_clear()
            with self.assertLogs("tirtha.display", level="WARNING") as logs:
                config = display.display_config()
            self.assertNotIn("noSuchSetting", config)
            self.assertIn("noSuchSetting", "".join(logs.output))

    def test_a_deployment_can_override_any_key(self):
        with override_settings(DISPLAY_CONFIG={"animatedPreviews": False}):
            display.display_config.cache_clear()
            self.assertFalse(display.display_config()["animatedPreviews"])

    def test_a_deployment_cannot_invent_one(self):
        with override_settings(DISPLAY_CONFIG={"nonsense": True}):
            display.display_config.cache_clear()
            self.assertNotIn("nonsense", display.display_config())


class ReachingTheClient(TestCase):
    """The config is no use unless the app can read it."""

    def setUp(self):
        display.display_config.cache_clear()

    def test_the_shell_publishes_it(self):
        response = self.client.get(reverse("shell"))
        self.assertContains(response, 'name="tirtha-display"')

    def test_what_it_publishes_is_valid_json_the_client_can_parse(self):
        body = self.client.get(reverse("shell")).content.decode()
        # Django escapes `"` to `&quot;` inside the attribute; the browser
        # unescapes it before the client sees it.
        start = body.index('name="tirtha-display" content="') + len(
            'name="tirtha-display" content="'
        )
        raw = body[start : body.index('"', start)].replace("&quot;", '"')
        parsed = json.loads(raw)
        self.assertEqual(set(parsed), set(display.DEFAULTS))

    def test_a_changed_setting_reaches_the_page(self):
        with override_settings(DISPLAY_CONFIG={"animatedPreviews": False}):
            display.display_config.cache_clear()
            body = self.client.get(reverse("shell")).content.decode()
            self.assertIn("animatedPreviews&quot;:false", body)
