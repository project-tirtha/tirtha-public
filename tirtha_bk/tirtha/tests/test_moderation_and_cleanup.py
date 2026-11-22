"""
Two destructive paths that reported success while doing nothing, or the wrong
thing (internal design notes).

**Marking an image NSFW did not quarantine it.** The admin action calls
`queryset.update(safety_status="rejected")`, and Django's `QuerySet.update()`
issues one UPDATE and fires no `pre_save`/`post_save` signals -- documented
behaviour. The entire quarantine lives in `pre_save_image`, which moves the file
out of the served tree into `images/nsfw/` and rewrites `image.name`. So a
moderator selected an image, was told "1 image was successfully marked as NSFW",
and the file stayed exactly where it was, still served. `mark_good` carries a
`FIXME` saying precisely this; `mark_nsfw` had the same defect and no comment,
which is the one where it matters.

**Deleting one image deleted every empty contribution in the database.**
`post_del_image` ran `Contribution.objects.filter(images__isnull=True).delete()`
-- unscoped: not to the deleted image's contribution, not to its site, not to
its contributor. Two populations sit in that state legitimately. `views.upload`
creates the `Contribution` *before* the image loop, so a concurrent upload is
imageless for the duration of its own first save; and a **video-only**
contribution has no images at all until `extract_video_frames_task` finishes,
which is minutes of CPU work. Deleting one image anywhere in the archive
destroyed those, cascading their `SourceVideo` rows and the uploaded video
files with them.

`views.upload` states the intended rule itself: a contribution is empty when it
has neither images nor videos.
"""

import shutil
import tempfile

from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings

from tirtha.admin import ImageAdmin
from tirtha.models import Contribution, Contributor, Image, Site, SourceVideo
from tirtha.workspace import reconstructable_images


def _png():
    """The smallest thing Pillow will accept as an image."""
    return SimpleUploadedFile(
        "x.png",
        (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
            b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        ),
        content_type="image/png",
    )


class _Fixture(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="Mukteswar Temple", country="India", state="Odisha", district="Khordha"
        )
        self.contributor = Contributor.objects.create(
            name="A Person", email="person@example.org"
        )


class ContributionCleanupTests(_Fixture):
    def test_deleting_an_image_does_not_touch_another_contribution(self):
        """
        The unscoped filter is the bug. Another contributor's in-flight upload
        is imageless for the moment between its creation and its first save.
        """
        mine = Contribution.objects.create(site=self.site, contributor=self.contributor)
        image = Image.objects.create(contribution=mine, image=_png())

        someone_else = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )

        image.delete()

        self.assertTrue(
            Contribution.objects.filter(ID=someone_else.ID).exists(),
            "deleting an image destroyed an unrelated, in-flight contribution",
        )

    def test_a_video_only_contribution_survives(self):
        """
        Frames are extracted asynchronously, so a video contribution has zero
        images for minutes. It is not empty; it is not finished.
        """
        video_only = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )
        SourceVideo.objects.create(
            contribution=video_only,
            video=SimpleUploadedFile("clip.mp4", b"\x00\x00\x00\x18ftypmp42"),
        )

        other = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )
        image = Image.objects.create(contribution=other, image=_png())
        image.delete()

        self.assertTrue(
            Contribution.objects.filter(ID=video_only.ID).exists(),
            "a video-only contribution awaiting frame extraction was deleted",
        )

    def test_its_own_contribution_is_cleaned_up_when_it_becomes_empty(self):
        """The behaviour the signal is actually for, which must still hold."""
        contribution = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )
        image = Image.objects.create(contribution=contribution, image=_png())
        image.delete()
        self.assertFalse(Contribution.objects.filter(ID=contribution.ID).exists())


class ModerationActionTests(_Fixture):
    """
    Runs against a temporary MEDIA_ROOT.

    `pre_save_image` physically moves files, and the quarantine is the thing
    under test -- so it has to be given a tree of its own rather than the
    deployment's, which is the whole point of the neighbouring finding about
    signals writing into a real MEDIA_ROOT during tests.
    """

    def setUp(self):
        self._media = tempfile.mkdtemp(prefix="tirtha-moderation-test-")
        self._override = override_settings(MEDIA_ROOT=self._media)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(shutil.rmtree, self._media, True)
        super().setUp()
        self.contribution = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )
        self.image = Image.objects.create(contribution=self.contribution, image=_png())
        self.admin = ImageAdmin(Image, AdminSite())
        self.request = RequestFactory().post("/admin/")
        # `message_user` needs the messages framework; the action's return value
        # is not what is under test.
        self.request._messages = _CollectingMessages()

    def _run(self, action):
        action(self.admin, self.request, Image.objects.filter(pk=self.image.pk))
        self.image.refresh_from_db()

    def test_marking_nsfw_actually_quarantines_the_file(self):
        original = self.image.image.name
        self._run(ImageAdmin.mark_nsfw)
        self.assertEqual(self.image.safety_status, "rejected")
        self.assertIn(
            "/nsfw/",
            self.image.image.name,
            "the row was updated but the file was never moved out of the served "
            "tree -- queryset.update() fires no pre_save signal",
        )
        self.assertNotEqual(original, self.image.image.name)

    def test_there_is_a_way_back_out_of_quarantine(self):
        """
        `mark_nsfw` had no counterpart, so a rejected image could not be
        released from the admin at all -- a moderation control a wrong call
        cannot be undone from.
        """
        self._run(ImageAdmin.mark_nsfw)
        self.assertIn("/nsfw/", self.image.image.name)
        self._run(ImageAdmin.mark_safe)
        self.image.refresh_from_db()
        self.assertNotIn("/nsfw/", self.image.image.name)

        # Moving the file back is only half of it. This released to `pending`
        # -- "not checked" -- and nothing re-checks that, so the image was out
        # of quarantine and still invisible to every reconstruction.
        self.assertEqual(self.image.safety_status, "safe")
        self.assertIn(
            self.image,
            reconstructable_images(self.image.contribution.site),
            "released from quarantine but still not reconstructable",
        )

    def test_the_other_two_actions_still_record_their_verdict(self):
        self._run(ImageAdmin.mark_good)
        self.assertEqual(self.image.admin_verdict, "accept")
        self._run(ImageAdmin.mark_bad)
        self.assertEqual(self.image.admin_verdict, "reject")


class _CollectingMessages:
    def __init__(self):
        self.messages = []

    def add(self, level, message, extra_tags=""):
        self.messages.append((level, message))
