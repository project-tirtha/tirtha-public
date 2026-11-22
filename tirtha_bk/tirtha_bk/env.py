"""
Environment-variable helpers for Tirtha settings.

Secrets come from the environment, never from a committed file. In production
(``DEBUG = False``) a missing secret is a hard failure rather than a silent
default, because the silent defaults are worse than an outage:

* A generated ``SECRET_KEY`` gives every gunicorn worker a *different* key, so a
  session signed by one worker is rejected by the next. Logins fail at random.
* An empty ``DB_PWD`` surfaces as an authentication error far from its cause.
* An empty ``GOOGLE_CLIENT_SECRET`` or ``EMAIL_HOST_PASSWORD`` leaves OAuth or
  mail quietly broken with no startup signal at all.

See internal design notes and unit 1.3.
"""

import os

from django.core.exceptions import ImproperlyConfigured

_TRUTHY = {"1", "true", "yes", "on"}


def env(name: str, default=None):
    """Return ``$name``, or ``default`` when unset or empty."""
    value = os.getenv(name)
    return default if value is None or value == "" else value


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None or value == "" else value.strip().lower() in _TRUTHY


def env_int(name: str, default: int | None = None) -> int | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"{name} must be an integer, got {value!r}."
        ) from exc


def env_float(name: str, default: float | None = None) -> float | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be a number, got {value!r}.") from exc


def env_required(name: str, *, debug: bool, default=None, hint: str = ""):
    """
    Return ``$name``.

    When ``debug`` is True the value is optional and ``default`` is returned, so
    a developer can run the site without provisioning secrets. When ``debug`` is
    False the value is mandatory and its absence raises at import time, which is
    the earliest and loudest place to notice.
    """
    value = os.getenv(name)
    if value is not None and value != "":
        return value
    if debug:
        return default
    suffix = f" {hint}" if hint else ""
    raise ImproperlyConfigured(
        f"{name} is required when DEBUG is False. Set it in the environment "
        f"(build/tirtha.env on a configured host).{suffix}"
    )
