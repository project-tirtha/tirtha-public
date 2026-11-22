"""
Tests for site requests (internal design notes).

This replaces a live Google Form, so the tests are mostly about *not losing*
what that form already collects. Affiliation, district, source link, access
restriction, purview and two separate consents were real questions answered by
real requesters; dropping any would be a regression dressed as a migration.
"""

import json

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from tirtha.models import Contributor, SiteSubmissionRequest


def _payload(**overrides):
    base = {
        "name": "Some Temple",
        "description": "A 12th-century temple.",
        "country": "India",
        "state": "Odisha",
        "district": "Puri",
        "latitude": 19.8876,
        "longitude": 86.0945,
        "affiliation": "NISER",
        "source_url": "https://en.wikipedia.org/wiki/Some_Temple",
        "purview": "asi",
        "restricted_access": False,
        "accepted_terms": True,
        "accepted_privacy": True,
    }
    base.update(overrides)
    return base


class _Base(TestCase):
    """
    The endpoint is throttled per IP (internal design notes), and the counter lives in
    the process-wide cache, which Django does not reset between tests. Clearing
    it here keeps each test inside its own window instead of the sixth test in
    a class inheriting the first five submissions.
    """

    def setUp(self):
        cache.clear()

    def _post(self, payload, **extra):
        return self.client.post(
            reverse("api-site-request"),
            data=json.dumps(payload),
            content_type="application/json",
            **extra,
        )


class SubmissionTests(_Base):
    def test_a_complete_request_is_accepted(self):
        response = self._post(_payload())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(SiteSubmissionRequest.objects.count(), 1)

    def test_every_google_form_field_is_retained(self):
        self._post(_payload())
        row = SiteSubmissionRequest.objects.get()
        self.assertEqual(row.affiliation, "NISER")
        self.assertEqual(row.district, "Puri")
        self.assertIn("wikipedia", row.source_url)
        self.assertEqual(row.purview, "asi")
        self.assertIs(row.restricted_access, False)
        self.assertTrue(row.accepted_terms)
        self.assertTrue(row.accepted_privacy)

    def test_a_request_starts_pending(self):
        self._post(_payload())
        self.assertEqual(SiteSubmissionRequest.objects.get().status, "pending")

    def test_the_pin_is_recorded(self):
        self._post(_payload())
        row = SiteSubmissionRequest.objects.get()
        self.assertTrue(row.has_pin)
        self.assertAlmostEqual(row.latitude, 19.8876)

    def test_anonymous_requests_are_allowed(self):
        # Requiring an account to *ask* for a site would suppress exactly the
        # local knowledge this is trying to reach.
        self._post(_payload())
        self.assertIsNone(SiteSubmissionRequest.objects.get().requester)

    def test_a_signed_in_requester_is_attributed(self):
        contributor = Contributor.objects.create(name="T", email="t@example.org")
        session = self.client.session
        session["tirtha_user_info"] = {"email": contributor.email}
        session.save()
        self._post(_payload())
        self.assertEqual(SiteSubmissionRequest.objects.get().requester, contributor)


class ConsentTests(_Base):
    def test_both_consents_are_required(self):
        for missing in ("accepted_terms", "accepted_privacy"):
            with self.subTest(missing=missing):
                response = self._post(_payload(**{missing: False}))
                self.assertEqual(response.status_code, 400)
        self.assertEqual(SiteSubmissionRequest.objects.count(), 0)

    def test_the_database_refuses_a_request_without_consent(self):
        # Belt and braces: a consent recorded as False is not consent, and such
        # a row must not exist to be approved by mistake.
        with self.assertRaises(IntegrityError), transaction.atomic():
            SiteSubmissionRequest.objects.create(
                name="X", accepted_terms=True, accepted_privacy=False
            )


class SafetyTests(_Base):
    def test_an_unanswered_access_question_is_not_recorded_as_no(self):
        # Sending someone to photograph a restricted site is a real risk, so
        # "not answered" must stay distinguishable from "open".
        self._post(_payload(restricted_access=None))
        self.assertIsNone(SiteSubmissionRequest.objects.get().restricted_access)

    def test_a_restricted_area_is_recorded_as_such(self):
        self._post(_payload(restricted_access=True))
        self.assertTrue(SiteSubmissionRequest.objects.get().restricted_access)

    def test_the_admin_distinguishes_all_three_access_states(self):
        from django.contrib.admin.sites import site as admin_site

        admin = admin_site._registry[SiteSubmissionRequest]
        rows = {
            None: "not answered",
            True: "RESTRICTED",
            False: "open",
        }
        for value, expected in rows.items():
            with self.subTest(value=value):
                row = SiteSubmissionRequest(
                    name="X",
                    restricted_access=value,
                    accepted_terms=True,
                    accepted_privacy=True,
                )
                self.assertIn(expected, admin.access(row))


class ValidationTests(_Base):
    def test_a_name_is_required(self):
        self.assertEqual(self._post(_payload(name="  ")).status_code, 400)

    def test_out_of_range_coordinates_are_refused(self):
        self.assertEqual(self._post(_payload(latitude=120)).status_code, 400)
        self.assertEqual(self._post(_payload(longitude=-500)).status_code, 400)

    def test_non_numeric_coordinates_are_refused(self):
        self.assertEqual(self._post(_payload(latitude="here")).status_code, 400)

    def test_a_request_without_a_pin_is_still_accepted(self):
        # Not everyone can drop a pin accurately; the review step resolves it.
        response = self._post(_payload(latitude=None, longitude=None))
        self.assertEqual(response.status_code, 201)
        self.assertFalse(SiteSubmissionRequest.objects.get().has_pin)

    def test_a_malformed_body_is_a_400(self):
        response = self.client.post(
            reverse("api-site-request"),
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_a_json_body_that_is_not_an_object_is_a_400(self):
        # `not json` is the handled case; `[]` is not. Each of these parses
        # fine and then meets `payload.get(...)`, which on an endpoint that is
        # deliberately anonymous turns an AttributeError into a 500 and a
        # stack-trace email to ADMINS for every request that sends one.
        for body in ("[]", '"hello"', "5", "null"):
            with self.subTest(body=body):
                response = self.client.post(
                    reverse("api-site-request"),
                    data=body,
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", json.loads(response.content))

    def test_a_field_that_is_not_a_string_is_refused_rather_than_crashing(self):
        # A buggy client sending `{"name": {...}}` used to reach `.strip()` on
        # a dict. Unanswered is the right reading of it; for the name that is
        # the 400 the client can act on.
        response = self._post(_payload(name={"en": "Some Temple"}))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SiteSubmissionRequest.objects.exists())

    def test_a_non_string_optional_field_is_recorded_as_unanswered(self):
        response = self._post(_payload(description=5, requester_name=["a"]))
        self.assertEqual(response.status_code, 201)
        submission = SiteSubmissionRequest.objects.get()
        self.assertEqual(submission.description, "")
        self.assertEqual(submission.requester_name, "")

    def test_the_endpoint_rejects_get(self):
        self.assertEqual(self.client.get(reverse("api-site-request")).status_code, 405)


class ThrottleTests(_Base):
    """
    Per-IP rate limiting on an endpoint that is deliberately anonymous.

    Anonymous submission is argued for -- requiring an account to *ask* for a
    site would suppress the local knowledge this exists to reach -- so the
    protection has to be somewhere other than authentication. Without it, the
    only cost of writing a row is one HTTP request, and the review step is a
    human reading them: a flood is an attack on a person, not just a table.
    """

    def _submit(self, **extra):
        return self._post(_payload(), **extra)

    @override_settings(SITE_REQUEST_RATE_LIMIT=3, SITE_REQUEST_RATE_WINDOW=3600)
    def test_submissions_are_accepted_up_to_the_limit_then_refused(self):
        for _ in range(3):
            self.assertEqual(self._submit().status_code, 201)
        self.assertEqual(self._submit().status_code, 429)
        # And nothing was written for the refused one.
        self.assertEqual(SiteSubmissionRequest.objects.count(), 3)

    @override_settings(SITE_REQUEST_RATE_LIMIT=1)
    def test_the_refusal_is_json_in_the_same_shape_as_every_other_error(self):
        self._submit()
        response = self._submit()
        self.assertEqual(response["Content-Type"], "application/json")
        body = json.loads(response.content)
        self.assertIn("error", body)
        self.assertIn("retry_after", body)
        # Retry-After tells a client when to come back rather than making it
        # guess by retrying.
        self.assertIn("Retry-After", response.headers)

    @override_settings(SITE_REQUEST_RATE_LIMIT=1)
    def test_reads_are_unaffected(self):
        # The throttle protects one write endpoint. Locking a visitor out of
        # the map because someone at their address submitted twice would be a
        # far worse outcome than the flood.
        self._submit()
        self.assertEqual(self._submit().status_code, 429)
        self.assertEqual(self.client.get(reverse("api-sites")).status_code, 200)
        self.assertEqual(self.client.get(reverse("api-categories")).status_code, 200)

    @override_settings(SITE_REQUEST_RATE_LIMIT=1)
    def test_invalid_submissions_are_counted_too(self):
        # A 400 that costs nothing is still a way to keep the endpoint busy, so
        # the throttle runs before validation.
        self.assertEqual(self._post(_payload(name="")).status_code, 400)
        self.assertEqual(self._submit().status_code, 429)

    @override_settings(SITE_REQUEST_RATE_LIMIT=1, TRUSTED_PROXY_DEPTH=1)
    def test_a_second_address_has_its_own_budget(self):
        # nginx appends the peer, so the rightmost entry is the real client.
        first = {"headers": {"x-forwarded-for": "203.0.113.7"}}
        second = {"headers": {"x-forwarded-for": "198.51.100.9"}}
        self.assertEqual(self._submit(**first).status_code, 201)
        self.assertEqual(self._submit(**first).status_code, 429)
        self.assertEqual(self._submit(**second).status_code, 201)

    @override_settings(SITE_REQUEST_RATE_LIMIT=1, TRUSTED_PROXY_DEPTH=1)
    def test_a_forged_forwarded_for_does_not_buy_a_fresh_bucket(self):
        # This is the whole reason the *rightmost* entry is taken. A submitter
        # who prepends an address of their choosing has it appear to the left
        # of the one nginx wrote, and must still land in their own bucket --
        # otherwise the limit is one header away from being no limit at all.
        real = "203.0.113.7"
        self.assertEqual(
            self._submit(headers={"x-forwarded-for": real}).status_code, 201
        )
        for forged in ("1.2.3.4", "8.8.8.8", "203.0.113.99"):
            with self.subTest(forged=forged):
                response = self._submit(
                    headers={"x-forwarded-for": f"{forged}, {real}"}
                )
                self.assertEqual(response.status_code, 429)

    @override_settings(SITE_REQUEST_RATE_LIMIT=1, TRUSTED_PROXY_DEPTH=0)
    def test_with_no_proxy_configured_the_header_is_ignored_entirely(self):
        # A deployment with nothing in front of Django must not believe a
        # header anyone can send. Both submissions share REMOTE_ADDR.
        self.assertEqual(
            self._submit(headers={"x-forwarded-for": "1.2.3.4"}).status_code, 201
        )
        self.assertEqual(
            self._submit(headers={"x-forwarded-for": "5.6.7.8"}).status_code, 429
        )

    @override_settings(SITE_REQUEST_RATE_LIMIT=1, TRUSTED_PROXY_DEPTH=2)
    def test_a_chain_shorter_than_the_trusted_depth_is_discarded(self):
        # Two trusted hops configured but only one entry present means the
        # chain cannot contain our own hops -- half-trusting it would let a
        # bare forged header through.
        self.assertEqual(
            self._submit(headers={"x-forwarded-for": "1.2.3.4"}).status_code, 201
        )
        self.assertEqual(
            self._submit(headers={"x-forwarded-for": "5.6.7.8"}).status_code, 429
        )

    @override_settings(SITE_REQUEST_RATE_LIMIT=0)
    def test_the_limit_is_configurable_and_can_be_switched_off(self):
        # The setting is read per call, not bound at import: `views.py` caches
        # settings in module-level names and `override_settings` cannot reach
        # those (internal design notes). This test fails if the same is done here.
        for _ in range(8):
            self.assertEqual(self._submit().status_code, 201)

    @override_settings(SITE_REQUEST_RATE_LIMIT=2)
    def test_the_limit_setting_is_the_limit(self):
        self.assertEqual(self._submit().status_code, 201)
        self.assertEqual(self._submit().status_code, 201)
        self.assertEqual(self._submit().status_code, 429)
