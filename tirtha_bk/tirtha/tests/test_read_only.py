"""
What a read-only deployment actually blocks.

The demo shares beta's database, and the only thing making that safe is that
nothing on it can write. The middleware enforced that by HTTP method alone,
which is not where the writes are: the whole OAuth flow is GET, and
`views._authenticate_user` calls `Contributor.objects.get_or_create` on the way
through. A demo visitor could sign in -- creating a real row in beta's database
-- and then could not sign out, because sign-out is a POST.

So this pins both directions. It is the kind of gap that a "block by method"
rule will grow again the next time a GET view learns to write.
"""

from django.conf import settings
from django.test import TestCase, override_settings

from tirtha.models import Contributor

# The dev settings do not install `ReadOnlyMiddleware` -- only the demo's do --
# so it has to be added here, or every assertion below passes vacuously against
# a stack that was never going to block anything.
DEMO_MIDDLEWARE = [*settings.MIDDLEWARE, "tirtha.middleware.ReadOnlyMiddleware"]


@override_settings(DEMO_READ_ONLY=True, MIDDLEWARE=DEMO_MIDDLEWARE)
class ReadOnlyDemo(TestCase):
    def test_a_post_is_refused(self):
        response = self.client.post(
            "/project/tirtha/api/v1/site-requests/",
            "{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 405)

    def test_reading_still_works(self):
        self.assertEqual(
            self.client.get("/project/tirtha/api/v1/sites/").status_code, 200
        )

    def test_sign_in_is_refused_even_though_it_is_a_get(self):
        """
        It writes. `_authenticate_user` creates a `Contributor` on first sight of
        an email, so allowing this let a demo visitor write to beta's database
        through a GET.
        """
        response = self.client.get("/project/tirtha/signin/")
        self.assertEqual(response.status_code, 405)

    def test_no_contributor_row_is_created_by_signing_in(self):
        before = Contributor.objects.count()
        self.client.get("/project/tirtha/signin/")
        self.assertEqual(Contributor.objects.count(), before)

    def test_sign_out_is_allowed_even_though_it_is_a_post(self):
        """
        It only removes state, and refusing it strands whoever is already signed
        in -- the account exists, the session is set, and the only way out is to
        clear cookies by hand.
        """
        session = self.client.session
        session["tirtha_user_info"] = {"email": "someone@example.org"}
        session.save()

        response = self.client.post("/project/tirtha/api/v1/sign-out/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("tirtha_user_info", self.client.session)


@override_settings(MIDDLEWARE=DEMO_MIDDLEWARE)
class NotReadOnly(TestCase):
    """Nothing above may leak into a normal deployment."""

    def test_sign_in_is_reachable(self):
        # Redirects to Google (302) or errors on missing OAuth config -- either
        # way, not the 405 the demo returns.
        response = self.client.get("/project/tirtha/signin/", follow=False)
        self.assertNotEqual(response.status_code, 405)

    def test_a_post_is_not_refused_by_the_middleware(self):
        response = self.client.post(
            "/project/tirtha/api/v1/site-requests/",
            '{"name": "Somewhere"}',
            content_type="application/json",
        )
        self.assertNotEqual(response.status_code, 405)
