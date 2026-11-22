"""
Emergency admin access: print a one-time login URL for a staff user.

    python manage.py admin_login_link <username> [--base-url https://host]

The URL is valid for ten minutes and works once. It bypasses both Google and
`ADMIN_PASSWORD_LOGIN`, which is the point -- and why running it needs shell
access to the server as the user that can read the settings (and so the
`SECRET_KEY` that signs the token). See DEPLOY.md, "Admin access".
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from tirtha.admin_auth import LINK_MAX_AGE, make_login_link_token


class Command(BaseCommand):
    help = "Print a single-use, 10-minute admin login URL for an active staff user."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument(
            "--base-url",
            help="Scheme and host to prefix, e.g. https://smlab.niser.ac.in "
            "(default: taken from settings.BASE_URL).",
        )

    def handle(self, *args, username, base_url=None, **options):
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"No user {username!r}.")
        if not (user.is_active and user.is_staff):
            raise CommandError(f"{username!r} is not an active staff user.")

        backend = settings.CACHES.get("default", {}).get("BACKEND", "")
        if backend.endswith("LocMemCache"):
            self.stderr.write(
                "Warning: CACHES is a per-process LocMemCache, so single use is "
                "enforced only per worker (the link is still invalidated by the "
                "login it performs)."
            )

        parts = urlsplit(base_url or getattr(settings, "BASE_URL", "") or "")
        origin = f"{parts.scheme}://{parts.netloc}" if parts.netloc else ""
        path = reverse("admin-login-link", args=[make_login_link_token(user)])
        self.stdout.write(origin + path)
        self.stderr.write(
            f"Valid for {LINK_MAX_AGE // 60} minutes, once, for {username!r}."
        )
