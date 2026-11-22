"""
Tests for the leaderboard and badges (internal design notes).

The central property is that the board cannot be gamed by uploading. If it
counted uploads, contributors would upload junk -- the incentive is that
direct. An image counts only when it survived moderation, was not rejected by
an operator, and belongs to a site that was actually reconstructed.

Visibility follows section 11.2 throughout: contributors consented to
contribute, not to be listed.
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from tirtha.leaderboard import (
    PERIOD_ALL,
    award_badges,
    compute,
    public_board,
)
from tirtha.models import (
    Badge,
    Contribution,
    Contributor,
    Image,
    LeaderboardEntry,
    Run,
    Site,
    SourceVideo,
)


class _Base(TestCase):
    def setUp(self):
        self.alice = Contributor.objects.create(
            name="Alice", email="a@example.org", profile_public=True, handle="alice"
        )
        self.bob = Contributor.objects.create(name="Bob", email="b@example.org")

    def _site(self, name, *, reconstructed=True, hidden_run=False):
        site = Site.objects.create(
            name=name, country="India", state="Odisha", district="Khordha"
        )
        if reconstructed:
            Run.objects.create(
                site=site, kind="GS", status="Archived", hidden=hidden_run
            )
        return site

    def _images(self, contributor, site, n, *, status="safe", verdict="", video=False):
        contribution = Contribution.objects.create(site=site, contributor=contributor)
        source = None
        if video:
            source = SourceVideo.objects.create(
                contribution=contribution,
                video=SimpleUploadedFile("v.mp4", b"x", content_type="video/mp4"),
            )
        for i in range(n):
            Image.objects.create(
                contribution=contribution,
                image=SimpleUploadedFile(f"{site.ID}{contributor.ID}{i}.jpg", b"x"),
                safety_status=status,
                admin_verdict=verdict,
                source_video=source,
            )


class SpamResistanceTests(_Base):
    def test_images_on_an_unreconstructed_site_do_not_count(self):
        # Uploading into a site that never got built must not earn anything.
        self._images(self.alice, self._site("Never", reconstructed=False), 50)
        compute()
        self.assertEqual(LeaderboardEntry.objects.count(), 0)

    def test_withheld_images_do_not_count(self):
        self._images(self.alice, self._site("S"), 50, status="rejected")
        compute()
        self.assertEqual(LeaderboardEntry.objects.count(), 0)

    def test_unchecked_images_do_not_count(self):
        self._images(self.alice, self._site("S"), 50, status="pending")
        compute()
        self.assertEqual(LeaderboardEntry.objects.count(), 0)

    def test_operator_rejected_images_do_not_count(self):
        self._images(self.alice, self._site("S"), 50, verdict="reject")
        compute()
        self.assertEqual(LeaderboardEntry.objects.count(), 0)

    def test_a_hidden_run_does_not_unlock_a_site(self):
        site = self._site("Hidden", hidden_run=True)
        self._images(self.alice, site, 10)
        compute()
        self.assertEqual(LeaderboardEntry.objects.count(), 0)


class RankingTests(_Base):
    def test_sites_unlocked_outrank_image_volume(self):
        # Someone whose 40 photos completed two sites contributed more than
        # someone who added their 900th angle of one.
        self._images(self.alice, self._site("A"), 20)
        self._images(self.alice, self._site("B"), 20)
        self._images(self.bob, self._site("C"), 200)
        compute()
        entries = list(LeaderboardEntry.objects.order_by("rank"))
        self.assertEqual(entries[0].contributor, self.alice)
        self.assertEqual(entries[0].sites_unlocked, 2)

    def test_images_break_ties_between_equal_site_counts(self):
        site = self._site("Shared")
        self._images(self.alice, site, 5)
        self._images(self.bob, site, 50)
        compute()
        self.assertEqual(
            LeaderboardEntry.objects.order_by("rank").first().contributor, self.bob
        )

    @override_settings(VIDEO_FRAME_WEIGHT=0.2)
    def test_video_frames_are_weighted_below_photographs(self):
        # A ten-minute clip yielding 200 frames must not outrank 50 deliberate
        # photographs (internal design notes).
        site = self._site("S")
        self._images(self.alice, site, 50)
        self._images(self.bob, site, 200, video=True)
        compute()
        self.assertEqual(
            LeaderboardEntry.objects.order_by("rank").first().contributor, self.alice
        )

    def test_frames_and_images_are_counted_separately(self):
        site = self._site("S")
        self._images(self.alice, site, 4)
        self._images(self.alice, site, 6, video=True)
        compute()
        entry = LeaderboardEntry.objects.get(contributor=self.alice)
        self.assertEqual(entry.images_accepted, 4)
        self.assertEqual(entry.frames_accepted, 6)

    def test_ranks_are_contiguous_from_one(self):
        self._images(self.alice, self._site("A"), 5)
        self._images(self.bob, self._site("B"), 3)
        compute()
        self.assertEqual(
            sorted(LeaderboardEntry.objects.values_list("rank", flat=True)), [1, 2]
        )

    def test_recomputing_replaces_rather_than_duplicates(self):
        self._images(self.alice, self._site("A"), 5)
        compute()
        compute()
        self.assertEqual(LeaderboardEntry.objects.filter(period=PERIOD_ALL).count(), 1)


class VisibilityTests(_Base):
    def test_only_opted_in_contributors_appear_publicly(self):
        site = self._site("S")
        self._images(self.alice, site, 5)
        self._images(self.bob, site, 50)
        compute()
        names = [e.contributor for e in public_board()]
        self.assertIn(self.alice, names)
        self.assertNotIn(self.bob, names)

    def test_ranks_are_not_renumbered_after_filtering(self):
        # A rank is a fact about the whole field; renumbering would tell Alice
        # she placed first when she placed second.
        site = self._site("S")
        self._images(self.alice, site, 5)
        self._images(self.bob, site, 500)
        compute()
        visible = list(public_board())
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0].rank, 2)

    def test_the_api_never_exposes_an_email(self):
        site = self._site("S")
        self._images(self.alice, site, 5)
        self._images(self.bob, site, 5)
        compute()
        body = self.client.get(reverse("api-leaderboard")).content
        self.assertNotIn(b"@example.org", body)

    def test_a_private_profile_is_indistinguishable_from_a_missing_one(self):
        self.bob.handle = "bob"
        self.bob.save()
        missing = self.client.get(reverse("api-profile", kwargs={"handle": "nobody"}))
        private = self.client.get(reverse("api-profile", kwargs={"handle": "bob"}))
        self.assertEqual(missing.status_code, private.status_code, 404)
        self.assertEqual(missing.json()["error"], private.json()["error"])


class BadgeTests(_Base):
    def test_badges_are_seeded_by_migration(self):
        self.assertTrue(Badge.objects.filter(slug="site-unlocker").exists())

    def test_a_badge_is_awarded_when_its_rule_holds(self):
        self._images(self.alice, self._site("A"), 5)
        compute()
        award_badges()
        self.assertTrue(
            self.alice.badge_awards.filter(badge__slug="site-unlocker").exists()
        )

    def test_awarding_twice_does_not_duplicate(self):
        self._images(self.alice, self._site("A"), 5)
        compute()
        award_badges()
        award_badges()
        self.assertEqual(
            self.alice.badge_awards.filter(badge__slug="site-unlocker").count(), 1
        )

    def test_an_unearned_badge_is_not_awarded(self):
        self._images(self.alice, self._site("A"), 5)
        compute()
        award_badges()
        self.assertFalse(
            self.alice.badge_awards.filter(badge__slug="five-sites").exists()
        )

    def test_awards_survive_a_recompute_that_would_no_longer_earn_them(self):
        # A badge records that something was true once. Revoking it because an
        # operator later hid a site would punish the contributor for that.
        site = self._site("A")
        self._images(self.alice, site, 5)
        compute()
        award_badges()
        Run.objects.filter(site=site).update(hidden=True)
        compute()
        award_badges()
        self.assertTrue(
            self.alice.badge_awards.filter(badge__slug="site-unlocker").exists()
        )
