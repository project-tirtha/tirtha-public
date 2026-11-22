"""
Tests for the reconstruction trigger (internal design notes).

This replaces "a fixed delay after every contribution", which could not batch:
three contributions in an afternoon meant three full reconstructions, and GS
training has no incremental mode -- it retrains from scratch for the full epoch
budget whether 3 images were added or 300.

Both halves of the policy are load-bearing. Without the day trigger a site with
4 new images waits forever; without the count trigger a busy site rebuilds
constantly.
"""

from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from tirtha.models import Contribution, Contributor, Image, Run, Site
from tirtha.scheduler import pending_images, sites_awaiting_reconstruction


@override_settings(RECON_MIN_NEW_IMAGES=5, RECON_MAX_WAIT_DAYS=14)
class _Base(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.contributor = Contributor.objects.create(name="T", email="t@example.org")

    def _images(self, n, *, site=None, status="safe", age_days=0):
        site = site or self.site
        contribution = Contribution.objects.create(
            site=site, contributor=self.contributor
        )
        created = timezone.now() - timedelta(days=age_days)
        made = []
        for i in range(n):
            img = Image.objects.create(
                contribution=contribution,
                image=SimpleUploadedFile(
                    f"{site.ID}{i}.jpg", b"x", content_type="image/jpeg"
                ),
                safety_status=status,
            )
            Image.objects.filter(pk=img.pk).update(created_at=created)
            made.append(img)
        return contribution, made


@override_settings(RECON_MIN_NEW_IMAGES=5, RECON_MAX_WAIT_DAYS=14)
class TriggerTests(_Base):
    def test_enough_new_images_triggers(self):
        self._images(5)
        candidates = sites_awaiting_reconstruction()
        self.assertEqual(len(candidates), 1)
        self.assertIn("new images", candidates[0].reason)

    def test_too_few_new_images_does_not_trigger_yet(self):
        self._images(2)
        self.assertEqual(sites_awaiting_reconstruction(), [])

    def test_a_long_wait_triggers_even_with_few_images(self):
        # Without this, a site with 4 new images waits forever.
        self._images(2, age_days=20)
        candidates = sites_awaiting_reconstruction()
        self.assertEqual(len(candidates), 1)
        self.assertIn("waited", candidates[0].reason)

    def test_nothing_pending_does_not_trigger(self):
        self.assertEqual(sites_awaiting_reconstruction(), [])

    def test_unchecked_images_do_not_trigger_a_reconstruction(self):
        # An unchecked image is not publishable, so it must not cause a run
        # that would publish it (internal design notes).
        self._images(10, status="pending")
        self.assertEqual(sites_awaiting_reconstruction(), [])

    def test_withheld_images_do_not_trigger(self):
        self._images(10, status="rejected")
        self.assertEqual(sites_awaiting_reconstruction(), [])

    def test_hidden_sites_are_not_scheduled(self):
        hidden = Site.objects.create(
            name="H", country="India", state="X", district="Y", hidden=True
        )
        self._images(10, site=hidden)
        self.assertEqual(sites_awaiting_reconstruction(), [])

    def test_completed_sites_are_not_scheduled(self):
        self.site.completed = True
        self.site.save()
        self._images(10)
        self.assertEqual(sites_awaiting_reconstruction(), [])


@override_settings(RECON_MIN_NEW_IMAGES=5, RECON_MAX_WAIT_DAYS=14)
class PendingImageTests(_Base):
    def test_images_before_the_last_run_are_not_pending(self):
        self._images(5, age_days=10)
        Run.objects.create(site=self.site, kind="GS")
        self.assertEqual(pending_images(self.site).count(), 0)

    def test_images_after_the_last_run_are_pending(self):
        Run.objects.create(site=self.site, kind="GS")
        self._images(3)
        self.assertEqual(pending_images(self.site).count(), 3)

    def test_an_errored_run_does_not_count_as_a_reconstruction(self):
        # Otherwise a failed run would suppress the site forever.
        self._images(6)
        Run.objects.create(site=self.site, kind="GS", status="Error")
        self.assertEqual(pending_images(self.site).count(), 6)

    def test_the_run_images_m2m_is_not_trusted(self):
        # 18 of 81 production runs have an empty M2M (§8); trusting it would
        # make those sites look permanently un-reconstructed and rebuild them
        # every night.
        self._images(5, age_days=10)
        run = Run.objects.create(site=self.site, kind="GS")
        self.assertEqual(run.images.count(), 0)
        self.assertEqual(pending_images(self.site).count(), 0)


@override_settings(RECON_MIN_NEW_IMAGES=5, RECON_MAX_WAIT_DAYS=14)
class FairnessTests(_Base):
    def test_the_longest_waiting_site_is_scheduled_first(self):
        # A popular site must not starve the rest.
        other = Site.objects.create(
            name="Other", country="India", state="X", district="Y"
        )
        self._images(6, age_days=1)
        self._images(6, site=other, age_days=30)
        candidates = sites_awaiting_reconstruction()
        self.assertEqual(candidates[0].site.ID, other.ID)

    def test_each_candidate_names_a_contribution_to_attribute_the_run_to(self):
        contribution, _ = self._images(6)
        candidate = sites_awaiting_reconstruction()[0]
        self.assertEqual(candidate.contribution_id, contribution.ID)
