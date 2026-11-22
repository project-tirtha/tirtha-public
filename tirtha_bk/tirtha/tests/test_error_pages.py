"""
The public error pages and the embed/shell markup the React bundle reads
(review 2026-11-22 §1 #10, §6).

The error pages had no doctype, `lang` or viewport, and redirected after three
seconds with an inline script -- which CSP blocks -- to a hard-coded production
URL. They are also what an `/embed/` iframe shows for a missing site.
"""

import os
import re
from pathlib import Path

from django.conf import settings
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from tirtha_bk.views import handler403, handler404, handler500

TEMPLATES = Path(settings.BASE_DIR) / "tirtha" / "templates" / "tirtha"


def _render(handler):
    request = RequestFactory().get("/project/tirtha/nowhere/")
    request.csp_nonce = "testnonce"
    return handler(request).content.decode()


class ErrorPageTests(SimpleTestCase):
    HANDLERS = {"403": handler403, "404": handler404, "500": handler500}

    def test_each_is_a_complete_html_document(self):
        for code, handler in self.HANDLERS.items():
            with self.subTest(code=code):
                html = _render(handler)
                self.assertTrue(html.lstrip().lower().startswith("<!doctype html>"))
                self.assertRegex(html, r'<html lang="[a-z-]+">')
                self.assertIn('name="viewport"', html)
                self.assertIn(f"<h1>{code}:", html)

    def test_no_script_and_no_hard_coded_production_url(self):
        for code, handler in self.HANDLERS.items():
            with self.subTest(code=code):
                html = _render(handler)
                self.assertNotIn("<script", html)
                self.assertNotIn("smlab.niser.ac.in", html)
                self.assertIn(f'href="{reverse("index")}"', html)

    def test_the_only_style_carries_the_csp_nonce(self):
        for code, handler in self.HANDLERS.items():
            with self.subTest(code=code):
                html = _render(handler)
                styles = re.findall(r"<style[^>]*>", html)
                self.assertEqual(styles, ['<style nonce="testnonce">'])


class StaticErrorPageTests(SimpleTestCase):
    """
    The copies nginx serves, written by `manage.py render_error_pages`.

    build/build.sh used to `cp` the templates verbatim, so nginx's own error
    pages linked to the literal `{% url 'index' %}`. They are rendered now.
    """

    def setUp(self):
        import tempfile

        self.out = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.out, ignore_errors=True)
        from django.core.management import call_command

        call_command("render_error_pages", str(self.out), stdout=open(os.devnull, "w"))

    def test_every_page_is_written(self):
        for code in ("403", "404", "500", "503"):
            with self.subTest(code=code):
                html = (self.out / f"{code}.html").read_text()
                self.assertTrue(html.lstrip().lower().startswith("<!doctype html>"))
                self.assertIn(f"<h1>{code}:", html)
                self.assertRegex(html, r'<html lang="[a-z]{2}(-[a-z]+)?">')

    def test_no_template_syntax_survives(self):
        for code in ("403", "404", "500", "503"):
            with self.subTest(code=code):
                html = (self.out / f"{code}.html").read_text()
                self.assertNotIn("{%", html)
                self.assertNotIn("{{", html)
                # No request, so no nonce -- and no empty attribute either.
                self.assertNotIn("nonce=", html)

    def test_the_map_link_is_the_real_url(self):
        for code in ("403", "404", "500"):
            with self.subTest(code=code):
                html = (self.out / f"{code}.html").read_text()
                self.assertIn(f'href="{reverse("index")}"', html)
                self.assertIn('href="/project/tirtha/"', html)

    def test_the_build_scripts_render_rather_than_copy(self):
        repo = Path(settings.BASE_DIR).parent
        for script in ("build/build.sh", "build/setup-beta.sh"):
            with self.subTest(script=script):
                if not (repo / script).exists():
                    self.skipTest(f"{script} is internal (not in the public tree)")
                source = (repo / script).read_text()
                self.assertIn("render_error_pages", source)
                self.assertNotRegex(source, r"cp[^\n]*templates/tirtha/\S*\.html")


class EmbedMarkupTests(TestCase):
    def test_a_missing_embed_renders_the_framable_404_page(self):
        response = self.client.get(reverse("embed-site", kwargs={"site_id": "nope"}))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'target="_top"', status_code=404)


class ShellMarkupTests(TestCase):
    def test_the_shell_always_explains_itself_without_javascript(self):
        html = self.client.get(reverse("shell")).content.decode()
        self.assertRegex(html, r"<noscript>\s*<p>")


class HowtoTests(SimpleTestCase):
    def test_every_img_has_alt_text(self):
        source = (TEMPLATES / "howto.html").read_text()
        for tag in re.findall(r"<img\b[^>]*>", source):
            with self.subTest(tag=tag[:80]):
                self.assertIn("alt=", tag)
