"""
The Django admin must not be shadowed by the React shell's catch-all.

`tirtha.urls` ends with `^project/tirtha/(?:[a-z]{2}/)?.*$`, and `include()`
splices those patterns into the project urlconf where it appears. Anything
mounted after that include therefore sits behind the catch-all and can never be
reached. The admin was, so `/project/tirtha/admin/` returned the SPA -- and with
it went banning a contributor, hiding a site or run, setting `safety_status`
and `admin_verdict`, releasing quarantined uploads, the reconstruction triggers
and site-request approval.

The failure is invisible from the outside: the wrong answer is a 200 with a
valid HTML body. Only resolution tells the truth, so these assert on resolution.
"""

from __future__ import annotations

from django.conf import settings
from django.test import TestCase
from django.urls import resolve, reverse

from tirtha import shell


class TheAdminIsReachable(TestCase):
    def test_the_admin_index_does_not_resolve_to_the_shell(self):
        match = resolve(reverse("admin:index"))
        self.assertIsNot(match.func, shell.shell)
        self.assertNotEqual(match.url_name, "shell-catchall")

    def test_the_login_page_does_not_resolve_to_the_shell(self):
        match = resolve(reverse("admin:login"))
        self.assertIsNot(match.func, shell.shell)

    def test_a_model_changelist_does_not_resolve_to_the_shell(self):
        match = resolve(reverse("admin:tirtha_site_changelist"))
        self.assertIsNot(match.func, shell.shell)
        self.assertNotEqual(match.url_name, "shell-catchall")

    def test_the_moderation_surfaces_all_resolve(self):
        """The models an operator actually has to reach."""
        for model in ("site", "run", "contributor", "contribution", "image"):
            with self.subTest(model=model):
                match = resolve(reverse(f"admin:tirtha_{model}_changelist"))
                self.assertIsNot(
                    match.func,
                    shell.shell,
                    f"the {model} change list resolves to the SPA",
                )

    def test_the_admin_is_mounted_before_the_app_include(self):
        """
        Asserted on order, not just on outcome: the outcome can be restored by
        accident and lost again the next time something is appended.
        """
        import tirtha_bk.urls as project_urls

        def _is_app_include(entry):
            # `admin.site.urls` is also a resolver with `url_patterns`, so
            # identify the app by the module it includes, not by shape.
            module = getattr(entry, "urlconf_name", None)
            name = getattr(module, "__name__", module)
            return name == "tirtha.urls"

        def _is_admin(entry):
            return getattr(entry, "app_name", None) == "admin"

        admin_at = next(
            i for i, e in enumerate(project_urls.urlpatterns) if _is_admin(e)
        )
        include_at = next(
            i for i, e in enumerate(project_urls.urlpatterns) if _is_app_include(e)
        )
        self.assertLess(
            admin_at,
            include_at,
            "admin.site.urls must be mounted before include('tirtha.urls'), "
            "or the shell catch-all swallows it",
        )


class TheShellStillCatchesEverythingElse(TestCase):
    def test_an_unclaimed_path_still_reaches_the_app(self):
        self.assertIs(resolve("/project/tirtha/site/abc123/").func, shell.shell)

    def test_a_language_prefixed_path_still_reaches_the_app(self):
        self.assertIs(resolve("/project/tirtha/or/site/abc123/").func, shell.shell)

    def test_the_api_is_not_shadowed(self):
        match = resolve(reverse("api-sites"))
        self.assertIsNot(match.func, shell.shell)


class TheSlashLessAdminUrlRedirects(TestCase):
    """
    `/project/tirtha/admin` -- no trailing slash -- reported "Page not found".

    Not Django's 404: the SPA's, with a 200, offering "Back to the map". The
    same invisible failure the ordering comment in `tirtha_bk/urls.py`
    describes, reached by a different route. An operator who types the address
    without the slash concludes the admin has been removed.

    `APPEND_SLASH` does not cover it. `CommonMiddleware` appends a slash only
    when the slash-less URL fails to resolve, and this one resolves -- to the
    React catch-all. So the redirect has to be explicit, and mounted above the
    include like the admin itself.
    """

    def test_it_redirects_rather_than_returning_the_app(self):
        response = self.client.get(f"/{settings.PRE_URL}admin")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/{settings.PRE_URL}admin/")

    def test_and_the_redirect_lands_on_the_admin(self):
        # Following it must reach the login page, not the shell.
        response = self.client.get(f"/{settings.PRE_URL}admin", follow=True)
        self.assertContains(response, "Project Tirtha", status_code=200)
        self.assertNotContains(response, "Back to the map")
