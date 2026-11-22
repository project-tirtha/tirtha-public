"""
Four defects found in the Fable review of 2026-09-05, and the assertions that
keep them fixed.

Each of these shipped the same day it was found, which is exactly when a
regression test is cheapest and least likely to be written.
"""

from __future__ import annotations

import io
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image as PILImage

from tirtha.models import Contribution, Contributor, Image, Site


def _png(size=(64, 64), colour=255) -> bytes:
    buf = io.BytesIO()
    PILImage.new("L", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", size, (120, 90, 40)).save(buf, format="JPEG")
    return buf.getvalue()


class SignInLandsSomewhere(TestCase):
    """
    `verifyToken` redirected by callable to a view that stopped being routed.

    The session was already written, so the visitor was signed in and then
    shown a 500 -- and every occurrence mailed ADMINS.
    """

    def test_the_index_name_reverses(self):
        self.assertEqual(reverse("index"), "/project/tirtha/")

    def test_verifyToken_does_not_reverse_the_unrouted_callable(self):
        from django.urls import NoReverseMatch

        from tirtha import views

        # The unrouted view itself is gone (review 2026-11-22 follow-ups):
        # it rendered a template that no longer exists, so nothing can be
        # pointed back at it by mistake.
        self.assertFalse(hasattr(views, "index"))
        with self.assertRaises(NoReverseMatch):
            reverse("tirtha.views.index")

        # The last statement of the view, comments excluded -- the fix is
        # explained in a comment that necessarily quotes the old call.
        source = __import__("inspect").getsource(views.verifyToken)
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        self.assertIn('redirect("index")', code)
        self.assertNotIn("redirect(index)", code)


class MasksBelongToTheirContributor(TestCase):
    """
    A mask is not decoration: `gaussian_cut` votes with it and COLMAP is handed
    it, so an all-black PNG of the right size removes that photograph from every
    future reconstruction. Attaching one to a stranger's image was possible by
    UUID always, and by filename stem whenever it was unique.
    """

    def setUp(self):
        # `views.auth.GOOGLE_LOGIN` is captured at import, so `override_settings`
        # cannot reach it -- same pattern as `test_upload_view._Base`. Without
        # this the view ignores the session and authenticates as the admin.
        patcher = patch("tirtha.views.auth.GOOGLE_LOGIN", True)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.alice = Contributor.objects.create(
            name="Alice", email="alice@example.org", active=True
        )
        self.mallory = Contributor.objects.create(
            name="Mallory", email="mallory@example.org", active=True
        )
        self.alice_contrib = Contribution.objects.create(
            site=self.site, contributor=self.alice
        )
        self.alice_image = Image(
            contribution=self.alice_contrib, original_filename="IMG_0117.jpeg"
        )
        self.alice_image.image.save(
            "IMG_0117.jpeg",
            SimpleUploadedFile("IMG_0117.jpeg", _jpeg()),
            save=True,
        )

    def _sign_in(self, contributor):
        session = self.client.session
        session["tirtha_user_info"] = {
            "email": contributor.email,
            "name": contributor.name,
        }
        session.save()

    def test_a_stranger_cannot_mask_someone_elses_image_by_stem(self):
        self._sign_in(self.mallory)
        response = self.client.post(
            reverse("upload"),
            {
                "mesh_vid": self.site.verbose_id,
                "masks": SimpleUploadedFile(
                    "IMG_0117.png", _png(), content_type="image/png"
                ),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("attached", 0), 0)
        self.alice_image.refresh_from_db()
        self.assertFalse(self.alice_image.mask)

    def test_a_stranger_cannot_mask_someone_elses_image_by_uuid(self):
        self._sign_in(self.mallory)
        response = self.client.post(
            reverse("upload"),
            {
                "mesh_vid": self.site.verbose_id,
                "masks": SimpleUploadedFile(
                    f"{self.alice_image.ID}.png", _png(), content_type="image/png"
                ),
            },
        )
        self.assertEqual(response.json().get("attached", 0), 0)
        self.alice_image.refresh_from_db()
        self.assertFalse(self.alice_image.mask)

    def test_a_contributor_can_still_mask_their_own_image(self):
        self._sign_in(self.alice)
        response = self.client.post(
            reverse("upload"),
            {
                "mesh_vid": self.site.verbose_id,
                "masks": SimpleUploadedFile(
                    "IMG_0117.png", _png(), content_type="image/png"
                ),
            },
        )
        self.assertEqual(response.json().get("attached"), 1)
        self.alice_image.refresh_from_db()
        self.assertTrue(self.alice_image.mask)


class OneCameraMeansOneGeometry(TestCase):
    """
    `exif_transpose` at ingest bakes orientation into pixels, so one phone held
    both ways stores two different sizes. COLMAP refuses `single_camera` on a
    mixed set and silently skips the minority orientation.
    """

    def setUp(self):
        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.contribution = Contribution.objects.create(
            site=self.site,
            contributor=Contributor.objects.create(
                name="T", email="t@example.org", active=True
            ),
        )

    def _image(self, name, size, make="Apple", model="iPhone 15", focal="26"):
        img = Image(
            contribution=self.contribution,
            original_filename=name,
            camera_make=make,
            camera_model=model,
            focal_length=focal,
            safety_status="safe",
        )
        img.image.save(name, SimpleUploadedFile(name, _jpeg(size)), save=True)
        return img

    def _decide(self):
        from tirtha.workers import GSOps

        ops = GSOps.__new__(GSOps)
        ops.site = self.site
        return GSOps._one_camera_for_the_site(ops)

    def test_same_camera_same_orientation_is_one_camera(self):
        self._image("a.jpg", (80, 60))
        self._image("b.jpg", (80, 60))
        self.assertTrue(self._decide())

    def test_same_camera_both_orientations_is_not_one_camera(self):
        self._image("a.jpg", (80, 60))
        self._image("b.jpg", (60, 80))
        self.assertFalse(self._decide())

    def test_empty_exif_is_still_not_one_camera(self):
        self._image("a.jpg", (80, 60), make="", model="", focal="")
        self._image("b.jpg", (80, 60), make="", model="", focal="")
        self.assertFalse(self._decide())


class AFailureIsReportedOnce(TestCase):
    """
    A stage's `_handle_error` re-raises; `_run_all` caught that and called it
    again. Two admin mails, two contributor mails, and `run.notes` overwritten
    with the frame that re-caught the error instead of the stage that failed.
    """

    def _ops(self):
        from tirtha.workers import GSOps

        ops = GSOps.__new__(GSOps)
        ops.logger = __import__("logging").getLogger("test-handle-error")
        return ops

    def test_the_second_call_neither_mails_nor_rewrites_notes(self):
        ops = self._ops()
        ops._error_handled = True
        run_before = object()
        ops.run = run_before

        with (
            patch("tirtha.email_utils.send_reconstruction_failure_email") as admin,
            patch("tirtha.email_utils.send_contributor_run_failed_email") as contrib,
        ):
            with self.assertRaises(ValueError):
                ops._handle_error(ValueError("boom"), "_run_all")

        admin.assert_not_called()
        contrib.assert_not_called()
        # `run` was never touched, so `notes` still names the real stage.
        self.assertIs(ops.run, run_before)

    def test_the_guard_starts_unset_so_the_first_call_still_reports(self):
        ops = self._ops()
        self.assertFalse(getattr(ops, "_error_handled", False))


class MalformedRationalsAreNotValues(TestCase):
    """
    Pillow 12 stopped raising on a zero denominator and returns `nan` instead,
    so the ZeroDivisionError guards became dead code and a camera with one bad
    rational was recorded as having a focal length of "nan" -- and, on the
    backfill's GPS path, a latitude of nan, which `is not None` let through.
    """

    def test_a_zero_denominator_is_no_value(self):
        from PIL.TiffImagePlugin import IFDRational

        from tirtha.exif import _rational

        self.assertIsNone(_rational(IFDRational(50, 0)))
        self.assertEqual(_rational(IFDRational(50, 2)), 25.0)

    def test_a_string_coordinate_is_refused_rather_than_unpacked(self):
        from tirtha.exif import _gps_decimal

        # "123" is iterable and unpacks into three characters; this returned
        # None by luck before, not by decision.
        self.assertIsNone(_gps_decimal("123", "S"))

    def test_a_coordinate_containing_a_bad_rational_is_no_value(self):
        from PIL.TiffImagePlugin import IFDRational

        from tirtha.exif import _gps_decimal

        self.assertIsNone(_gps_decimal((IFDRational(1, 0), 30, 0), "N"))
        self.assertAlmostEqual(_gps_decimal((12, 30, 0), "S"), -12.5)


class TheGateCountsWhatTheRunWillUse(TestCase):
    """
    `reconstructable_images` excludes `admin_verdict="reject"`; the two gates in
    front of it counted `safety_status="safe"` and stopped there. A moderator
    who rejected images saw the gate still call a site ready, the run start, and
    then materialise fewer images than promised -- or none.
    """

    def setUp(self):
        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )
        self.contribution = Contribution.objects.create(
            site=self.site,
            contributor=Contributor.objects.create(
                name="T", email="t@example.org", active=True
            ),
        )

    def _images(self, n, verdict=""):
        made = []
        for i in range(n):
            img = Image(
                contribution=self.contribution,
                original_filename=f"{i}.jpg",
                safety_status="safe",
                admin_verdict=verdict,
            )
            img.image.save(
                f"{i}.jpg", SimpleUploadedFile(f"{i}.jpg", _jpeg()), save=True
            )
            made.append(img)
        return made

    def test_prerun_check_refuses_a_site_whose_images_were_all_rejected(self):
        from tirtha.workers import prerun_check

        self._images(20, verdict="reject")
        ok, message = prerun_check(str(self.contribution.ID), "GS")
        self.assertFalse(ok)
        self.assertIn("Not enough images", message)

    def test_prerun_check_still_allows_a_site_with_accepted_images(self):
        from tirtha.workers import prerun_check

        self._images(20)
        ok, _ = prerun_check(str(self.contribution.ID), "GS")
        self.assertTrue(ok)

    def test_the_scheduler_does_not_re_enqueue_a_fully_rejected_site(self):
        from tirtha.scheduler import pending_images

        self._images(30, verdict="reject")
        self.assertEqual(pending_images(self.site).count(), 0)

    def test_the_scheduler_still_sees_accepted_images(self):
        from tirtha.scheduler import pending_images

        self._images(30)
        self.assertEqual(pending_images(self.site).count(), 30)


class TheShellRevalidates(TestCase):
    """
    The hashed assets are immutable and cached for 30 days; the shell is the
    document that names which of them to fetch. Sent with no `Cache-Control`,
    whether a reload after a deploy picked up the new bundle was left to each
    browser's heuristic guess.
    """

    def test_the_shell_is_sent_no_cache(self):
        response = self.client.get(reverse("index"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-cache", response.headers.get("Cache-Control", ""))

    def test_no_cache_does_not_mean_no_store(self):
        # The browser may still keep it and revalidate; forbidding storage
        # would cost a full download of the document on every navigation.
        response = self.client.get(reverse("index"))
        self.assertNotIn("no-store", response.headers.get("Cache-Control", ""))


class SignInRequiresAVerifiedEmail(TestCase):
    """
    `Contributor` is keyed on the email address and nothing else: it attributes
    contributions, credits the leaderboard, and receives reconstruction mail. A
    token can carry an address Google has not confirmed belongs to the holder.
    """

    def _verify(self, idinfo):
        from unittest.mock import patch

        session = self.client.session
        session["auth_random_state"] = "st4te"
        session.save()
        with (
            patch(
                "tirtha.views.google.authorize_access_token",
                return_value={"id_token": "tok"},
            ),
            patch("tirtha.views.id_token.verify_oauth2_token", return_value=idinfo),
        ):
            return self.client.get(reverse("verifyToken"), {"state": "st4te"})

    def test_an_unverified_address_is_refused_and_sets_no_session(self):
        response = self._verify(
            {"email": "someone@example.org", "name": "S", "email_verified": False}
        )
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.client.session.get("tirtha_user_info"))

    def test_a_missing_email_verified_claim_is_refused(self):
        response = self._verify({"email": "someone@example.org", "name": "S"})
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.client.session.get("tirtha_user_info"))

    def test_a_verified_address_signs_in_and_lands_on_the_app(self):
        response = self._verify(
            {"email": "someone@example.org", "name": "S", "email_verified": True}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/project/tirtha/")
        self.assertEqual(
            self.client.session["tirtha_user_info"]["email"], "someone@example.org"
        )


class TheSiteLockDoesNotSpanTheExifPass(TestCase):
    """
    `store_uploaded_images` takes a row lock on the Site to allocate `seq`.

    The EXIF pass re-opens every stored file and needs nothing that lock
    protects -- each read touches one row that already exists and that no other
    uploader can reach. Inside the lock it made every upload block every other
    write to that site's row, including the pipeline's own `Site.save()`, for a
    second full pass over the files.

    Asserted structurally rather than by timing, because a timing test for lock
    contention is flaky and this is a fact about the code.
    """

    def _tree(self):
        import ast
        from pathlib import Path

        # The submodule that defines it: the package's `__init__` only
        # re-exports, so its `__file__` holds no function body to walk.
        import tirtha.views.uploads as views_module

        source = Path(views_module.__file__).read_text()
        tree = __import__("ast").parse(source)
        return ast, next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "store_uploaded_images"
        )

    def _called_in(self, ast_mod, node):
        return {
            n.func.id
            for n in ast_mod.walk(node)
            if isinstance(n, ast_mod.Call) and isinstance(n.func, ast_mod.Name)
        }

    def test_read_exif_is_not_inside_the_atomic_block(self):
        ast_mod, fn = self._tree()
        for block in [n for n in ast_mod.walk(fn) if isinstance(n, ast_mod.With)]:
            self.assertNotIn("read_exif", self._called_in(ast_mod, block))

    def test_the_exif_pass_still_happens(self):
        ast_mod, fn = self._tree()
        top_level = [n for n in fn.body if isinstance(n, ast_mod.For)]
        self.assertTrue(
            any("read_exif" in self._called_in(ast_mod, loop) for loop in top_level),
            "the EXIF pass disappeared entirely rather than moving out of the lock",
        )

    def test_seq_allocation_is_still_locked(self):
        """The lock is not merely gone -- the race it prevents is real."""
        ast_mod, fn = self._tree()
        blocks = [n for n in ast_mod.walk(fn) if isinstance(n, ast_mod.With)]
        self.assertTrue(blocks, "the row lock was removed entirely")
        source = __import__("inspect").getsource(
            __import__("tirtha.views", fromlist=["x"]).store_uploaded_images
        )
        self.assertIn("select_for_update", source)
