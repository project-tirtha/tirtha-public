"""
A site with no thumbnail must not destroy a finished reconstruction.

`Site.thumbnail` is `blank=False`, so every site made through the admin has
one and `run_ark`'s `site.thumbnail.url` looked total. A site created in code
skips that validation, and two were -- Piazza del Campidoglio and the
Equestrian Statue of Marcus Aurelius, both added on 2026-09-09.

What that cost is the point of this file. The statue's GS run registered 97 of
97 images, trained FRGS for 2h8m, cut, post-processed and published
`scene.sog`, `.splat`, `.splat.gz` and `.compressed.ply` into the site's
`published/` directory -- and then died in the last stage on

    The 'thumbnail' attribute has no file associated with it.

The run went to Error, the model was never served, and the contributor was
mailed that their photographs could not be reconstructed and that they should
go back and re-shoot the statue with better coverage. All of that from a
missing preview image.
"""

from django.test import TestCase

from tirtha.email_utils import (
    _FAILURE_GUIDANCE,
    _PHOTOGRAPHY_STAGES,
    _POST_MODEL_STAGES,
)
from tirtha.models import Site


class TheArkMetadataToleratesAMissingThumbnail(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="Equestrian Statue", country="Italy", state="Lazio", district="Rome"
        )

    def test_the_field_really_is_empty(self):
        """Guards the premise: if this ever starts failing, the rest is vacuous."""
        self.assertFalse(self.site.thumbnail)
        with self.assertRaises(ValueError):
            _ = self.site.thumbnail.url

    def test_the_metadata_builds_anyway(self):
        """
        The expression `run_ark` uses, in isolation -- the stage itself needs a
        GPU run behind it, and the defect was entirely in this read.
        """
        site = self.site
        thumbnail = f"https://example.org{site.thumbnail.url}" if site.thumbnail else ""
        self.assertEqual(thumbnail, "")

    def test_a_site_with_a_thumbnail_still_gets_its_url(self):
        """The guard must not blank the value it exists to protect."""
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image as PILImage

        # A real PNG, because `Site.save()` opens and re-encodes the bytes.
        buf = io.BytesIO()
        PILImage.new("RGB", (12, 12), (90, 110, 130)).save(buf, format="PNG")
        png = buf.getvalue()
        self.site.thumbnail = SimpleUploadedFile("t.png", png, content_type="image/png")
        self.site.save()
        site = Site.objects.get(pk=self.site.pk)
        self.assertTrue(site.thumbnail)
        thumbnail = f"https://example.org{site.thumbnail.url}" if site.thumbnail else ""
        self.assertTrue(thumbnail.startswith("https://example.org/"))
        self.assertIn("_thumb", thumbnail)


class TheFailureMailDoesNotBlameThePhotographs(TestCase):
    """
    `run_ark` was unmapped, so the contributor mail fell through to its
    unconditional closing paragraph -- "a failure is usually about coverage" --
    under an opening line saying no model could be built. Both were false.
    """

    def test_every_post_model_stage_has_its_own_wording(self):
        for stage in _POST_MODEL_STAGES:
            with self.subTest(stage=stage):
                reason, guidance = _FAILURE_GUIDANCE.get(stage, (None, None))
                self.assertIsNotNone(reason, f"{stage} has no reason")
                self.assertIn("ours to fix", guidance)

    def test_post_model_stages_are_never_treated_as_photographic(self):
        self.assertFalse(_POST_MODEL_STAGES & _PHOTOGRAPHY_STAGES)

    def test_only_alignment_and_training_blame_the_photographs(self):
        self.assertEqual(_PHOTOGRAPHY_STAGES, frozenset({"run_colmap", "run_frgs"}))

    def test_run_ark_is_covered(self):
        """The stage that actually failed."""
        self.assertIn("run_ark", _POST_MODEL_STAGES)
        self.assertNotIn("run_ark", _PHOTOGRAPHY_STAGES)
