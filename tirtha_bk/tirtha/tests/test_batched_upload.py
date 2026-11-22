"""
Batched uploads: one contribution arriving over several requests.

`POST upload/` with `batched=1` opens (or appends to) a contribution that stays
*open* -- `finalized_at` NULL -- until `POST upload/finish/`. Nothing
downstream may see an open contribution, a retried batch must not store a file
twice, and a client that disappears must not leave the photographs it already
sent stranded.
"""

from __future__ import annotations

import importlib
import io
import json
import os
from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image as PILImage

from tirtha.models import Contribution, Contributor, Image, Site, SourceVideo


def _jpeg(name="photo.jpg") -> SimpleUploadedFile:
    buffer = io.BytesIO()
    PILImage.new("RGB", (48, 48), (120, 80, 40)).save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


def _video(name="clip.mp4") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"\x00\x00\x00 ftypmp42", content_type="video/mp4")


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        patcher = patch("tirtha.views.auth.GOOGLE_LOGIN", True)
        patcher.start()
        self.addCleanup(patcher.stop)
        # No broker in tests: every enqueue is observed, never sent.
        self.imageops = self._patch("tirtha.tasks.post_save_contrib_imageops.delay")
        self.frames = self._patch("tirtha.tasks.extract_video_frames_task.delay")
        self.mail = self._patch("tirtha.tasks.notify_admin_new_contribution_task.delay")

        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.other_site = Site.objects.create(
            name="T", country="India", state="Odisha", district="Puri"
        )
        self.alice = Contributor.objects.create(
            name="Alice", email="alice@example.org", active=True
        )
        self.bob = Contributor.objects.create(
            name="Bob", email="bob@example.org", active=True
        )
        self.sign_in(self.alice)

    def _patch(self, target):
        patcher = patch(target)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def sign_in(self, contributor):
        session = self.client.session
        session["tirtha_user_info"] = {
            "email": contributor.email,
            "name": contributor.name,
        }
        session.save()

    def batch(self, images=(), keys=None, contribution_id=None, site=None, **extra):
        images = list(images)
        payload = {
            "batched": "1",
            "mesh_vid": (site or self.site).verbose_id,
            "images": images,
            **extra,
        }
        if keys is None:
            keys = [f"k-{f.name.replace('.', '_')}" for f in images]
        if keys:
            payload["image_keys"] = keys
        if contribution_id:
            payload["contribution_id"] = str(contribution_id)
        return self.client.post(reverse("upload"), payload)

    def finish(self, contribution_id):
        return self.client.post(
            reverse("upload_finish"),
            json.dumps({"contribution_id": str(contribution_id)}),
            content_type="application/json",
        )

    def discard(self, contribution_id):
        return self.client.post(
            reverse("upload_discard"),
            json.dumps({"contribution_id": str(contribution_id)}),
            content_type="application/json",
        )

    def open_upload(self, *names):
        response = self.batch([_jpeg(n) for n in (names or ("a.jpg",))])
        self.assertEqual(response.status_code, 200, response.content)
        return Contribution.objects.get(ID=response.json()["contribution_id"])


class FirstBatch(_Base):
    def test_it_opens_a_contribution_and_enqueues_nothing(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.batch([_jpeg("a.jpg"), _jpeg("b.jpg")])
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["received"], ["k-a_jpg", "k-b_jpg"])
        self.assertEqual(data["count"], 2)
        contribution = Contribution.objects.get(ID=data["contribution_id"])
        self.assertIsNone(contribution.finalized_at)
        self.assertEqual(
            set(contribution.images.values_list("client_key", flat=True)),
            {"k-a_jpg", "k-b_jpg"},
        )
        self.imageops.assert_not_called()
        self.frames.assert_not_called()
        self.mail.assert_not_called()

    def test_it_requires_a_signed_in_contributor(self):
        session = self.client.session
        session["tirtha_user_info"] = None
        session.save()
        response = self.batch([_jpeg()])
        self.assertEqual(Contribution.objects.count(), 0)
        self.assertNotEqual(response.json().get("status"), "Success")

    def test_mismatched_keys_are_refused(self):
        response = self.batch([_jpeg("a.jpg"), _jpeg("b.jpg")], keys=["only-one"])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "bad_keys")
        self.assertEqual(Contribution.objects.count(), 0)

    def test_an_unreadable_file_is_reported_not_fatal(self):
        bad = SimpleUploadedFile("x.jpg", b"not an image", content_type="image/jpeg")
        response = self.batch([bad, _jpeg("ok.jpg")])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["received"], ["k-ok_jpg"])
        self.assertEqual(response.json()["rejected"], ["k-x_jpg"])

    def test_videos_are_stored_with_their_keys(self):
        response = self.client.post(
            reverse("upload"),
            {
                "batched": "1",
                "mesh_vid": self.site.verbose_id,
                "videos": [_video()],
                "video_keys": ["vid-1"],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SourceVideo.objects.get().client_key, "vid-1")
        self.frames.assert_not_called()

    def test_a_hidden_site_is_refused(self):
        Site.objects.filter(pk=self.site.pk).update(hidden=True)
        self.assertEqual(self.batch([_jpeg()]).status_code, 404)


class Append(_Base):
    def test_a_second_batch_joins_the_same_contribution(self):
        contribution = self.open_upload("a.jpg")
        response = self.batch([_jpeg("b.jpg")], contribution_id=contribution.ID)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["contribution_id"], str(contribution.ID))
        self.assertEqual(response.json()["count"], 2)
        self.assertEqual(Contribution.objects.count(), 1)
        contribution.refresh_from_db()
        self.assertIsNotNone(contribution.last_batch_at)

    def test_another_contributor_cannot_append(self):
        contribution = self.open_upload()
        self.sign_in(self.bob)
        response = self.batch([_jpeg("b.jpg")], contribution_id=contribution.ID)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "forbidden")
        self.assertEqual(contribution.images.count(), 1)

    def test_it_cannot_switch_sites(self):
        contribution = self.open_upload()
        response = self.batch(
            [_jpeg("b.jpg")], contribution_id=contribution.ID, site=self.other_site
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "wrong_site")

    @override_settings(UPLOAD_OPEN_TTL_MINUTES=120)
    def test_an_idle_upload_has_expired(self):
        contribution = self.open_upload()
        Contribution.objects.filter(pk=contribution.pk).update(
            last_batch_at=timezone.now() - timedelta(minutes=121)
        )
        response = self.batch([_jpeg("b.jpg")], contribution_id=contribution.ID)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "expired")

    def test_a_finalized_contribution_takes_no_more(self):
        contribution = self.open_upload()
        self.finish(contribution.ID)
        response = self.batch([_jpeg("b.jpg")], contribution_id=contribution.ID)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "finalized")

    def test_an_unknown_id_is_not_found(self):
        response = self.batch(
            [_jpeg()], contribution_id="00000000-0000-0000-0000-000000000000"
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Contribution.objects.count(), 0)


class RetriedBatches(_Base):
    def test_a_retried_batch_stores_nothing_twice(self):
        contribution = self.open_upload("a.jpg")
        first = self.batch(
            [_jpeg("b.jpg"), _jpeg("c.jpg")], contribution_id=contribution.ID
        )
        again = self.batch(
            [_jpeg("b.jpg"), _jpeg("c.jpg")], contribution_id=contribution.ID
        )
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["received"], first.json()["received"])
        self.assertEqual(again.json()["received"], ["k-b_jpg", "k-c_jpg"])
        self.assertEqual(contribution.images.count(), 3)

    def test_a_key_repeated_within_a_batch_is_stored_once(self):
        response = self.batch([_jpeg("a.jpg"), _jpeg("a.jpg")])
        self.assertEqual(response.json()["received"], ["k-a_jpg"])
        self.assertEqual(Image.objects.count(), 1)

    def test_a_retried_first_batch_resumes_rather_than_opening_another(self):
        first = self.batch([_jpeg("a.jpg")])
        again = self.batch([_jpeg("a.jpg")])
        self.assertEqual(
            first.json()["contribution_id"], again.json()["contribution_id"]
        )
        self.assertEqual(Contribution.objects.count(), 1)
        self.assertEqual(Image.objects.count(), 1)

    def test_the_same_keys_at_another_site_are_another_upload(self):
        self.batch([_jpeg("a.jpg")])
        self.batch([_jpeg("a.jpg")], site=self.other_site)
        self.assertEqual(Contribution.objects.count(), 2)


class Finish(_Base):
    def test_finish_finalizes_and_enqueues_once(self):
        contribution = self.open_upload("a.jpg", "b.jpg")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.finish(contribution.ID)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "Success")
        self.assertFalse(response.json()["already_finalized"])
        contribution.refresh_from_db()
        self.assertIsNotNone(contribution.finalized_at)
        self.assertIsNotNone(contribution.imageops_enqueued_at)
        self.imageops.assert_called_once_with(str(contribution.ID))
        self.mail.assert_called_once_with(str(contribution.ID))

    def test_finishing_twice_does_nothing_the_second_time(self):
        contribution = self.open_upload()
        with self.captureOnCommitCallbacks(execute=True):
            self.finish(contribution.ID)
        with self.captureOnCommitCallbacks(execute=True):
            second = self.finish(contribution.ID)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["already_finalized"])
        self.imageops.assert_called_once()
        self.mail.assert_called_once()

    def test_videos_are_extracted_at_finish_not_before(self):
        response = self.client.post(
            reverse("upload"),
            {
                "batched": "1",
                "mesh_vid": self.site.verbose_id,
                "videos": [_video()],
                "video_keys": ["v1"],
            },
        )
        contribution_id = response.json()["contribution_id"]
        self.frames.assert_not_called()
        with self.captureOnCommitCallbacks(execute=True):
            self.finish(contribution_id)
        self.frames.assert_called_once_with(str(SourceVideo.objects.get().ID))
        # Video only: the extraction enqueues the checks itself.
        self.imageops.assert_not_called()

    def test_someone_else_cannot_finish_it(self):
        contribution = self.open_upload()
        self.sign_in(self.bob)
        self.assertEqual(self.finish(contribution.ID).status_code, 403)
        contribution.refresh_from_db()
        self.assertIsNone(contribution.finalized_at)

    def test_an_unknown_id_is_not_found(self):
        self.assertEqual(
            self.finish("00000000-0000-0000-0000-000000000000").status_code, 404
        )

    def test_a_form_encoded_body_works_too(self):
        contribution = self.open_upload()
        response = self.client.post(
            reverse("upload_finish"), {"contribution_id": str(contribution.ID)}
        )
        self.assertEqual(response.status_code, 200)

    def test_an_empty_open_upload_is_deleted_not_finalized(self):
        bad = SimpleUploadedFile("x.jpg", b"not an image", content_type="image/jpeg")
        contribution_id = self.batch([bad]).json()["contribution_id"]
        response = self.finish(contribution_id)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Contribution.objects.filter(ID=contribution_id).exists())


class Discard(_Base):
    def test_an_open_upload_is_deleted_with_its_files(self):
        contribution = self.open_upload("a.jpg")
        path = contribution.images.get().image.path
        self.assertTrue(os.path.exists(path))
        with self.captureOnCommitCallbacks(execute=True):
            response = self.discard(contribution.ID)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["discarded"])
        self.assertFalse(Contribution.objects.filter(ID=contribution.ID).exists())
        self.assertEqual(Image.objects.count(), 0)
        self.assertFalse(os.path.exists(path))

    def test_a_finalized_contribution_is_never_discarded(self):
        contribution = self.open_upload()
        self.finish(contribution.ID)
        response = self.discard(contribution.ID)
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Contribution.objects.filter(ID=contribution.ID).exists())

    def test_a_legacy_contribution_is_never_discarded(self):
        contribution = Contribution.objects.create(
            site=self.site, contributor=self.alice
        )
        self.assertEqual(self.discard(contribution.ID).status_code, 409)
        self.assertTrue(Contribution.objects.filter(ID=contribution.ID).exists())

    def test_someone_else_cannot_discard_it(self):
        contribution = self.open_upload()
        self.sign_in(self.bob)
        self.assertEqual(self.discard(contribution.ID).status_code, 403)
        self.assertTrue(Contribution.objects.filter(ID=contribution.ID).exists())

    def test_discarding_twice_is_harmless(self):
        contribution = self.open_upload()
        self.discard(contribution.ID)
        response = self.discard(contribution.ID)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["discarded"])


class LegacyUploadIsUnchanged(_Base):
    def test_one_post_is_finalized_at_once_and_enqueued(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("upload"),
                {"mesh_vid": self.site.verbose_id, "images": [_jpeg()]},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "Success")
        self.assertNotIn("contribution_id", response.json())
        contribution = Contribution.objects.get()
        self.assertIsNotNone(contribution.finalized_at)
        self.assertIsNone(contribution.images.get().client_key)
        self.imageops.assert_called_once()
        self.mail.assert_called_once()


@override_settings(
    UPLOAD_RATE_LIMIT=2,
    UPLOAD_RATE_WINDOW=600,
    UPLOAD_BATCH_RATE_LIMIT=6,
    UPLOAD_BATCH_RATE_WINDOW=600,
)
class Throttle(_Base):
    def test_batches_do_not_count_as_contributions(self):
        contribution = self.open_upload("a.jpg")
        for n in range(3):
            response = self.batch([_jpeg(f"n{n}.jpg")], contribution_id=contribution.ID)
            self.assertEqual(response.status_code, 200)
        # One contribution so far, so a second may still be opened.
        self.assertEqual(
            self.batch([_jpeg("other.jpg")], site=self.other_site).status_code, 200
        )

    def test_contributions_created_are_counted(self):
        self.batch([_jpeg("a.jpg")])
        self.batch([_jpeg("b.jpg")], site=self.other_site)
        response = self.batch([_jpeg("c.jpg")], keys=["fresh"])
        self.assertEqual(response.status_code, 429)
        self.assertEqual(Contribution.objects.count(), 2)

    def test_batches_have_their_own_limit(self):
        contribution = self.open_upload("a.jpg")
        codes = [
            self.batch(
                [_jpeg(f"n{n}.jpg")], contribution_id=contribution.ID
            ).status_code
            for n in range(6)
        ]
        self.assertEqual(codes[:5], [200] * 5)
        self.assertEqual(codes[5], 429)

    def test_the_legacy_upload_still_counts_against_the_contribution_limit(self):
        for _ in range(2):
            self.client.post(
                reverse("upload"),
                {"mesh_vid": self.site.verbose_id, "images": [_jpeg()]},
            )
        response = self.batch([_jpeg("z.jpg")])
        self.assertEqual(response.status_code, 429)


@override_settings(UPLOAD_OPEN_TTL_MINUTES=120)
class AbandonedUploads(_Base):
    def _age(self, contribution, minutes):
        Contribution.objects.filter(pk=contribution.pk).update(
            last_batch_at=timezone.now() - timedelta(minutes=minutes)
        )

    def _sweep(self):
        from tirtha.tasks import finalize_abandoned_uploads_task

        with self.captureOnCommitCallbacks(execute=True):
            return finalize_abandoned_uploads_task()

    def _confirmed_upload(self):
        """An upload whose client has seen its id: a second batch named it."""
        contribution = self.open_upload()
        response = self.batch([_jpeg("second.jpg")], contribution_id=contribution.ID)
        self.assertEqual(response.status_code, 200)
        contribution.refresh_from_db()
        self.assertEqual(contribution.confirmed_batches, 1)
        return contribution

    def test_an_idle_upload_with_photos_is_finalized(self):
        contribution = self._confirmed_upload()
        self._age(contribution, 121)
        self.assertEqual(self._sweep(), {"finalized": 1, "deleted": 0})
        contribution.refresh_from_db()
        self.assertIsNotNone(contribution.finalized_at)
        self.imageops.assert_called_once_with(str(contribution.ID))
        self.mail.assert_called_once()

    def test_an_idle_empty_upload_is_deleted(self):
        contribution = Contribution.objects.create(
            site=self.site,
            contributor=self.alice,
            finalized_at=None,
            last_batch_at=timezone.now() - timedelta(minutes=200),
        )
        self.assertEqual(self._sweep(), {"finalized": 0, "deleted": 1})
        self.assertFalse(Contribution.objects.filter(pk=contribution.pk).exists())

    def test_an_unacknowledged_first_batch_is_deleted_not_finalized(self):
        # A Cancel or a closed tab during the first batch: the client never
        # learnt the id, so nothing proves it still wanted these photographs.
        contribution = self.open_upload()
        path = contribution.images.get().image.path
        self._age(contribution, 121)
        self.assertEqual(self._sweep(), {"finalized": 0, "deleted": 1})
        self.assertFalse(Contribution.objects.filter(pk=contribution.pk).exists())
        self.assertFalse(os.path.exists(path))
        self.imageops.assert_not_called()
        self.mail.assert_not_called()

    def test_a_retried_first_batch_is_not_an_acknowledgement(self):
        contribution = self.open_upload("a.jpg")
        self.batch([_jpeg("a.jpg")])  # resumed by its keys, no id named
        contribution.refresh_from_db()
        self.assertEqual(contribution.confirmed_batches, 0)

    def test_a_banned_contributors_upload_is_deleted(self):
        contribution = self._confirmed_upload()
        self._age(contribution, 121)
        Contributor.objects.filter(pk=self.alice.pk).update(banned=True)
        self.assertEqual(self._sweep(), {"finalized": 0, "deleted": 1})
        self.assertFalse(Contribution.objects.filter(pk=contribution.pk).exists())
        self.imageops.assert_not_called()

    def test_an_inactive_contributors_upload_is_deleted(self):
        contribution = self._confirmed_upload()
        self._age(contribution, 121)
        Contributor.objects.filter(pk=self.alice.pk).update(active=False)
        self.assertEqual(self._sweep(), {"finalized": 0, "deleted": 1})

    def test_an_active_upload_is_left_alone(self):
        contribution = self.open_upload()
        self._age(contribution, 30)
        self.assertEqual(self._sweep(), {"finalized": 0, "deleted": 0})
        contribution.refresh_from_db()
        self.assertIsNone(contribution.finalized_at)
        self.imageops.assert_not_called()

    def test_a_finished_upload_is_not_finalized_again(self):
        contribution = self.open_upload()
        with self.captureOnCommitCallbacks(execute=True):
            self.finish(contribution.ID)
        self._age(contribution, 500)
        self.assertEqual(self._sweep(), {"finalized": 0, "deleted": 0})
        self.imageops.assert_called_once()

    def test_it_is_on_the_beat_schedule(self):
        from tirtha import tasks

        with patch.object(tasks.app, "add_periodic_task") as sender:
            tasks.setup_periodic_tasks(tasks.app)
        names = [c.kwargs.get("name") for c in sender.call_args_list]
        self.assertIn("finalize_abandoned_uploads_task", names)


class DownstreamIgnoresOpenUploads(_Base):
    def _open_with_safe_image(self):
        contribution = self.open_upload()
        # As if something had checked it anyway.
        contribution.images.update(safety_status="safe")
        return contribution

    def test_reconstructable_images_excludes_them(self):
        from tirtha.workspace import reconstructable_images

        contribution = self._open_with_safe_image()
        self.assertEqual(reconstructable_images(self.site).count(), 0)
        Contribution.objects.filter(pk=contribution.pk).update(
            finalized_at=timezone.now()
        )
        self.assertEqual(reconstructable_images(self.site).count(), 1)

    def test_the_scheduler_does_not_count_them(self):
        from tirtha.scheduler import pending_images

        self._open_with_safe_image()
        self.assertEqual(pending_images(self.site).count(), 0)

    @override_settings(IMAGEOPS_STUCK_AFTER_MINUTES=60)
    def test_the_stuck_ingest_sweep_does_not_enqueue_them(self):
        from tirtha.tasks import requeue_stuck_contributions_task

        contribution = self.open_upload()
        Contribution.objects.filter(pk=contribution.pk).update(
            contributed_at=timezone.now() - timedelta(minutes=90)
        )
        with patch("tirtha.tasks.post_save_contrib_imageops.apply_async") as enqueue:
            self.assertEqual(requeue_stuck_contributions_task(), 0)
        enqueue.assert_not_called()

        Contribution.objects.filter(pk=contribution.pk).update(
            finalized_at=timezone.now()
        )
        with patch("tirtha.tasks.post_save_contrib_imageops.apply_async") as enqueue:
            self.assertEqual(requeue_stuck_contributions_task(), 1)

    def test_imageops_skips_an_open_contribution(self):
        from tirtha.tasks import post_save_contrib_imageops

        contribution = self.open_upload()
        with patch("tirtha.imageops.ImageOps.check_images") as checks:
            post_save_contrib_imageops(str(contribution.ID))
        checks.assert_not_called()
        contribution.refresh_from_db()
        self.assertFalse(contribution.processed)


class Migration0032(TestCase):
    def test_existing_contributions_count_as_finalized(self):
        module = importlib.import_module("tirtha.migrations.0032_batched_uploads")
        site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        contributor = Contributor.objects.create(name="A", email="a@example.org")
        contribution = Contribution.objects.create(
            site=site, contributor=contributor, finalized_at=None
        )
        module.backfill_finalized_at(apps, None)
        contribution.refresh_from_db()
        self.assertEqual(contribution.finalized_at, contribution.contributed_at)


class AdminFilter(TestCase):
    def test_open_and_finalized_are_separable(self):
        from django.contrib import admin as django_admin

        from tirtha.admin import ContributionAdmin, UploadStateFilter

        site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        contributor = Contributor.objects.create(name="A", email="a@example.org")
        open_one = Contribution.objects.create(
            site=site, contributor=contributor, finalized_at=None
        )
        done = Contribution.objects.create(site=site, contributor=contributor)
        model_admin = ContributionAdmin(Contribution, django_admin.site)
        self.assertIn("finalized_at", model_admin.readonly_fields)

        def _filtered(value):
            f = UploadStateFilter(
                None, {"upload_state": [value]}, Contribution, model_admin
            )
            return list(f.queryset(None, Contribution.objects.all()))

        self.assertEqual(_filtered("open"), [open_one])
        self.assertEqual(_filtered("finalized"), [done])


def _files_on_disk(site) -> set[str]:
    """Every file stored for `site`, relative to MEDIA_ROOT."""
    from django.conf import settings

    root = os.path.join(settings.MEDIA_ROOT, "models", str(site.ID))
    found = set()
    for folder, _, names in os.walk(root):
        for name in names:
            found.add(os.path.relpath(os.path.join(folder, name), settings.MEDIA_ROOT))
    return found


def _files_in_rows(site) -> set[str]:
    images = Image.objects.filter(contribution__site=site).values_list(
        "image", flat=True
    )
    videos = SourceVideo.objects.filter(contribution__site=site).values_list(
        "video", flat=True
    )
    return {name for name in (*images, *videos) if name}


@override_settings(UPLOAD_MAX_FILES_PER_CONTRIBUTION=3)
class FileCap(_Base):
    def test_a_batch_past_the_file_cap_is_refused_and_leaves_nothing(self):
        contribution = self.open_upload("a.jpg", "b.jpg")
        response = self.batch(
            [_jpeg("c.jpg"), _jpeg("d.jpg")], contribution_id=contribution.ID
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"], "too_many_files")
        self.assertEqual(contribution.images.count(), 2)
        self.assertEqual(_files_on_disk(self.site), _files_in_rows(self.site))

    def test_up_to_the_cap_is_accepted(self):
        contribution = self.open_upload("a.jpg", "b.jpg")
        response = self.batch([_jpeg("c.jpg")], contribution_id=contribution.ID)
        self.assertEqual(response.status_code, 200)

    def test_videos_count_too(self):
        contribution = self.open_upload("a.jpg", "b.jpg", "c.jpg")
        response = self.client.post(
            reverse("upload"),
            {
                "batched": "1",
                "mesh_vid": self.site.verbose_id,
                "contribution_id": str(contribution.ID),
                "videos": [_video()],
                "video_keys": ["v1"],
            },
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(SourceVideo.objects.count(), 0)
        self.assertEqual(_files_on_disk(self.site), _files_in_rows(self.site))

    def test_a_first_batch_over_the_cap_opens_nothing(self):
        response = self.batch([_jpeg(f"{n}.jpg") for n in range(4)])
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"], "too_many_files")
        self.assertEqual(Contribution.objects.count(), 0)


class ByteCaps(_Base):
    def test_bytes_received_are_tracked(self):
        first = _jpeg("a.jpg")
        contribution = self.open_upload("a.jpg")
        contribution.refresh_from_db()
        self.assertEqual(contribution.received_bytes, first.size)

    def test_a_batch_past_the_contribution_byte_cap_is_refused(self):
        size = _jpeg().size
        with override_settings(UPLOAD_MAX_BYTES_PER_CONTRIBUTION=int(size * 2.5)):
            contribution = self.open_upload("a.jpg", "b.jpg")
            response = self.batch([_jpeg("c.jpg")], contribution_id=contribution.ID)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"], "too_large")
        contribution.refresh_from_db()
        self.assertEqual(contribution.received_bytes, size * 2)
        self.assertEqual(contribution.images.count(), 2)
        self.assertEqual(_files_on_disk(self.site), _files_in_rows(self.site))

    @override_settings(UPLOAD_MAX_BYTES_PER_BATCH=100)
    def test_a_batch_past_the_batch_byte_cap_is_refused(self):
        response = self.batch([_jpeg("a.jpg")])
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"], "too_large")
        self.assertEqual(Contribution.objects.count(), 0)

    @override_settings(UPLOAD_MAX_BYTES_PER_BATCH=100)
    def test_a_lone_video_may_exceed_the_batch_cap(self):
        big = SimpleUploadedFile("walk.mp4", b"\x00" * 4096, content_type="video/mp4")
        response = self.client.post(
            reverse("upload"),
            {
                "batched": "1",
                "mesh_vid": self.site.verbose_id,
                "videos": [big],
                "video_keys": ["v1"],
            },
        )
        self.assertEqual(response.status_code, 200, response.content)

    @override_settings(UPLOAD_MAX_BYTES_PER_BATCH=100)
    def test_a_video_with_company_may_not(self):
        big = SimpleUploadedFile("walk.mp4", b"\x00" * 4096, content_type="video/mp4")
        response = self.client.post(
            reverse("upload"),
            {
                "batched": "1",
                "mesh_vid": self.site.verbose_id,
                "videos": [big, _video("b.mp4")],
                "video_keys": ["v1", "v2"],
            },
        )
        self.assertEqual(response.status_code, 413)


@override_settings(UPLOAD_MAX_OPEN_PER_CONTRIBUTOR=2, UPLOAD_OPEN_TTL_MINUTES=120)
class OpenUploadCap(_Base):
    def test_a_third_open_upload_is_refused(self):
        self.open_upload("a.jpg")
        self.open_upload("b.jpg")
        response = self.batch([_jpeg("c.jpg")])
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"], "too_many_open")
        self.assertEqual(Contribution.objects.count(), 2)

    def test_resuming_one_by_its_keys_is_not_a_new_one(self):
        first = self.open_upload("a.jpg")
        self.open_upload("b.jpg")
        response = self.batch([_jpeg("a.jpg")])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["contribution_id"], str(first.ID))

    def test_finished_and_expired_ones_do_not_count(self):
        done = self.open_upload("a.jpg")
        self.finish(done.ID)
        stale = self.open_upload("b.jpg")
        Contribution.objects.filter(pk=stale.pk).update(
            last_batch_at=timezone.now() - timedelta(minutes=121)
        )
        self.open_upload("c.jpg")
        self.assertEqual(self.batch([_jpeg("d.jpg")]).status_code, 200)

    def test_it_is_per_contributor(self):
        self.open_upload("a.jpg")
        self.open_upload("b.jpg")
        self.sign_in(self.bob)
        self.assertEqual(self.batch([_jpeg("c.jpg")]).status_code, 200)


class Defaults(TestCase):
    def test_the_documented_defaults(self):
        from tirtha import api

        self.assertEqual(api.DEFAULT_UPLOAD_BATCH_RATE_LIMIT, 60)
        self.assertEqual(api.DEFAULT_UPLOAD_BATCH_RATE_WINDOW, 600)
        self.assertEqual(api.DEFAULT_UPLOAD_MAX_FILES_PER_CONTRIBUTION, 1000)
        self.assertEqual(api.DEFAULT_UPLOAD_MAX_BYTES_PER_CONTRIBUTION, 3 * 1024**3)
        self.assertEqual(api.DEFAULT_UPLOAD_MAX_BYTES_PER_BATCH, 300 * 1024**2)
        self.assertEqual(api.DEFAULT_UPLOAD_MAX_OPEN_PER_CONTRIBUTOR, 3)


class LateBatch(_Base):
    """A batch whose checks passed, but which reaches the insert after finish."""

    def _race(self, act):
        from tirtha import views

        real = views.prepare_upload
        fired = []

        def prepare_then_race(*args, **kwargs):
            if not fired:
                fired.append(True)
                act()
            return real(*args, **kwargs)

        return patch(
            "tirtha.views.uploads.prepare_upload", side_effect=prepare_then_race
        )

    def test_finish_between_the_check_and_the_insert(self):
        from tirtha.tasks import finalize_contribution

        contribution = self.open_upload("a.jpg")
        with self._race(lambda: finalize_contribution(contribution.ID)):
            response = self.batch([_jpeg("b.jpg")], contribution_id=contribution.ID)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "finalized")
        self.assertEqual(contribution.images.count(), 1)
        self.assertEqual(_files_on_disk(self.site), _files_in_rows(self.site))

    def test_discard_between_the_check_and_the_insert(self):
        contribution = self.open_upload("a.jpg")
        # On-commit callbacks run, so `django_cleanup` removes the discarded
        # upload's own file and only this batch's could be left behind.
        with (
            self.captureOnCommitCallbacks(execute=True),
            self._race(
                lambda: Contribution.objects.filter(pk=contribution.pk).delete()
            ),
        ):
            response = self.batch([_jpeg("b.jpg")], contribution_id=contribution.ID)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "finalized")
        self.assertEqual(Image.objects.count(), 0)
        self.assertEqual(_files_on_disk(self.site), _files_in_rows(self.site))


class OrphanFiles(_Base):
    """Whatever `store_uploaded_images` fails on, no file outlives its row."""

    def _contribution(self):
        return Contribution.objects.create(
            site=self.site, contributor=self.alice, finalized_at=None
        )

    def test_an_integrity_error_on_a_later_row_removes_every_file(self):
        from django.db import IntegrityError

        from tirtha.views import store_uploaded_images

        contribution = self._contribution()
        store_uploaded_images(
            contribution, [_jpeg("held.jpg")], compress=False, keys=["dup"]
        )
        before = _files_on_disk(self.site)
        # The first new row saves; the second collides with "dup".
        with self.assertRaises(IntegrityError):
            store_uploaded_images(
                contribution,
                [_jpeg("new.jpg"), _jpeg("again.jpg")],
                compress=False,
                keys=["fresh", "dup"],
            )
        self.assertEqual(_files_on_disk(self.site), before)

    def test_a_failed_commit_removes_every_file(self):
        from django.db import DatabaseError
        from django.db import transaction as real_transaction

        from tirtha.views import store_uploaded_images

        contribution = self._contribution()

        class _FailingCommit:
            def __init__(self):
                self.inner = real_transaction.atomic()

            def __enter__(self):
                return self.inner.__enter__()

            def __exit__(self, *exc):
                self.inner.__exit__(*exc)
                if exc[0] is None:
                    raise DatabaseError("commit failed")
                return False

        fake = type("T", (), {"atomic": staticmethod(_FailingCommit)})
        with patch("tirtha.views.uploads.transaction", fake):
            with self.assertRaises(DatabaseError):
                store_uploaded_images(
                    contribution,
                    [_jpeg("a.jpg"), _jpeg("b.jpg")],
                    compress=False,
                    keys=["a", "b"],
                    videos=[(_video(), "v")],
                )
        self.assertEqual(_files_on_disk(self.site), set())


class DiscardByKeys(_Base):
    def discard_keys(self, keys, site=None):
        return self.client.post(
            reverse("upload_discard"),
            json.dumps(
                {"mesh_vid": (site or self.site).verbose_id, "keys": list(keys)}
            ),
            content_type="application/json",
        )

    def test_a_cancel_during_the_first_batch_discards_it(self):
        contribution = self.open_upload("a.jpg", "b.jpg")
        response = self.discard_keys(["k-b_jpg"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["discarded"], 1)
        self.assertFalse(Contribution.objects.filter(pk=contribution.pk).exists())

    def test_only_the_requesters_own(self):
        contribution = self.open_upload("a.jpg")
        self.sign_in(self.bob)
        response = self.discard_keys(["k-a_jpg"])
        self.assertEqual(response.json()["discarded"], 0)
        self.assertTrue(Contribution.objects.filter(pk=contribution.pk).exists())

    def test_never_a_finalized_one(self):
        contribution = self.open_upload("a.jpg")
        self.finish(contribution.ID)
        self.assertEqual(self.discard_keys(["k-a_jpg"]).json()["discarded"], 0)
        self.assertTrue(Contribution.objects.filter(pk=contribution.pk).exists())

    def test_only_at_that_site(self):
        contribution = self.open_upload("a.jpg")
        response = self.discard_keys(["k-a_jpg"], site=self.other_site)
        self.assertEqual(response.json()["discarded"], 0)
        self.assertTrue(Contribution.objects.filter(pk=contribution.pk).exists())

    def test_keys_are_required_and_well_formed(self):
        self.assertEqual(self.discard_keys([]).status_code, 400)
        self.assertEqual(self.discard_keys(["no spaces"]).status_code, 400)

    def test_signed_out_is_refused(self):
        self.open_upload("a.jpg")
        session = self.client.session
        session["tirtha_user_info"] = None
        session.save()
        self.assertEqual(self.discard_keys(["k-a_jpg"]).status_code, 401)
        self.assertEqual(Contribution.objects.count(), 1)


class BeaconDiscard(_Base):
    """`navigator.sendBeacon` cannot set headers: its token rides in the form."""

    def setUp(self):
        super().setUp()
        from django.test import Client

        self.client = Client(enforce_csrf_checks=True)
        self.sign_in(self.alice)

    def _token(self):
        from django.conf import settings
        from django.middleware.csrf import _get_new_csrf_string

        token = _get_new_csrf_string()
        self.client.cookies[settings.CSRF_COOKIE_NAME] = token
        return token

    def _open(self):
        token = self._token()
        response = self.client.post(
            reverse("upload"),
            {
                "batched": "1",
                "mesh_vid": self.site.verbose_id,
                "images": [_jpeg("a.jpg")],
                "image_keys": ["k1"],
                "csrfmiddlewaretoken": token,
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["contribution_id"]

    def test_a_form_with_the_token_discards(self):
        contribution_id = self._open()
        response = self.client.post(
            reverse("upload_discard"),
            {"contribution_id": contribution_id, "csrfmiddlewaretoken": self._token()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Contribution.objects.filter(ID=contribution_id).exists())

    def test_by_keys_too(self):
        contribution_id = self._open()
        response = self.client.post(
            reverse("upload_discard"),
            {
                "mesh_vid": self.site.verbose_id,
                "keys": ["k1"],
                "csrfmiddlewaretoken": self._token(),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Contribution.objects.filter(ID=contribution_id).exists())

    def test_without_the_token_it_is_refused(self):
        contribution_id = self._open()
        self._token()
        response = self.client.post(
            reverse("upload_discard"), {"contribution_id": contribution_id}
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Contribution.objects.filter(ID=contribution_id).exists())


class OldCodeInserts(_Base):
    def test_a_row_inserted_without_the_new_columns_is_finalized(self):
        # What the previous release's INSERT looks like: it names none of
        # 0032's columns, so their database defaults decide.
        from django.db import connection

        table = Contribution._meta.db_table
        ident = "0" * 31 + "1"
        with connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO "{table}" ("ID", "contributed_at", "processed", '
                '"contributor_id", "site_id") VALUES (%s, %s, %s, %s, %s)',
                [ident, timezone.now(), False, self.alice.ID.hex, self.site.ID],
            )
        row = Contribution.objects.get(ID=ident)
        self.assertIsNotNone(row.finalized_at)
        self.assertEqual(row.confirmed_batches, 0)
        self.assertEqual(row.received_bytes, 0)

    def test_the_batched_path_still_opens_with_null(self):
        self.assertIsNone(self.open_upload().finalized_at)


class PublicCountsIgnoreOpenUploads(_Base):
    def test_counts_shown_publicly_exclude_open_uploads(self):
        from tirtha import api

        contribution = self.open_upload("a.jpg")
        self.assertEqual(Image.objects.filter(contribution__site=self.site).count(), 1)

        site = api.annotate_map_status(Site.objects.filter(pk=self.site.pk)).get()
        self.assertEqual(site.image_count, 0)
        stats = self.client.get(reverse("api-site-stats", args=[self.site.ID])).json()
        self.assertEqual(stats["total_images"], 0)
        self.assertEqual(stats["total_contributors"], 0)
        self.assertEqual(
            self.client.get(reverse("api-stats")).json()["total_images"], 0
        )

        self.finish(contribution.ID)
        cache.clear()
        site = api.annotate_map_status(Site.objects.filter(pk=self.site.pk)).get()
        self.assertEqual(site.image_count, 1)
        stats = self.client.get(reverse("api-site-stats", args=[self.site.ID])).json()
        self.assertEqual(stats["total_images"], 1)
        self.assertEqual(stats["total_contributors"], 1)
        self.assertEqual(
            self.client.get(reverse("api-stats")).json()["total_images"], 1
        )
