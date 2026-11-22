"""
Telling the contributor, and not telling them the wrong things.

Until now a contributor heard nothing either way: the success mail was gated off
and the failure mail went only to admins. That failure mail is admin-shaped --
`/admin/...` links, the raw exception, a log path -- so switching its recipient
would have leaked the admin surface to whoever photographed the site.
"""

from __future__ import annotations

from django.core import mail
from django.test import TestCase, override_settings

from tirtha.email_utils import send_contributor_run_failed_email


@override_settings(MAIL_CONTRIB_TOGGLE=True, DEFAULT_FROM_EMAIL="tirtha@example.org")
class WhatTheContributorIsTold(TestCase):
    def _send(self, step="run_colmap"):
        mail.outbox.clear()
        sent = send_contributor_run_failed_email(
            site_name="Fontaine Bartholdi",
            site_url="https://example.org/project/tirtha/site/abc/",
            contributor_email="someone@example.org",
            contributor_name="Someone",
            processing_step=step,
        )
        return sent, mail.outbox[0] if mail.outbox else None

    def test_it_goes_to_the_contributor_alone(self):
        sent, msg = self._send()
        self.assertTrue(sent)
        self.assertEqual(msg.to, ["someone@example.org"])

    def test_it_explains_the_stage_in_terms_of_photographs(self):
        _, msg = self._send("run_colmap")
        self.assertIn("overlap", msg.body.lower())
        self.assertIn("Fontaine Bartholdi", msg.body)

    def test_it_never_carries_the_admin_surface(self):
        """
        The point of a separate template.

        The admin mail links into /admin/ and quotes the exception; this one must
        do neither, whatever stage failed.
        """
        for step in ("run_colmap", "run_frgs", "run_postprocess", "something_new"):
            with self.subTest(step=step):
                _, msg = self._send(step)
                body = msg.body + str(msg.alternatives)
                self.assertNotIn("/admin/", body)
                self.assertNotIn("Traceback", body)
                self.assertNotIn("CalledProcessError", body)

    def test_an_unknown_stage_still_sends_something_useful(self):
        # `_FAILURE_GUIDANCE` cannot know every stage name, and a new one must
        # not produce an empty or broken message.
        sent, msg = self._send("a_stage_added_next_year")
        self.assertTrue(sent)
        self.assertIn("not able to build", msg.body)

    def test_it_says_the_images_are_kept(self):
        # The question a contributor actually has.
        _, msg = self._send()
        self.assertIn("kept", msg.body)


class WhenTheDeploymentHasNotOptedIn(TestCase):
    @override_settings(MAIL_CONTRIB_TOGGLE=False)
    def test_nothing_is_sent(self):
        mail.outbox.clear()
        sent = send_contributor_run_failed_email(
            site_name="S",
            site_url="https://example.org/",
            contributor_email="someone@example.org",
            contributor_name="Someone",
            processing_step="run_colmap",
        )
        self.assertTrue(sent, "a disabled toggle is not a failure")
        self.assertEqual(mail.outbox, [])

    @override_settings(MAIL_CONTRIB_TOGGLE=True)
    def test_no_address_is_not_a_crash(self):
        mail.outbox.clear()
        self.assertFalse(
            send_contributor_run_failed_email(
                site_name="S",
                site_url="u",
                contributor_email="",
                contributor_name="",
                processing_step="run_colmap",
            )
        )
