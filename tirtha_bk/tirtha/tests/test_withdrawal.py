"""
Moderation has to be able to take a photograph out of a reconstruction.

The workspace is an append-only symlink farm, and deliberately so: COLMAP's
database references every image it has registered by path, so pruning would
break a continuation rather than tidy it. The cost was that a moderator could
reject an image, watch the count drop everywhere it is displayed, and have the
next run read the link still sitting in the workspace.

Fontaine Bartholdi had 73 such links against 0 reconstructable images.
"""

from __future__ import annotations

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image as PILImage

from tirtha.models import Contribution, Contributor, Image, Site
from tirtha.workspace import (
    image_refs,
    materialise_site,
    reconstructable_images,
    withdrawn_links,
)


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (16, 16), (3, 4, 5)).save(buffer, format="JPEG")
    return buffer.getvalue()


class WithdrawnLinksAreVisible(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        contribution = Contribution.objects.create(
            site=self.site,
            contributor=Contributor.objects.create(
                name="T", email="t@example.org", active=True
            ),
        )
        self.images = []
        for i in range(3):
            img = Image(
                contribution=contribution,
                original_filename=f"{i}.jpg",
                safety_status="safe",
                seq=i,
            )
            img.image.save(f"{i}.jpg", SimpleUploadedFile(f"{i}.jpg", _jpeg()))
            self.images.append(img)
        self.workspace, _ = materialise_site(self.site)

    def test_a_fresh_workspace_has_nothing_withdrawn(self):
        self.assertEqual(withdrawn_links(self.site, self.workspace), set())
        self.assertEqual(reconstructable_images(self.site).count(), 3)

    def test_rejecting_an_image_leaves_its_link_behind(self):
        """The behaviour the guard exists to detect, stated plainly."""
        victim = self.images[1]
        victim.admin_verdict = "reject"
        victim.save(update_fields=["admin_verdict"])

        self.assertEqual(reconstructable_images(self.site).count(), 2)
        # Re-materialising does not remove it -- that is the append-only rule.
        materialise_site(self.site)
        stale = withdrawn_links(self.site, self.workspace)
        self.assertEqual(len(stale), 1)

    def test_the_withdrawn_link_is_the_rejected_image(self):
        victim = self.images[2]
        expected = next(
            ref.link_name
            for ref in image_refs(self.site)
            if ref.image_id == str(victim.ID)
        )
        victim.admin_verdict = "reject"
        victim.save(update_fields=["admin_verdict"])
        self.assertEqual(withdrawn_links(self.site, self.workspace), {expected})

    def test_an_image_marked_unsafe_is_also_withdrawn(self):
        victim = self.images[0]
        victim.safety_status = "rejected"
        victim.save(update_fields=["safety_status"])
        self.assertEqual(len(withdrawn_links(self.site, self.workspace)), 1)

    def test_a_site_with_no_workspace_yet_reports_nothing(self):
        other = Site.objects.create(name="O", country="India", state="X", district="Y")
        from tirtha.workspace import Workspace

        self.assertEqual(withdrawn_links(other, Workspace.for_site(other.ID)), set())
