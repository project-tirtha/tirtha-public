"""
Tests for `map_status` and `model_count` on the site payload (internal design notes).

The map is the page, and clustering is client-side: MapLibre collapses nearby
pins into one whose colour make-up summarises the sites underneath (18 of 60
sites sit in Khordha alone). That summary is computed in the browser from the
site list, so every site in the list has to carry its own status -- there is no
second request the map can make per pin, and no server-side cluster to ask.

Two things dominate here.

**`Site.completed` is not the input.** It means "no longer accepting images"
(`models.py`), not "a model exists". Wiring the pin colour to it would paint a
finished-looking marker on a site with no reconstruction and a "needs images"
marker on a site with three.

**The annotated and unannotated paths must agree.** The list annotates so the
query count stays constant; the detail view recomputes per object. Two code
paths for one answer is exactly where a divergence hides, so they are asserted
against each other rather than each against a hardcoded expectation.
"""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tirtha.api import _map_status, annotate_map_status
from tirtha.models import ARK, Contribution, Contributor, Image, Run, Site


def _site(name="Mukteswar Temple", **kw):
    d = {"country": "India", "state": "Odisha", "district": "Khordha"}
    d.update(kw)
    return Site.objects.create(name=name, **d)


def _ark(assigned_name):
    return ARK.objects.create(
        ark=f"99999/a{assigned_name}",
        naan="99999",
        shoulder="/a",
        assigned_name=assigned_name,
        url=f"https://example.org/{assigned_name}.sog",
        metadata={},
    )


def _run(site, *, ark=False, **kw):
    run = Run.objects.create(site=site, kind="GS", **kw)
    if ark:
        run.ark = _ark(run.ID)
        run.save()
    return run


def _status_of(site):
    """The site's `map_status` as the list endpoint computes it (annotated)."""
    annotated = annotate_map_status(Site.objects.filter(pk=site.pk)).get()
    return _map_status(annotated)[0]


class MapStatusTests(TestCase):
    def test_a_site_with_a_published_run_is_published(self):
        site = _site()
        _run(site, ark=True)
        self.assertEqual(_status_of(site), "published")

    def test_a_site_with_runs_but_none_published_is_in_progress(self):
        # The reconstruction is underway: the pin says "coming", not "help".
        site = _site()
        _run(site)
        self.assertEqual(_status_of(site), "in_progress")

    def test_a_site_with_no_runs_at_all_needs_images(self):
        self.assertEqual(_status_of(_site()), "needs_images")

    def test_a_run_without_an_ark_is_not_published(self):
        # The ARK is what binds a run to a published file -- `file_url` reads
        # `ark.url`. An Archived run with no ARK has nothing to open.
        site = _site()
        _run(site, status="Archived")
        self.assertEqual(_status_of(site), "in_progress")

    def test_a_hidden_run_does_not_publish_a_site(self):
        # Hidden is hidden at every endpoint. A site whose only model is hidden
        # must not advertise a model the visitor cannot open.
        site = _site()
        _run(site, ark=True, hidden=True)
        self.assertEqual(_status_of(site), "needs_images")

    def test_an_errored_run_does_not_publish_a_site(self):
        site = _site()
        _run(site, ark=True, status="Error")
        self.assertEqual(_status_of(site), "needs_images")

    def test_a_site_with_images_but_no_run_still_needs_images(self):
        # More images is the honest ask: nothing has been attempted yet, and
        # `Contribution` rows on their own reconstruct nothing.
        site = _site()
        contributor = Contributor.objects.create(name="T", email="t@example.org")
        contribution = Contribution.objects.create(site=site, contributor=contributor)
        Image.objects.create(contribution=contribution, image="images/x.jpg")
        self.assertEqual(_status_of(site), "needs_images")

    def test_one_published_run_beats_any_number_of_unpublished_ones(self):
        site = _site()
        _run(site)
        _run(site, status="Error")
        _run(site, ark=True)
        self.assertEqual(_status_of(site), "published")

    def test_completed_is_not_the_input(self):
        # `Site.completed` means "no longer accepting images", not "a model
        # exists". A site closed to contributions with nothing reconstructed is
        # not published, and an open site with a model is.
        closed = _site("Closed", completed=True)
        self.assertEqual(_status_of(closed), "needs_images")
        open_site = _site("Open", completed=False)
        _run(open_site, ark=True)
        self.assertEqual(_status_of(open_site), "published")


class ModelCountTests(TestCase):
    def test_published_runs_are_counted(self):
        site = _site()
        _run(site, ark=True)
        _run(site, ark=True)
        annotated = annotate_map_status(Site.objects.filter(pk=site.pk)).get()
        self.assertEqual(_map_status(annotated), ("published", 2))

    def test_unpublishable_runs_are_not_counted(self):
        # "12 sites - 8 published - 19 models" counts what a visitor can open,
        # so a hidden, errored or unminted run is not a model.
        site = _site()
        _run(site, ark=True)
        _run(site, ark=True)
        _run(site)
        _run(site, ark=True, hidden=True)
        _run(site, ark=True, status="Error")
        annotated = annotate_map_status(Site.objects.filter(pk=site.pk)).get()
        self.assertEqual(_map_status(annotated)[1], 2)

    def test_a_site_with_no_models_counts_zero_not_null(self):
        # Coalesced, because the subquery returns no row at all for a site with
        # no published runs and the frontend sums this field.
        annotated = annotate_map_status(Site.objects.filter(pk=_site().pk)).get()
        self.assertEqual(_map_status(annotated)[1], 0)

    def test_the_count_is_not_inflated_by_the_category_join(self):
        # The list joins `categories` when `?category=` is given. A count over
        # a second multi-valued join would multiply out; a subquery does not.
        from tirtha.models import Category

        site = _site()
        for slug in ("temple", "fort", "monument"):
            site.categories.add(Category.objects.create(slug=slug, name=slug))
        _run(site, ark=True)
        annotated = annotate_map_status(
            Site.objects.filter(pk=site.pk, categories__slug="temple")
        ).get()
        self.assertEqual(_map_status(annotated)[1], 1)


class AnnotationAndFallbackAgreeTests(TestCase):
    """
    The list annotates and the detail view does not. Two paths, one answer.

    Every shape of data is put through both and the results compared, rather
    than each being compared to an expected constant -- a divergence between
    them is the failure this unit is most likely to produce, and it would show
    up as a pin that changes colour when you click it.
    """

    def _both(self, site):
        annotated = annotate_map_status(Site.objects.filter(pk=site.pk)).get()
        plain = Site.objects.get(pk=site.pk)
        # Nothing else may have leaked the annotation onto the plain instance.
        self.assertFalse(hasattr(plain, "model_count"))
        return _map_status(annotated), _map_status(plain)

    def test_every_shape_agrees(self):
        shapes = {
            "no runs": lambda s: None,
            "one unpublished run": lambda s: _run(s),
            "one published run": lambda s: _run(s, ark=True),
            "only a hidden run": lambda s: _run(s, ark=True, hidden=True),
            "only an errored run": lambda s: _run(s, ark=True, status="Error"),
            "two published runs": lambda s: [_run(s, ark=True) for _ in range(2)],
            "published and errored": lambda s: (
                _run(s, ark=True),
                _run(s, status="Error"),
            ),
        }
        for label, build in shapes.items():
            with self.subTest(shape=label):
                site = _site(f"Site {label}")
                build(site)
                annotated, plain = self._both(site)
                self.assertEqual(annotated, plain)

    def test_the_detail_endpoint_reports_the_same_status_as_the_list(self):
        site = _site()
        _run(site, ark=True)
        listed = self.client.get(reverse("api-sites")).json()["results"][0]
        detail = self.client.get(
            reverse("api-site", kwargs={"site_id": site.ID})
        ).json()
        self.assertEqual(listed["map_status"], detail["map_status"])
        self.assertEqual(listed["model_count"], detail["model_count"])


class ListQueryCountTests(TestCase):
    """
    A clustered map asks for every site at once, so the cost of the list is the
    cost of the map. It must not grow per site.
    """

    def _populate(self, count):
        from tirtha.models import Category

        category, _ = Category.objects.get_or_create(
            slug="temple", defaults={"name": "Temple"}
        )
        for i in range(count):
            site = _site(f"Site {i}", latitude=20.0 + i / 100, longitude=85.0)
            site.categories.add(category)
            _run(site, ark=True)
            _run(site)

    def test_the_list_is_a_constant_number_of_queries(self):
        # Four on a cold cache: the version stamp (`api._catalogue_version`),
        # then the count, the page, and the categories prefetch. The status
        # annotations are correlated subqueries inside the page query, so they
        # add none. Each populate moves the stamp, so both requests miss.
        self._populate(1)
        with self.assertNumQueries(4):
            self.client.get(reverse("api-sites"))

        Site.objects.all().delete()
        self._populate(25)
        with self.assertNumQueries(4):
            response = self.client.get(reverse("api-sites"))
        self.assertEqual(len(response.json()["results"]), 25)

    def test_the_status_costs_no_extra_query(self):
        # A guard against the obvious regression: dropping the annotation and
        # letting `_map_status` fall back per row would still be *correct*, and
        # would quietly turn one query into sixty-one.
        self._populate(20)
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse("api-sites"))
        self.assertTrue(
            all(r["map_status"] == "published" for r in response.json()["results"])
        )
        # The version stamp plus the three above.
        self.assertLessEqual(len(ctx.captured_queries), 4)


class ListPayloadTests(TestCase):
    def test_hidden_sites_are_still_absent(self):
        _site("Visible")
        _site("TestMesh", hidden=True)
        names = [
            s["name"] for s in self.client.get(reverse("api-sites")).json()["results"]
        ]
        self.assertEqual(names, ["Visible"])

    def test_a_site_without_coordinates_is_still_listed(self):
        # The list is the accessible equivalent of the map (internal design notes) and
        # the only route to a site that has no coordinate yet. The annotation
        # must not have turned the outer query into an inner join that drops
        # them.
        _site("Unlocated")
        _site("Located", latitude=20.0, longitude=85.0)
        data = self.client.get(reverse("api-sites")).json()
        self.assertEqual(data["count"], 2)
        unlocated = [s for s in data["results"] if s["name"] == "Unlocated"][0]
        self.assertIsNone(unlocated["location"])
        self.assertEqual(unlocated["map_status"], "needs_images")

    def test_every_listed_site_carries_both_fields(self):
        _site("A")
        _run(_site("B"), ark=True)
        for row in self.client.get(reverse("api-sites")).json()["results"]:
            self.assertIn(
                row["map_status"], {"published", "in_progress", "needs_images"}
            )
            self.assertIsInstance(row["model_count"], int)

    def test_the_totals_the_frontend_shows_can_be_summed_from_the_list(self):
        # "12 sites - 8 published - 19 models" is computed in the browser from
        # this payload; no separate stats endpoint exists.
        _site("None")
        _run(_site("One"), ark=True)
        two = _site("Two")
        _run(two, ark=True)
        _run(two, ark=True)
        rows = self.client.get(reverse("api-sites")).json()["results"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(r["map_status"] == "published" for r in rows), 2)
        self.assertEqual(sum(r["model_count"] for r in rows), 3)
