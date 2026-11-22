"""
Tests for the evidence-shaped Image fields (internal design notes).

The old `label` stored one verdict across two orthogonal axes, so it could not
express "safe but blurry" and carried no provenance. These pin the replacement:
safety and quality are separate, measurements are separate from human decisions,
and usability is a query rather than a stored conclusion.
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from tirtha.models import Contribution, Contributor, Image, Site, SourceVideo


class _Base(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="Mukteswar Temple", country="India", state="Odisha", district="Khordha"
        )
        self.contributor = Contributor.objects.create(name="T", email="t@example.org")
        self.contribution = Contribution.objects.create(
            site=self.site, contributor=self.contributor
        )

    def _image(self, name="a.jpg", **kw):
        return Image.objects.create(
            contribution=self.contribution,
            image=SimpleUploadedFile(name, b"x", content_type="image/jpeg"),
            **kw,
        )


class AxisSeparationTests(_Base):
    def test_safety_and_quality_are_independent(self):
        # The case the old single field could not express.
        img = self._image(safety_status="safe", sharpness_score=3.1)
        img.refresh_from_db()
        self.assertEqual(img.safety_status, "safe")
        self.assertLess(img.sharpness_score, 10)

    def test_default_is_not_yet_assessed(self):
        img = self._image()
        self.assertEqual(img.safety_status, "pending")
        self.assertIsNone(img.sharpness_score)
        self.assertIsNone(img.safety_score)
        self.assertEqual(img.admin_verdict, "")

    def test_human_verdict_is_separate_from_measurements(self):
        img = self._image(admin_verdict="accept")
        img.refresh_from_db()
        # Accepting by hand asserts nothing about what a checker measured.
        self.assertEqual(img.admin_verdict, "accept")
        self.assertEqual(img.safety_status, "pending")
        self.assertIsNone(img.sharpness_score)


class UsabilityIsAQueryTests(_Base):
    def test_usable_set_is_derived_not_stored(self):
        keep = self._image("keep.jpg", safety_status="safe")
        self._image("nsfw.jpg", safety_status="rejected")
        self._image("flag.jpg", safety_status="flagged")
        self._image("no.jpg", admin_verdict="reject")
        usable = Image.objects.exclude(
            safety_status__in=("rejected", "flagged")
        ).exclude(admin_verdict="reject")
        self.assertEqual(list(usable), [keep])

    def test_threshold_changes_need_no_relabelling(self):
        # The point of storing the measurement: policy can move without touching
        # 19,321 rows.
        self._image("sharp.jpg", sharpness_score=120.0)
        self._image("blurry.jpg", sharpness_score=4.0)
        self.assertEqual(Image.objects.filter(sharpness_score__gte=50).count(), 1)
        self.assertEqual(Image.objects.filter(sharpness_score__gte=1).count(), 2)


class ProvenanceTests(_Base):
    def test_stills_have_no_video_provenance(self):
        img = self._image()
        self.assertIsNone(img.source_video)
        self.assertIsNone(img.frame_index)

    def test_video_frames_are_attributable_to_their_source(self):
        # 500 frames from one clip must not outrank 50 deliberate photographs
        # on the leaderboard (internal design notes).
        video = SourceVideo.objects.create(
            contribution=self.contribution,
            video=SimpleUploadedFile("v.mp4", b"x", content_type="video/mp4"),
        )
        frames = [
            self._image(f"f{i}.jpg", source_video=video, frame_index=i)
            for i in range(3)
        ]
        self.assertEqual(video.frames.count(), 3)
        self.assertEqual([f.frame_index for f in frames], [0, 1, 2])

    def test_frames_are_separable_from_stills_in_a_query(self):
        video = SourceVideo.objects.create(
            contribution=self.contribution,
            video=SimpleUploadedFile("v.mp4", b"x", content_type="video/mp4"),
        )
        self._image("still.jpg")
        self._image("frame.jpg", source_video=video, frame_index=0)
        self.assertEqual(Image.objects.filter(source_video__isnull=True).count(), 1)


class OrderingTests(_Base):
    def test_seq_is_optional_until_assigned(self):
        self.assertIsNone(self._image().seq)

    def test_seq_orders_images_within_a_site(self):
        c = self._image("c.jpg", seq=3)
        a = self._image("a.jpg", seq=1)
        b = self._image("b.jpg", seq=2)
        self.assertEqual(list(Image.objects.order_by("seq")), [a, b, c])


class ExifTests(_Base):
    def test_exif_fields_round_trip(self):
        img = self._image(
            camera_make="Apple",
            camera_model="iPhone 15 Pro",
            focal_length="6.86mm",
            gps_latitude=20.24,
            gps_longitude=85.83,
            original_filename="IMG_0001.HEIC",
            original_file_size=4_200_000,
            uploaded_file_size=1_100_000,
            is_compressed=True,
        )
        img.refresh_from_db()
        self.assertEqual(img.camera_make, "Apple")
        self.assertTrue(img.is_compressed)
        self.assertEqual(img.original_file_size, 4_200_000)

    def test_file_sizes_are_wide_enough_for_video_masters(self):
        # A 2-minute 4K HEVC clip runs to several hundred MB (internal design notes).
        img = self._image(original_file_size=3_500_000_000)
        img.refresh_from_db()
        self.assertEqual(img.original_file_size, 3_500_000_000)
