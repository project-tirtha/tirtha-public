"""
The two statistics endpoints, and the opt-in they must not leak past.

Both were added because the front end had been asking for them since the port
and nothing served them: the sidebar showed zero images and no contributors for
every site, including the ones with thousands, and the platform card showed
"Could not load statistics" on every page load.

The interesting constraint is not the arithmetic. A contributor who did not
opt into a public profile (internal design notes) must be *counted* but not *named* --
the count is a fact about the site, the name is a fact about a person who asked
not to be listed. An aggregate is exactly the shape of query that forgets that.
"""

from django.test import TestCase

from tirtha.models import ARK, Contribution, Contributor, Image, Run, Site


class _Fixture(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="Mukteswar Temple",
            country="India",
            state="Odisha",
            district="Khordha",
            latitude=20.2451,
            longitude=85.8383,
        )
        self.hidden = Site.objects.create(
            name="Hidden",
            country="India",
            state="Odisha",
            district="Khordha",
            hidden=True,
        )

        self.public = Contributor.objects.create(
            name="Public Person", email="public@example.org", profile_public=True
        )
        self.private = Contributor.objects.create(
            name="Private Person", email="private@example.org", profile_public=False
        )

        for contributor, count in ((self.public, 2), (self.private, 3)):
            contribution = Contribution.objects.create(
                site=self.site, contributor=contributor
            )
            for index in range(count):
                Image.objects.create(
                    contribution=contribution, image=f"{contributor.handle}-{index}.jpg"
                )

        self.run = Run.objects.create(site=self.site, kind="GS", status="Archived")
        self.run.ark = ARK.objects.create(
            ark="99999/aX1",
            naan="99999",
            shoulder="/a",
            assigned_name="X1",
            url="https://example.org/x1.splat",
            metadata={},
        )
        self.run.save()

    def get(self, path):
        response = self.client.get(f"/project/tirtha/api/v1/{path}")
        self.assertEqual(response.status_code, 200, response.content[:200])
        return response.json()


class SiteStats(_Fixture):
    def test_counts_every_contributor_and_image(self):
        data = self.get(f"sites/{self.site.ID}/stats/")
        self.assertEqual(data["total_images"], 5)
        self.assertEqual(data["total_contributors"], 2)

    def test_names_only_the_contributor_who_opted_in(self):
        data = self.get(f"sites/{self.site.ID}/stats/")
        names = [c["name"] for c in data["top_contributors"]]
        self.assertIn("Public Person", names)
        self.assertNotIn("Private Person", names)

    def test_no_email_anywhere(self):
        body = self.client.get(
            f"/project/tirtha/api/v1/sites/{self.site.ID}/stats/"
        ).content.decode()
        self.assertNotIn("@example.org", body)

    def test_a_hidden_site_is_not_addressable(self):
        # Hidden is hidden at every endpoint, including a new one.
        response = self.client.get(
            f"/project/tirtha/api/v1/sites/{self.hidden.ID}/stats/"
        )
        self.assertEqual(response.status_code, 404)

    def test_unknown_site_is_404_not_500(self):
        self.assertEqual(
            self.client.get("/project/tirtha/api/v1/sites/nosuchid/stats/").status_code,
            404,
        )


class PlatformStats(_Fixture):
    def test_counts_visible_sites_only(self):
        data = self.get("stats/")
        self.assertEqual(data["total_sites"], 1)

    def test_counts_published_runs(self):
        # A run counts as a model when it has an ARK -- that is what binds it to
        # a published file (`_published_runs`).
        data = self.get("stats/")
        self.assertEqual(data["total_models"], 1)

    def test_names_only_public_contributors(self):
        data = self.get("stats/")
        names = [c["name"] for c in data["top_contributors"]]
        self.assertNotIn("Private Person", names)

    def test_no_email_anywhere(self):
        body = self.client.get("/project/tirtha/api/v1/stats/").content.decode()
        self.assertNotIn("@example.org", body)


class SessionEndpoint(TestCase):
    def test_anonymous_says_so_without_erroring(self):
        response = self.client.get("/project/tirtha/api/v1/session/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["authenticated"])

    def test_never_cached(self):
        """
        A shared cache that stored one person's answer and served it to the next
        is an account takeover by proxy.
        """
        response = self.client.get("/project/tirtha/api/v1/session/")
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    def test_sign_out_refuses_get(self):
        # So a link in an email or an <img> on another site cannot sign someone
        # out.
        self.assertEqual(
            self.client.get("/project/tirtha/api/v1/sign-out/").status_code, 405
        )

    def test_sign_out_clears_the_session(self):
        session = self.client.session
        session["tirtha_user_info"] = {"email": "someone@example.org"}
        session.save()

        response = self.client.post("/project/tirtha/api/v1/sign-out/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("tirtha_user_info", self.client.session)
