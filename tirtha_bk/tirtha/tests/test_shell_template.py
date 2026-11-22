"""
Guards on the server-rendered React shell (internal design notes).
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import resolve, reverse

TEMPLATES = Path(settings.BASE_DIR) / "tirtha" / "templates" / "tirtha"


class CommentSyntaxTests(TestCase):
    def test_no_template_comment_spans_lines(self):
        """
        `{# ... #}` is single-line only.

        Django does not strip a `{#` whose `#}` is on a later line -- it renders
        the whole thing as text. Two of them shipped to the demo and appeared as
        a paragraph of source commentary above the page heading. Multi-line
        commentary belongs in `{% comment %}`.
        """
        offenders = []
        for path in TEMPLATES.rglob("*.html"):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if "{#" in line and "#}" not in line.split("{#", 1)[1]:
                    offenders.append(f"{path.name}:{number}")
        self.assertEqual(offenders, [], f"multi-line {{# #}} comments: {offenders}")

    def test_shell_renders_no_commentary_into_the_body(self):
        rendered = self.client.get(reverse("shell")).content.decode()
        # The words that leaked. Their absence is the actual guarantee -- the
        # syntax check above is the reason, this is the symptom.
        for phrase in ("Django owns the URL prefix", "A crawler must know"):
            self.assertNotIn(phrase, rendered)
        self.assertNotIn("{#", rendered)


class MetaTests(TestCase):
    def test_the_prefix_is_published_for_the_bundle(self):
        rendered = self.client.get(reverse("shell")).content.decode()
        self.assertIn('name="tirtha-prefix"', rendered)
        self.assertRegex(rendered, r'<meta name="tirtha-prefix" content="[^"]*/"')

    def test_unfurl_tags_are_server_rendered(self):
        # The whole reason the shell is a Django template: crawlers do not run
        # JavaScript, so these must be in the HTML as delivered (12.1).
        rendered = self.client.get(reverse("shell")).content.decode()
        for tag in ('property="og:title"', 'property="og:url"', 'name="twitter:card"'):
            self.assertIn(tag, rendered)
        self.assertNotIn("{{", re.sub(r"<script.*?</script>", "", rendered, flags=re.S))


class ShellCatchallTests(TestCase):
    """
    An unmatched path under the deployment renders the shell, not Django's 404.

    Reported from the demo: `/app/site` returned Django's 404 page. It was
    reached by clicking a link labelled "Back to the map" that walked one URL
    segment at a time, but the routing half is worth fixing on its own -- a
    truncated share, a stale bookmark or a hand-typed URL all land here, and
    Django's error page makes a working deployment look broken.

    The client router renders its own not-found page once the shell loads.
    """

    def test_an_unmatched_app_path_serves_the_shell(self):
        response = self.client.get(f"/{settings.PRE_URL}site")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="tirtha-prefix"')

    def test_a_deep_unmatched_path_serves_the_shell(self):
        response = self.client.get(f"/{settings.PRE_URL}nonsense/deeper/still")
        self.assertEqual(response.status_code, 200)

    def test_the_specific_routes_still_win(self):
        """The catch-all is last, so per-route OG tags survive (internal design notes)."""
        self.assertEqual(resolve(f"/{settings.PRE_URL}").url_name, "shell")

    def test_it_does_not_swallow_the_api_or_embeds(self):
        for path in ("api/v1/sites/", "embed/abc/"):
            with self.subTest(path=path):
                self.assertNotEqual(
                    resolve(f"/{settings.PRE_URL}{path}").url_name, "shell-catchall"
                )
