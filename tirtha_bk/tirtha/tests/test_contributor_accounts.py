"""
Tests for the Contributor account link and public profile (internal design notes).

Two things are pinned here. First, that logins are attached to contributors
rather than replacing them: Contributor.ID is a UUID already referenced by three
years of Contributions and Runs, so the link has to be optional and its removal
has to be survivable. Second, that a contributor's identity is not published
because they uploaded a photo -- the profile is opt-in, and nothing public ever
carries the address they signed in with.
"""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from tirtha.models import Contributor


class EmailUniquenessTests(TestCase):
    def test_duplicate_email_is_rejected(self):
        Contributor.objects.create(name="First", email="dup@example.org")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contributor.objects.create(name="Second", email="dup@example.org")

    def test_distinct_emails_are_fine(self):
        Contributor.objects.create(name="First", email="a@example.org")
        Contributor.objects.create(name="Second", email="b@example.org")
        self.assertEqual(Contributor.objects.count(), 2)


class UserLinkTests(TestCase):
    def test_contributor_starts_unlinked(self):
        # Every existing contributor predates accounts, so the link is optional.
        c = Contributor.objects.create(name="Anon", email="anon@example.org")
        self.assertIsNone(c.user)

    def test_link_is_reachable_from_both_sides(self):
        user = get_user_model().objects.create_user(
            username="someone", email="someone@example.org"
        )
        c = Contributor.objects.create(
            name="Someone", email="someone@example.org", user=user
        )
        self.assertEqual(user.contributor, c)

    def test_one_login_cannot_hold_two_contributors(self):
        user = get_user_model().objects.create_user(username="u1")
        Contributor.objects.create(name="A", email="a@example.org", user=user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contributor.objects.create(name="B", email="b@example.org", user=user)

    def test_deleting_the_login_keeps_the_contributor(self):
        # SET_NULL, not CASCADE. Deleting a login must never delete the record of
        # what someone contributed, or the Runs built from it lose their credits.
        user = get_user_model().objects.create_user(username="u2")
        c = Contributor.objects.create(name="C", email="c@example.org", user=user)
        user.delete()
        c.refresh_from_db()
        self.assertIsNotNone(c.pk)
        self.assertIsNone(c.user)


class PublicProfileTests(TestCase):
    def test_profiles_are_private_by_default(self):
        c = Contributor.objects.create(name="Real Name", email="p@example.org")
        self.assertFalse(c.profile_public)
        self.assertEqual(c.public_name, "Anonymous contributor")

    def test_sso_name_is_not_shown_until_opted_in(self):
        c = Contributor.objects.create(
            name="Real Name", email="p2@example.org", profile_public=False
        )
        self.assertNotIn("Real", c.public_name)

    def test_opting_in_shows_the_sso_name_when_no_display_name_is_set(self):
        c = Contributor.objects.create(
            name="Real Name", email="p3@example.org", profile_public=True
        )
        self.assertEqual(c.public_name, "Real Name")

    def test_display_name_overrides_the_sso_name(self):
        # The point of a display name is pseudonymity, so it has to override the
        # Google-supplied one rather than sit alongside it.
        c = Contributor.objects.create(
            name="Real Name",
            email="p4@example.org",
            profile_public=True,
            display_name="wanderer",
        )
        self.assertEqual(c.public_name, "wanderer")

    def test_public_name_never_carries_the_email(self):
        for public in (True, False):
            with self.subTest(profile_public=public):
                c = Contributor.objects.create(
                    name="N", email=f"leak-{public}@example.org", profile_public=public
                )
                self.assertNotIn("@", c.public_name)

    def test_handles_are_unique(self):
        Contributor.objects.create(name="A", email="h1@example.org", handle="taken")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contributor.objects.create(name="B", email="h2@example.org", handle="taken")

    def test_many_contributors_may_have_no_handle(self):
        # unique + null: the constraint must not collapse every handle-less
        # contributor into a single row.
        Contributor.objects.create(name="A", email="n1@example.org")
        Contributor.objects.create(name="B", email="n2@example.org")
        self.assertEqual(Contributor.objects.filter(handle__isnull=True).count(), 2)
