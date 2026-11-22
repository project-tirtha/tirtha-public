"""
A visitor's language must not follow the worker into the next request.

`shell.shell` and `shell.document` call `translation.activate()` so a page
renders in the language its URL names, and never deactivate. Under gunicorn a
worker is a long-lived thread and `activate` sets thread-local state, so the
language one visitor chose stayed active for **every subsequent request that
worker handled**, whoever made it.

That changes query results, not just wording. `Site.name` is registered with
modeltranslation, which rewrites `name__icontains` and `order_by("name")` to the
active language's column -- and only `name_en` is ever populated. Measured
against the live demo before the fix: `/api/v1/sites/?q=Temple` returned 28,
then 25 visits to `/or/app/` by anyone at all made the same URL return **0**.

**The rest of the suite cannot see this.** Every other test that touches
language uses `translation.override`, which restores on exit -- so the leak is
invisible by construction. These use the real view, then look at what the next
request sees.
"""

from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from django.utils import translation

from tirtha.models import Site


def _odia_shell():
    """
    `/or/app/`, however this deployment's prefix spells it.

    The language-prefixed routes are generated from one list with a `<str:lang>`
    segment (urls.py), so the name is `shell-lang` with a kwarg -- not
    `shell-or`.
    """
    try:
        return reverse("shell-lang", kwargs={"lang": "or"})
    except NoReverseMatch:
        return None


class LanguageDoesNotLeakBetweenRequests(TestCase):
    def setUp(self):
        self.baseline = translation.get_language()
        self.shell_url = _odia_shell()
        if self.shell_url is None:
            self.skipTest("the React shell is not routed in this configuration")
        Site.objects.create(name="Bhaskareswar Temple")
        Site.objects.create(name="Konark Sun Temple")

    def tearDown(self):
        translation.activate(self.baseline)

    def test_the_shell_leaves_the_thread_in_english(self):
        self.client.get(self.shell_url)
        # Against the baseline, not a literal: `deactivate()` restores
        # `settings.LANGUAGE_CODE`, which is "en-us" here, and what matters is
        # that the next request is unaffected -- not the exact code.
        self.assertEqual(
            translation.get_language(),
            self.baseline,
            "the worker is still set to the last visitor's language",
        )

    def test_search_returns_the_same_answer_after_someone_visits_in_odia(self):
        """
        The finding, end to end.

        `q=` filters on `name__icontains`, which modeltranslation rewrites to
        `name_or__icontains` while Odia is active. `name_or` is NULL for every
        row, so the endpoint answered zero.
        """
        before = self.client.get(reverse("api-sites"), {"q": "Temple"}).json()["count"]
        self.assertEqual(before, 2)

        self.client.get(self.shell_url)

        after = self.client.get(reverse("api-sites"), {"q": "Temple"}).json()["count"]
        self.assertEqual(
            after, before, "an Odia visitor changed what the next visitor is shown"
        )

    def test_the_site_list_keeps_its_order(self):
        # `order_by("name")` becomes `order_by("name_or")` -- an all-NULL
        # column, so the order becomes arbitrary rather than alphabetical.
        before = [
            s["name"] for s in self.client.get(reverse("api-sites")).json()["results"]
        ]
        self.client.get(self.shell_url)
        after = [
            s["name"] for s in self.client.get(reverse("api-sites")).json()["results"]
        ]
        self.assertEqual(after, before)

    def test_a_document_page_does_not_leak_either(self):
        # `shell.document` has the same unguarded `activate` as `shell.shell`.
        try:
            url = reverse("document-terms-lang", kwargs={"lang": "or"})
        except NoReverseMatch:
            self.skipTest("document routes are not mounted in this configuration")
        self.client.get(url)
        self.assertEqual(translation.get_language(), self.baseline)

    def test_the_page_still_renders_in_the_language_it_was_asked_for(self):
        """
        The fix must not break what the activation was for.

        Deactivating *after* the response leaves the render itself untouched;
        removing the activation would have made `/or/app/` an English page.
        """
        response = self.client.get(self.shell_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'lang="or"')
