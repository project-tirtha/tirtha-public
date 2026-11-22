"""
Assertions for five fixes that shipped without one (internal design notes).

The end-to-end review checked these by mutation: delete the fixed line and the
whole suite still passes. That is the review's sharpest procedural point --
a fix commit should add an assertion that fails without the fix, and five in a
row did not.

Each test here was confirmed to fail with its subject reverted.
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from tirtha.models import ARK, Contribution, Contributor, Image, Run, Site
from tirtha.workspace import reconstructable_images


class RejectedImagesAreNotReconstructable(TestCase):
    """
    `workspace.reconstructable_images` excludes `admin_verdict="reject"`.

    The exclusion shipped with no test, and the nearest one
    (`test_usable_set_is_derived_not_stored`) re-implements the query instead of
    calling the function -- so it would agree with a broken implementation.
    """

    def setUp(self):
        self.site = Site.objects.create(name="Verdict Temple")
        contributor = Contributor.objects.create(email="v@example.test")
        self.contribution = Contribution.objects.create(
            site=self.site, contributor=contributor
        )

    def _image(self, **fields):
        return Image.objects.create(
            contribution=self.contribution,
            image=SimpleUploadedFile("x.jpg", b"\xff\xd8\xff\xd9"),
            safety_status="safe",
            **fields,
        )

    def test_a_rejected_image_is_excluded(self):
        keep = self._image()
        self._image(admin_verdict="reject")
        self.assertEqual(list(reconstructable_images(self.site)), [keep])

    def test_an_accepted_one_is_kept(self):
        keep = self._image(admin_verdict="accept")
        self.assertIn(keep, reconstructable_images(self.site))

    def test_no_verdict_at_all_is_kept(self):
        # Blank is the default and means "nobody has looked", which is not the
        # same as "rejected".
        keep = self._image(admin_verdict="")
        self.assertIn(keep, reconstructable_images(self.site))


class AnArkOutlivesItsRun(TestCase):
    """
    Deleting a Run must not destroy its ARK, and an orphaned ARK must resolve.

    Both halves shipped untested. The first is a `post_delete` receiver that
    used to call `instance.ark.delete()`; the second is the `Run.DoesNotExist`
    branch in `resolveARK` that keeps an orphan out of a 500.
    """

    def setUp(self):
        self.site = Site.objects.create(name="Ark Temple")
        self.ark = ARK.objects.create(
            ark="99999/fk4arksurvives",
            naan="99999",
            shoulder="/fk4",
            assigned_name="arksurvives",
            url="https://example.test/x",
            metadata={},
        )
        self.run = Run.objects.create(site=self.site, ark=self.ark, status="Archived")

    def test_deleting_the_run_leaves_the_ark(self):
        self.run.delete()
        self.assertTrue(
            ARK.objects.filter(ark="99999/fk4arksurvives").exists(),
            "one admin click destroyed a persistent identifier",
        )

    def test_an_orphaned_ark_falls_through_instead_of_500ing(self):
        """
        `ark_obj.run` raises `Run.DoesNotExist` once the run is gone. That was
        not in the `except` clause, so every orphan -- a state the fix above
        deliberately creates -- returned a 500.
        """
        self.run.delete()
        response = self.client.get(
            reverse("resolveARK", kwargs={"ark": "ark:/99999/fk4arksurvives"})
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("500", response.get("Location", ""))


class AwaitingRunIsItsOwnStatus(TestCase):
    """
    A site with photographs and no run is not a site that needs photographs.

    Four production sites hold 278 to 541 images each and had never been
    reconstructed. They were drawn as "needs images -- contribute here", which
    is the one response that cannot help. The status rule shipped with no
    backend test.
    """

    def setUp(self):
        self.site = Site.objects.create(name="Awaiting Temple")
        contributor = Contributor.objects.create(email="a@example.test")
        self.contribution = Contribution.objects.create(
            site=self.site, contributor=contributor
        )

    def _images(self, count):
        for _ in range(count):
            Image.objects.create(
                contribution=self.contribution,
                image=SimpleUploadedFile("x.jpg", b"\xff\xd8\xff\xd9"),
                safety_status="safe",
            )

    def _status(self):
        payload = self.client.get(reverse("api-sites")).json()
        entry = next(s for s in payload["results"] if s["id"] == str(self.site.ID))
        return entry["map_status"]

    def test_images_but_no_run_is_awaiting_not_needs_images(self):
        # Above `MESHOPS_MIN_IMAGES`, which is what separates "nothing has been
        # run yet" from "there is not enough to run on".
        self._images(15)
        self.assertEqual(self._status(), "awaiting_run")

    def test_no_images_is_still_needs_images(self):
        self.assertEqual(self._status(), "needs_images")
