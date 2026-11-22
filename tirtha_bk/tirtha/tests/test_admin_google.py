"""
Admin access: Google sign-in for allowlisted staff, the password form behind
`ADMIN_PASSWORD_LOGIN`, and the emergency `admin_login_link` command.

The Google exchange is mocked the way `test_review_fixes.SignInRequiresAVerifiedEmail`
does it: `authorize_access_token` and `verify_oauth2_token` are patched, and
the state is planted in the session as `signin` would have stored it.
"""

from __future__ import annotations

import io
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from tirtha import admin_auth

ALLOW = ["Admin@example.org", "staff@example.org"]
ADMIN_INDEX = "/project/tirtha/admin/"


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.boss = User.objects.create_user(
            "boss",
            email="admin@example.org",
            password="pw-boss-123",
            is_staff=True,
            is_superuser=True,
        )
        self.lab = User.objects.create_user(
            "lab",
            email="staff@example.org",
            password="pw-lab-123",
            is_staff=True,
            is_superuser=True,
        )
        self.other_staff = User.objects.create_user(
            "other", email="other@example.org", password="pw-other-123", is_staff=True
        )

    def _verify(self, email, verified=True, next_url=ADMIN_INDEX):
        session = self.client.session
        session["auth_random_state"] = "st4te"
        session["auth_next"] = next_url
        session.save()
        idinfo = {"email": email, "name": "N", "email_verified": verified}
        with (
            patch(
                "tirtha.views.google.authorize_access_token",
                return_value={"id_token": "tok"},
            ),
            patch("tirtha.views.id_token.verify_oauth2_token", return_value=idinfo),
        ):
            return self.client.get(reverse("verifyToken"), {"state": "st4te"})

    def _django_user_id(self):
        return self.client.session.get("_auth_user_id")


@override_settings(ADMIN_GOOGLE_EMAILS=ALLOW)
class GoogleAdminSignIn(_Base):
    def test_allowlisted_staff_gets_a_django_login_and_the_admin(self):
        with self.assertLogs("tirtha.admin_auth", "INFO") as logs:
            response = self._verify("admin@example.org")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], ADMIN_INDEX)
        self.assertEqual(self._django_user_id(), str(self.boss.pk))
        self.assertIn("Admin sign-in via Google", "\n".join(logs.output))
        # Contributor sign-in is unchanged.
        self.assertEqual(
            self.client.session["tirtha_user_info"]["email"],
            "admin@example.org",
        )
        self.assertEqual(self.client.get(ADMIN_INDEX).status_code, 200)

    def test_matching_is_case_insensitive(self):
        self._verify("Staff@EXAMPLE.org")
        self.assertEqual(self._django_user_id(), str(self.lab.pk))

    def test_session_key_is_cycled(self):
        self.client.get(reverse("admin:login"))
        before = self.client.session.session_key
        self._verify("staff@example.org")
        self.assertNotEqual(self.client.session.session_key, before)

    def test_non_allowlisted_email_gets_no_admin_but_contributor_signin_works(self):
        with self.assertLogs("tirtha.admin_auth", "WARNING"):
            response = self._verify("other@example.org")
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self._django_user_id())
        self.assertEqual(
            self.client.session["tirtha_user_info"]["email"], "other@example.org"
        )
        admin = self.client.get(ADMIN_INDEX)
        self.assertEqual(admin.status_code, 302)
        self.assertIn("/admin/login/", admin["Location"])

    def test_non_allowlisted_signin_ends_an_existing_admin_session(self):
        self.client.force_login(self.boss)
        self._verify("someone@example.org", next_url=None)
        self.assertIsNone(self._django_user_id())
        self.assertEqual(
            self.client.session["tirtha_user_info"]["email"], "someone@example.org"
        )

    def test_unverified_email_is_refused(self):
        response = self._verify("admin@example.org", verified=False)
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self._django_user_id())

    def test_allowlisted_email_without_a_user_is_refused(self):
        with self.settings(ADMIN_GOOGLE_EMAILS=ALLOW + ["ghost@example.org"]):
            with self.assertLogs("tirtha.admin_auth", "WARNING"):
                self._verify("ghost@example.org")
        self.assertIsNone(self._django_user_id())
        self.assertFalse(
            get_user_model().objects.filter(email__iexact="ghost@example.org").exists()
        )

    def test_allowlisted_non_staff_user_is_refused(self):
        get_user_model().objects.create_user("plain", email="plain@example.org")
        with self.settings(ADMIN_GOOGLE_EMAILS=["plain@example.org"]):
            self._verify("plain@example.org")
        self.assertIsNone(self._django_user_id())

    def test_inactive_staff_user_is_refused(self):
        self.lab.is_active = False
        self.lab.save()
        self._verify("staff@example.org")
        self.assertIsNone(self._django_user_id())

    def test_ambiguous_email_is_refused(self):
        get_user_model().objects.create_user(
            "dup", email="STAFF@example.org", is_staff=True
        )
        self._verify("staff@example.org")
        self.assertIsNone(self._django_user_id())

    def test_empty_allowlist_by_default(self):
        with self.settings(ADMIN_GOOGLE_EMAILS=[]):
            self._verify("admin@example.org")
        self.assertIsNone(self._django_user_id())

    def test_logout_works(self):
        self._verify("admin@example.org")
        response = self.client.post(reverse("admin:logout"))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self._django_user_id())
        self.assertEqual(self.client.get(ADMIN_INDEX).status_code, 302)


class AdminLoginPage(_Base):
    def test_shows_the_google_button_pointing_at_signin_with_next(self):
        response = self.client.get(
            reverse("admin:login"), {"next": "/project/tirtha/admin/tirtha/site/"}
        )
        self.assertContains(response, "Sign in with Google")
        self.assertContains(response, 'name="password"')  # default: still on
        url = admin_auth._google_signin_url(response.wsgi_request)
        parts = urlsplit(url)
        self.assertEqual(parts.path, reverse("signin"))
        self.assertEqual(
            parse_qs(parts.query)["next"], ["/project/tirtha/admin/tirtha/site/"]
        )

    def test_an_offsite_next_falls_back_to_the_admin_index(self):
        response = self.client.get(
            reverse("admin:login"), {"next": "https://evil.example/"}
        )
        url = admin_auth._google_signin_url(response.wsgi_request)
        self.assertEqual(parse_qs(urlsplit(url).query)["next"], [ADMIN_INDEX])

    def test_signin_keeps_the_admin_next(self):
        with patch(
            "tirtha.views.google.authorize_redirect",
            return_value=__import__("django.http").http.HttpResponse("ok"),
        ):
            self.client.get(reverse("signin"), {"next": ADMIN_INDEX})
        self.assertEqual(self.client.session["auth_next"], ADMIN_INDEX)

    def test_password_post_works_when_enabled(self):
        response = self.client.post(
            reverse("admin:login"),
            {"username": "boss", "password": "pw-boss-123", "next": ADMIN_INDEX},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._django_user_id(), str(self.boss.pk))

    @override_settings(ADMIN_PASSWORD_LOGIN=False)
    def test_password_post_is_refused_when_disabled(self):
        with self.assertLogs("tirtha.admin_auth", "WARNING"):
            response = self.client.post(
                reverse("admin:login"),
                {"username": "boss", "password": "pw-boss-123", "next": ADMIN_INDEX},
            )
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self._django_user_id())
        self.assertContains(response, "Password sign-in is disabled", status_code=403)

    @override_settings(ADMIN_PASSWORD_LOGIN=False)
    def test_disabled_page_shows_only_the_google_button(self):
        response = self.client.get(reverse("admin:login"))
        self.assertContains(response, "Sign in with Google")
        self.assertNotContains(response, 'name="password"')
        self.assertNotContains(response, 'id="login-form"')

    @override_settings(ADMIN_PASSWORD_LOGIN=False)
    def test_disabled_page_redirects_staff_already_signed_in(self):
        self.client.force_login(self.boss)
        response = self.client.get(reverse("admin:login"))
        self.assertEqual(response.status_code, 302)

    def test_csrf_is_enforced_on_the_login_post(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        response = client.post(
            reverse("admin:login"), {"username": "boss", "password": "pw-boss-123"}
        )
        self.assertEqual(response.status_code, 403)

    def test_no_password_reset_route(self):
        from django.urls import NoReverseMatch

        for name in ("admin_password_reset", "password_reset", "rest_framework:login"):
            with self.subTest(name=name), self.assertRaises(NoReverseMatch):
                reverse(name)


@override_settings(ADMIN_PASSWORD_LOGIN=False)
class EmergencyLoginLink(_Base):
    def _link(self, username="boss"):
        out = io.StringIO()
        call_command("admin_login_link", username, stdout=out, stderr=io.StringIO())
        return urlsplit(out.getvalue().strip()).path

    def test_works_once(self):
        path = self._link()
        self.assertTrue(path.startswith("/project/tirtha/admin/login/link/"))
        response = self.client.get(path)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], ADMIN_INDEX)
        self.assertEqual(self._django_user_id(), str(self.boss.pk))
        self.assertEqual(self.client.get(ADMIN_INDEX).status_code, 200)

        self.client.logout()
        with self.assertLogs("tirtha.admin_auth", "WARNING"):
            again = self.client.get(path)
        self.assertEqual(again.status_code, 403)
        self.assertIsNone(self._django_user_id())

    def test_reuse_is_refused_even_if_the_cache_forgot(self):
        path = self._link()
        self.client.get(path)
        self.client.logout()
        cache.clear()
        self.assertEqual(self.client.get(path).status_code, 403)

    def test_expires(self):
        path = self._link()
        with patch(
            "django.core.signing.time.time",
            return_value=__import__("time").time() + admin_auth.LINK_MAX_AGE + 5,
        ):
            response = self.client.get(path)
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self._django_user_id())

    def test_tampered_token_is_refused(self):
        path = self._link()
        self.assertEqual(self.client.get(path[:-3] + "xx/").status_code, 403)

    def test_command_refuses_non_staff(self):
        get_user_model().objects.create_user("plain", email="plain@example.org")
        with self.assertRaises(CommandError):
            self._link("plain")
        with self.assertRaises(CommandError):
            self._link("nobody")

    def test_link_is_refused_if_the_user_lost_staff(self):
        path = self._link()
        self.boss.is_staff = False
        self.boss.save()
        self.assertEqual(self.client.get(path).status_code, 403)
        self.assertIsNone(self._django_user_id())

    def test_link_for_a_non_staff_user_is_refused(self):
        plain = get_user_model().objects.create_user("plain", email="p@example.org")
        path = reverse(
            "admin-login-link", args=[admin_auth.make_login_link_token(plain)]
        )
        self.assertEqual(self.client.get(path).status_code, 403)
        self.assertIsNone(self._django_user_id())
