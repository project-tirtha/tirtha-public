"""
Render the error pages to static HTML for nginx.

nginx serves 403/404/500/503 itself when Django is down or never reached
(`error_page` in `config/*.nginx.template`). The build scripts used to `cp` the
Django templates there verbatim, so the "Go to the map" link was the literal
text `{% url 'index' %}` -- a broken relative URL on exactly the pages people
land on when something has gone wrong. This renders them once, with the real
URL for this deployment's `PRE_URL`.

    python manage.py render_error_pages /path/to/errors

No request, so no CSP nonce: the `nonce` attribute is dropped rather than left
empty. Needs no database.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string

PAGES = ("403", "404", "500", "503")


def render_error_page(code: str) -> str:
    html = render_to_string(
        f"tirtha/{code}.html",
        {"LANGUAGE_CODE": getattr(settings, "LANGUAGE_CODE", "en") or "en"},
    )
    return html.replace(' nonce=""', "")


class Command(BaseCommand):
    help = "Write the 403/404/500/503 pages as static HTML for nginx."

    def add_arguments(self, parser):
        parser.add_argument("outdir", help="Directory to write the pages into.")

    def handle(self, *args, **opts):
        out = Path(opts["outdir"])
        out.mkdir(parents=True, exist_ok=True)
        for code in PAGES:
            target = out / f"{code}.html"
            target.write_text(render_error_page(code), encoding="utf-8")
            self.stdout.write(f"Wrote {target}")
