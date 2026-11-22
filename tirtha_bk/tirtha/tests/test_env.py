"""
Tests for the settings environment helpers (tirtha_bk/env.py).

These gate production startup: env_required is what turns a missing secret
into a loud failure instead of a silently broken deployment (internal design notes).
"""

import os
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from tirtha_bk.env import env, env_bool, env_float, env_int, env_required


class EnvTests(SimpleTestCase):
    def test_returns_value_when_set(self):
        with mock.patch.dict(os.environ, {"X": "hello"}):
            self.assertEqual(env("X"), "hello")

    def test_falls_back_when_unset_or_empty(self):
        with mock.patch.dict(os.environ, {"X": ""}):
            self.assertEqual(env("X", "fallback"), "fallback")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(env("X", "fallback"), "fallback")


class EnvBoolTests(SimpleTestCase):
    def test_truthy_spellings(self):
        for raw in ("1", "true", "TRUE", "Yes", "on"):
            with mock.patch.dict(os.environ, {"X": raw}):
                self.assertTrue(env_bool("X"), f"{raw!r} should be True")

    def test_falsy_spellings(self):
        for raw in ("0", "false", "no", "off", "anything else"):
            with mock.patch.dict(os.environ, {"X": raw}):
                self.assertFalse(env_bool("X"), f"{raw!r} should be False")

    def test_default_used_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(env_bool("X", True))
            self.assertFalse(env_bool("X", False))


class EnvNumericTests(SimpleTestCase):
    def test_parses_int_and_float(self):
        with mock.patch.dict(os.environ, {"I": "42", "F": "1.5"}):
            self.assertEqual(env_int("I"), 42)
            self.assertEqual(env_float("F"), 1.5)

    def test_unparseable_raises_rather_than_defaulting(self):
        # Silently falling back would hide a typo in a deployment env file.
        with mock.patch.dict(os.environ, {"I": "not-a-number"}):
            with self.assertRaises(ImproperlyConfigured):
                env_int("I", 7)
        with mock.patch.dict(os.environ, {"F": "not-a-number"}):
            with self.assertRaises(ImproperlyConfigured):
                env_float("F", 7.0)


class EnvRequiredTests(SimpleTestCase):
    def test_missing_in_production_raises(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ImproperlyConfigured) as ctx:
                env_required("SECRET_KEY", debug=False)
        self.assertIn("SECRET_KEY", str(ctx.exception))

    def test_missing_in_debug_returns_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                env_required("SECRET_KEY", debug=True, default="dev"), "dev"
            )

    def test_present_value_wins_in_both_modes(self):
        with mock.patch.dict(os.environ, {"SECRET_KEY": "real"}):
            self.assertEqual(
                env_required("SECRET_KEY", debug=True, default="dev"), "real"
            )
            self.assertEqual(env_required("SECRET_KEY", debug=False), "real")

    def test_empty_string_counts_as_missing(self):
        with mock.patch.dict(os.environ, {"SECRET_KEY": ""}):
            with self.assertRaises(ImproperlyConfigured):
                env_required("SECRET_KEY", debug=False)

    def test_hint_is_included(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ImproperlyConfigured) as ctx:
                env_required("DB_PWD", debug=False, hint="Set it in build/tirtha.env.")
        self.assertIn("build/tirtha.env", str(ctx.exception))
