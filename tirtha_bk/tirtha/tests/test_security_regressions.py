"""
Four holes a security audit found, and the assumptions that let each one exist.

Each is one or two lines of fix. What makes them worth pinning is that every one
of them was invisible to the 758 tests that existed: three because the test
suite configured the safe case (`test_upload_view.py` patches `GOOGLE_LOGIN` to
True for the whole module), one because the fixture never built the shape that
breaks (a hidden site with a visible run on it).
"""

from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from tirtha.models import ARK, Contributor, Run, Site


@override_settings(GOOGLE_LOGIN=False, DEBUG=False)
class AnonymousUploadIsRefused(TestCase):
    """
    The critical one: an anonymous POST created a Contribution as the admin.

    `upload` called `_authenticate_user(request.session.get(...))` without first
    checking that anything was in the session -- the only one of that function's
    four callers to skip the check. And `_authenticate_user` answers "the admin"
    on `not GOOGLE_LOGIN` *before* it looks at `user_info` at all. So with the
    flag off, no session was required: files written, a Contribution attributed
    to the project admin, and `post_save_contrib_imageops` queued.

    `GOOGLE_LOGIN=False` is what `build/tirtha.env.example` ships and what
    `local_settings.manual.py` defaults to, so this is the state a deployment
    built from the documented path starts in. The failure is silent: everyone
    appears signed in rather than the site appearing broken.
    """

    def setUp(self):
        self.site = Site.objects.create(
            name="Konark", country="India", state="Odisha", district="Puri"
        )

    def _post(self):
        return self.client.post(
            "/project/tirtha/upload/",
            {"mesh_vid": self.site.verbose_id},
        )

    def test_an_anonymous_upload_does_not_succeed(self):
        response = self._post()
        self.assertNotIn("Success", response.content.decode())

    def test_it_creates_no_contribution(self):
        from tirtha.models import Contribution

        before = Contribution.objects.count()
        self._post()
        self.assertEqual(Contribution.objects.count(), before)

    def test_nothing_is_attributed_to_the_admin(self):
        from tirtha.models import Contribution

        self._post()
        self.assertFalse(
            Contribution.objects.filter(contributor__email__isnull=False).exists()
        )


@override_settings(GOOGLE_LOGIN=False)
class TheAdminShortcutNeedsDebug(TestCase):
    """
    The root cause, rather than the one view that forgot to guard against it.

    Signing everyone in as the admin is a development convenience. On a
    deployment that merely forgot to switch the flag on, it is a bypass. Better
    to refuse loudly than to be quietly wide open.
    """

    def test_it_refuses_when_debug_is_off(self):
        from tirtha.views import _authenticate_user

        with override_settings(DEBUG=False):
            message, contributor = _authenticate_user(None)
        self.assertIsNone(contributor)
        self.assertIn("not configured", message)

    def test_it_still_works_in_development(self):
        from django.conf import settings

        from tirtha.views import _authenticate_user

        Contributor.objects.get_or_create(
            email=settings.ADMIN_MAIL, defaults={"name": "Admin", "active": True}
        )
        with override_settings(DEBUG=True):
            _, contributor = _authenticate_user(None)
        self.assertIsNotNone(contributor)


class HiddenIsHiddenForRunsToo(TestCase):
    """
    `_visible_runs` filtered `Run.hidden` and not `Run.site.hidden`.

    So `/api/v1/runs/<id>/` served a visible run belonging to a *hidden* site --
    its name, its slug, and with an ARK present the model file itself. 25 of the
    59 production sites are hidden. The module docstring opens by stating this
    invariant; every other endpoint kept it.

    The existing fixture created a hidden site and never put a run on it, which
    is exactly why this passed.
    """

    def setUp(self):
        self.hidden = Site.objects.create(
            name="Not for release",
            country="India",
            state="Odisha",
            district="Cuttack",
            hidden=True,
        )
        self.run = Run.objects.create(site=self.hidden, kind="GS", status="Archived")
        self.run.ark = ARK.objects.create(
            ark="99999/h1",
            naan="99999",
            shoulder="/",
            assigned_name="h1",
            url="https://example.org/hidden.splat",
            metadata={},
        )
        self.run.save()

    def test_the_run_endpoint_does_not_serve_it(self):
        response = self.client.get(f"/project/tirtha/api/v1/runs/{self.run.ID}/")
        self.assertEqual(response.status_code, 404)

    def test_its_name_and_model_url_are_not_disclosed(self):
        body = self.client.get(
            f"/project/tirtha/api/v1/runs/{self.run.ID}/"
        ).content.decode()
        self.assertNotIn("Not for release", body)
        self.assertNotIn("hidden.splat", body)

    def test_it_is_not_counted_in_the_platform_totals(self):
        # `total_models` counts published runs, and inherited the same blind
        # spot through `_published_runs`.
        data = self.client.get("/project/tirtha/api/v1/stats/").json()
        self.assertEqual(data["total_models"], 0)


class PaginationCannotBeMadeToRaise(TestCase):
    """
    `min()` clamps a ceiling, not a floor.

    `?page_size=-1` reached `queryset[0:-1]`, which Django refuses with
    "Negative indexing is not supported" -- an unauthenticated 500 on an
    unthrottled endpoint. On production that also means one email to `ADMINS`
    per request, from an account whose app password is one of the seven in
    the internal secrets register.
    """

    def test_a_negative_page_size_is_clamped(self):
        response = self.client.get("/project/tirtha/api/v1/sites/?page_size=-1")
        self.assertEqual(response.status_code, 200)

    def test_a_negative_limit_is_clamped(self):
        response = self.client.get("/project/tirtha/api/v1/leaderboard/?limit=-1")
        self.assertEqual(response.status_code, 200)

    def test_zero_is_clamped_too(self):
        self.assertEqual(
            self.client.get("/project/tirtha/api/v1/sites/?page_size=0").status_code,
            200,
        )

    def test_a_non_integer_is_still_a_400_not_a_500(self):
        self.assertEqual(
            self.client.get("/project/tirtha/api/v1/sites/?page_size=x").status_code,
            400,
        )


class PagingBoundaries(TestCase):
    """
    `?page=` was clamped only at the bottom (internal design notes).

    `page_size` is clamped at both ends a few lines above, with a comment
    explaining that `?page_size=-1` was "an unauthenticated 500 on an endpoint
    with no throttle". `page` got half that treatment: `(page - 1) * page_size`
    handed the driver an offset past the 64-bit boundary and it raised, so
    `?page=99999999999999999999` was a 500 with an HTML body -- breaking the
    invariant that this API always answers in JSON.
    """

    def setUp(self):
        for n in range(3):
            Site.objects.create(name=f"Paging Temple {n}")

    def test_an_absurd_page_does_not_reach_the_database(self):
        response = self.client.get(
            reverse("api-sites"), {"page": "99999999999999999999"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")

    def test_the_boundary_that_used_to_split_200_from_500(self):
        # Measured against the live demo: page_size=100 made 92233720368547758
        # a 200 and 100000000000000000 a 500.
        for page in (92233720368547758, 100000000000000000):
            with self.subTest(page=page):
                response = self.client.get(
                    reverse("api-sites"), {"page": page, "page_size": 100}
                )
                self.assertEqual(response.status_code, 200)

    def test_a_page_past_the_end_is_empty_rather_than_the_last_page(self):
        """
        The terminator a client walking pages expects.

        Clamping `page` instead would hand someone who asked for page 999 the
        last page of results and label it page 999.
        """
        payload = self.client.get(
            reverse("api-sites"), {"page": 999, "page_size": 2}
        ).json()
        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["count"], 3)

    def test_the_first_pages_still_work(self):
        first = self.client.get(
            reverse("api-sites"), {"page": 1, "page_size": 2}
        ).json()
        second = self.client.get(
            reverse("api-sites"), {"page": 2, "page_size": 2}
        ).json()
        self.assertEqual(len(first["results"]), 2)
        self.assertEqual(len(second["results"]), 1)
        self.assertNotEqual(first["results"][0]["id"], second["results"][0]["id"])


class HiddenIsHiddenForTheUploadPathToo(TestCase):
    """
    The same invariant as `HiddenIsHiddenForRunsToo`, one layer down.

    `pre_upload_check` and `upload` were the only two places that reached a Site
    by slug without `exclude(hidden=True)` -- `index`, `search`,
    `api._visible_sites`, `shell` and `embed` all filtered. So a signed-in
    contributor who guessed a slug got `allowupload: true` for any hidden site
    that was not `completed`, and could then POST a Contribution, write files
    and queue `post_save_contrib_imageops` against it. On the live database that
    was 23 of the 25 hidden sites, `Konark_Sun_Temple` among them.

    `legacy_site_redirect` was worse, because it needs no session at all: a
    hidden site answered 301 and an absent one 404, and the `Location` header
    handed over the immutable `Site.ID` as well.

    None of the 758 tests caught any of it, because no fixture built the shape
    that breaks: `test_upload_view.py` creates one visible site and never a
    hidden one. Each test below therefore has a visible-site control, so it
    fails if the filter is ever widened into refusing everything -- an
    assertion that only checks the hidden case passes just as well against a
    broken endpoint.

    internal design notes: a hidden resource and a non-existent one answer identically.
    """

    def setUp(self):
        # `views.auth.GOOGLE_LOGIN` is read at import, so `override_settings` cannot
        # reach it -- the same capture `test_upload_view._Base` patches. Left
        # alone, the visible-site control below fails, which is how this was
        # caught: the control is the test that tests the tests.
        patcher = patch("tirtha.views.auth.GOOGLE_LOGIN", True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.hidden = Site.objects.create(
            name="Konark", country="India", state="Odisha", district="Puri", hidden=True
        )
        self.visible = Site.objects.create(
            name="Rajarani", country="India", state="Odisha", district="Khordha"
        )
        self.absent = "India__Nowhere__Nowhere__Nope"

        self.contributor = Contributor.objects.create(
            name="T", email="t@example.org", active=True
        )
        session = self.client.session
        session["tirtha_user_info"] = {
            "email": self.contributor.email,
            "name": self.contributor.name,
        }
        session.save()

    def _pre_upload(self, verbose_id):
        return self.client.get(reverse("preUpload"), {"mesh_vid": verbose_id}).json()

    def test_a_hidden_site_answers_exactly_as_an_absent_one(self):
        self.assertEqual(
            self._pre_upload(self.hidden.verbose_id), self._pre_upload(self.absent)
        )

    def test_a_hidden_site_is_not_offered_for_upload(self):
        self.assertFalse(self._pre_upload(self.hidden.verbose_id)["allowupload"])

    def test_a_visible_site_still_is(self):
        # The control: without this, refusing everything would pass the above.
        self.assertTrue(self._pre_upload(self.visible.verbose_id)["allowupload"])

    def test_the_legacy_redirect_does_not_confirm_a_hidden_site(self):
        hidden = self.client.get(reverse("legacySite", args=[self.hidden.verbose_id]))
        absent = self.client.get(reverse("legacySite", args=[self.absent]))
        self.assertEqual(hidden.status_code, absent.status_code)
        self.assertNotIn("Location", hidden.headers)

    def test_the_legacy_redirect_still_redirects_a_visible_site(self):
        # The control, again: a 404 for everything would satisfy the test above.
        response = self.client.get(
            reverse("legacySite", args=[self.visible.verbose_id])
        )
        self.assertEqual(response.status_code, 301)
        self.assertIn(self.visible.ID, response.headers["Location"])

    def test_the_hidden_site_id_is_never_disclosed(self):
        response = self.client.get(reverse("legacySite", args=[self.hidden.verbose_id]))
        self.assertNotIn(self.hidden.ID, response.content.decode(errors="replace"))
