"""
The admin notification says what actually arrived.

It reported "Number of Images: 0" for the whole life of the project, and named
no site at all -- `Mesh:  ()` and `Location: , ,` -- because the template still
read `contribution.mesh.*` after migration 2.1 renamed the model to `Site`, and
Django renders a missing attribute as the empty string.

Two independent faults with the same symptom, neither of which any test would
have caught: nothing asserted the *content* of this mail.
"""

from __future__ import annotations

import io

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image as PILImage

from tirtha.email_utils import send_new_contribution_email
from tirtha.models import Contribution, Contributor, Image, Site


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (16, 16), (20, 40, 60)).save(buffer, format="JPEG")
    return buffer.getvalue()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    ADMINS=[("Admin", "admin@example.org")],
)
class TheAdminNotificationIsAccurate(TestCase):
    def setUp(self):
        mail.outbox.clear()
        self.site = Site.objects.create(
            name="Konark Sun Temple",
            country="India",
            state="Odisha",
            district="Puri",
        )
        self.contribution = Contribution.objects.create(
            site=self.site,
            contributor=Contributor.objects.create(
                name="Real Name", email="someone@example.org", active=True
            ),
        )

    def _add(self, n):
        for i in range(n):
            img = Image(contribution=self.contribution, original_filename=f"{i}.jpg")
            img.image.save(f"{i}.jpg", SimpleUploadedFile(f"{i}.jpg", _jpeg()))

    def _body(self):
        self.assertEqual(len(mail.outbox), 1)
        return mail.outbox[0].body

    def test_it_counts_the_photographs_that_are_actually_there(self):
        self._add(7)
        self.assertTrue(send_new_contribution_email(str(self.contribution.ID)))
        self.assertIn("7", self._body())
        self.assertNotIn("Number of Images: 0", self._body())

    def test_it_names_the_site(self):
        """`contribution.mesh.name` rendered empty for years."""
        self._add(2)
        send_new_contribution_email(str(self.contribution.ID))
        body = self._body()
        self.assertIn("Konark Sun Temple", body)
        self.assertIn(str(self.site.ID), body)

    def test_it_gives_the_location(self):
        self._add(1)
        send_new_contribution_email(str(self.contribution.ID))
        body = self._body()
        for part in ("Puri", "Odisha", "India"):
            self.assertIn(part, body)

    def test_the_word_mesh_is_gone_from_the_mail(self):
        """The model was renamed; the prose people read had not been."""
        self._add(1)
        send_new_contribution_email(str(self.contribution.ID))
        self.assertNotIn("Mesh", self._body())

    def test_the_subject_says_how_many(self):
        self._add(3)
        send_new_contribution_email(str(self.contribution.ID))
        self.assertIn("Konark Sun Temple", mail.outbox[0].subject)
        self.assertIn("3", mail.outbox[0].subject)

    @override_settings(CONTRIB_IGNORE_LIST=["someone@example.org"])
    def test_an_ignored_contributor_produces_no_mail(self):
        self._add(1)
        self.assertFalse(send_new_contribution_email(str(self.contribution.ID)))
        self.assertEqual(len(mail.outbox), 0)

    def test_a_missing_contribution_is_reported_not_raised(self):
        """An upload that succeeded must not fail because mail did."""
        import uuid

        self.assertFalse(send_new_contribution_email(str(uuid.uuid4())))
        self.assertEqual(len(mail.outbox), 0)


class TheSignalDoesNotSend(TestCase):
    """
    `post_save` on Contribution fires one line before the photographs are
    written, so anything sent from there can only ever report zero.
    """

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_creating_a_contribution_sends_nothing_by_itself(self):
        mail.outbox.clear()
        site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        Contribution.objects.create(
            site=site,
            contributor=Contributor.objects.create(
                name="T", email="t@example.org", active=True
            ),
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_the_view_enqueues_it_after_the_images_are_stored(self):
        """Asserted on order: the count is only right if it runs after."""
        import inspect

        from tirtha import views

        source = inspect.getsource(views.upload)
        stored = source.index("store_uploaded_images(")
        notified = source.index("notify_admin_new_contribution_task")
        self.assertLess(stored, notified)


class ItRunsOffTheRequestThread(TestCase):
    def test_the_notification_is_a_task_on_the_ingest_queue(self):
        from tirtha.tasks import notify_admin_new_contribution_task

        self.assertEqual(notify_admin_new_contribution_task.queue, "ingest")

    def test_the_view_calls_it_with_delay_not_inline(self):
        import inspect

        from tirtha import views

        source = inspect.getsource(views.upload)
        self.assertIn("notify_admin_new_contribution_task.delay", source)
