"""
One gate for pixels, on both pipelines (internal design notes).

Unit 5.5 replaced a directory glob with a database-driven materialisation, and
landed on the GS path only. AliceVision was still handed
`MEDIA/models/<id>/images` whole, and `BaseOps.__init__` still globbed it to
decide what `Run.images` recorded -- so a moderator's "reject" was advisory at
the pipeline boundary, and an ARK asserted an image count including photographs
nothing had ever checked.

These assert the wiring, not a reconstruction: reaching the real thing needs
COLMAP, AliceVision and hours on the shared GPU.
"""

import inspect

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from tirtha import workers
from tirtha.models import Contribution, Contributor, Image, Site
from tirtha.workspace import reconstructable_images


class BothPipelinesUseTheSameGate(TestCase):
    def test_alicevision_is_handed_the_materialised_workspace(self):
        """
        Not `self.imageDir`, which is the raw media directory.

        That directory holds every file ever uploaded for the site. Handing it
        to AliceVision meant the moderation columns did nothing on this path.
        """
        source = inspect.getsource(workers.MeshOps.run_aliceVision)
        self.assertIn("materialise_site(site)", source)
        self.assertIn("input_dir=workspace.images", source)
        self.assertNotIn("input_dir=self.imageDir", source)

    def test_the_run_records_what_the_database_says_is_usable(self):
        # `run.images.set()` feeds the ARK's image count, so a directory
        # listing here makes the archival record disagree with moderation.
        source = inspect.getsource(workers.BaseOps.__init__)
        self.assertIn("reconstructable_images(self.site)", source)
        self.assertNotIn(
            'self.imageDir.glob("*") if p.is_file())\n        self.imageUUIDs', source
        )


class SeqIsAssignedAtIngest(TestCase):
    """
    Every uploaded still gets a `seq`, as video frames already do.

    Without one, `workspace.image_refs` falls back to the row's *position* in
    `reconstructable_images(site)` -- an index that shifts when a moderator
    changes a verdict, so COLMAP sees the same photograph under a new link name.
    """

    def setUp(self):
        self.site = Site.objects.create(name="Seq Temple")
        self.contributor = Contributor.objects.create(email="seq@example.test")
        self.contribution = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )

    def _upload(self, count):
        from io import BytesIO

        from PIL import Image as PILImage

        files = []
        for n in range(count):
            buffer = BytesIO()
            PILImage.new("RGB", (16, 16), (n * 10, 80, 70)).save(buffer, format="JPEG")
            files.append(
                SimpleUploadedFile(
                    f"shot{n}.jpg", buffer.getvalue(), content_type="image/jpeg"
                )
            )
        from tirtha.views import store_uploaded_images

        store_uploaded_images(self.contribution, files, compress=False)

    def test_every_upload_gets_one(self):
        self._upload(3)
        seqs = list(
            Image.objects.filter(contribution=self.contribution)
            .order_by("seq")
            .values_list("seq", flat=True)
        )
        self.assertEqual(seqs, [0, 1, 2])

    def test_a_later_contribution_continues_the_site_sequence(self):
        """
        Per-site, not per-contribution: two contributors' photographs are one
        capture sequence for the site, and duplicate `seq` values would collide
        in the symlink farm.
        """
        self._upload(2)
        second = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )
        self.contribution = second
        self._upload(2)
        seqs = sorted(
            Image.objects.filter(contribution__site=self.site).values_list(
                "seq", flat=True
            )
        )
        self.assertEqual(seqs, [0, 1, 2, 3])

    def test_the_link_name_no_longer_depends_on_moderation(self):
        """
        The consequence, directly.

        With `seq` set, rejecting an earlier image leaves the later ones' link
        names untouched. Under the position fallback they all shifted by one,
        and COLMAP re-registered every one of them as a new photograph.
        """
        from tirtha.workspace import image_refs

        self._upload(3)
        Image.objects.filter(contribution__site=self.site).update(safety_status="safe")

        before = {ref.image_id: ref.seq for ref in image_refs(self.site)}

        first = reconstructable_images(self.site).first()
        first.admin_verdict = "reject"
        first.save(update_fields=["admin_verdict"])

        after = {ref.image_id: ref.seq for ref in image_refs(self.site)}
        for image_id, seq in after.items():
            self.assertEqual(
                seq, before[image_id], "a surviving image's link name moved under it"
            )
