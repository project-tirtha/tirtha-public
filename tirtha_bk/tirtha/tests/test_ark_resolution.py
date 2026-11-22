"""
An ARK resolves to the model it names (internal design notes).

This is the one promise this project makes that outlives the software. Every
published run is minted an ARK, `Run` and `ARK` are `DO_NOTHING` on delete
precisely so a successful run's identifier can never be dropped, and the record
itself stores a commitment statement saying the identifier is permanent.

None of that matters if the resolver does not resolve. `resolveARK` ended with

    return redirect("indexMesh", vid=..., runid=...)

and `indexMesh` is not a URL name -- `grep -rn indexMesh` over the whole
repository returns that one call site and nothing else. The name was retired
when routing moved onto `Site.ID` (internal design notes); the surviving names are
`site`, `siteSlug` and `siteRun`, and they take `site_id`/`slug`/`runid`, not
`vid`/`runid`.

`redirect()` raises `NoReverseMatch`, which is not in the `except (ValueError,
ARK.DoesNotExist)` clause below it, so it escapes as an unhandled 500. The
failure is exactly inverted: an ARK that does *not* exist is handled correctly
and falls through to the global resolver, while every ARK that *does* exist
returns a server error. Nothing covered this view, so nothing said so.
"""

from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from tirtha.models import ARK, Run, Site


@override_settings(ROOT_URLCONF="tirtha_bk.urls")
class ARKResolutionTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="Mukteswar Temple",
            country="India",
            state="Odisha",
            district="Khordha",
            latitude=20.2451,
            longitude=85.8383,
        )
        self.run = Run.objects.create(site=self.site, kind="GS", status="Archived")
        self.ark = ARK.objects.create(
            ark="99999/fk4tirtha1",
            naan="99999",
            shoulder="/fk4",
            assigned_name="tirtha1",
            url="https://smlab.niser.ac.in/project/tirtha/",
            metadata={},
        )
        # `Run` holds the FK; `ark_obj.run` in the view is the reverse accessor.
        self.run.ark = self.ark
        self.run.save()

    def _resolve(self, ark: str):
        return self.client.get(reverse("resolveARK", kwargs={"ark": f"ark:/{ark}"}))

    def test_a_valid_ark_redirects_rather_than_erroring(self):
        """The whole point. This returned 500 for every published model."""
        response = self._resolve(self.ark.ark)
        self.assertNotEqual(
            response.status_code,
            500,
            "a valid ARK raised NoReverseMatch instead of redirecting",
        )
        self.assertIn(response.status_code, (301, 302))

    def test_it_redirects_to_the_run_it_names(self):
        response = self._resolve(self.ark.ark)
        self.assertEqual(response.status_code, 302)
        # Routed on Site.ID, which never changes -- not on verbose_id, which
        # changes whenever any of the four fields it is built from change.
        self.assertIn(self.site.ID, response["Location"])
        self.assertIn(str(self.run.ID), response["Location"])

    def test_the_target_url_name_exists(self):
        """
        Named directly, so the failure reads as "this name is gone" rather than
        as a 500 from a view three layers down.
        """
        try:
            reverse(
                "siteRun",
                kwargs={
                    "site_id": self.site.ID,
                    "slug": self.site.verbose_id,
                    "runid": str(self.run.ID),
                },
            )
        except NoReverseMatch as exc:  # pragma: no cover - the assertion is the point
            self.fail(f"resolveARK's redirect target does not exist: {exc}")

    def test_a_hidden_run_does_not_resolve(self):
        self.run.hidden = True
        self.run.save()
        response = self._resolve(self.ark.ark)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(self.site.ID, response["Location"])

    def test_a_hidden_site_does_not_resolve(self):
        self.site.hidden = True
        self.site.save()
        response = self._resolve(self.ark.ark)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(self.site.ID, response["Location"])

    def test_an_unknown_ark_falls_through_to_the_global_resolver(self):
        """This half already worked, and is what disguised the other half."""
        response = self._resolve("99999/fk4nosuchthing")
        self.assertEqual(response.status_code, 302)
        self.assertIn("n2t.net", response["Location"])
