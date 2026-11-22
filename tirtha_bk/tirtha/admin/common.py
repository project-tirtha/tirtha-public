"""Helpers shared by the admin modules: thumbnails, counts, labels."""

from django.conf import settings
from django.core.files.storage import default_storage
from django.db.models import Count, IntegerField, Subquery
from django.db.models.functions import Coalesce
from django.utils.html import format_html

# Local imports
from ..models import (
    derivative_path,
)

# Logger setup
# Logging
ADMIN_LOG_LOCATION = settings.ADMIN_LOG_LOCATION

# Logging is configured once, in settings (`LOGGING`). `basicConfig` is a
# no-op after the first caller wins, so these two calls were a race that
# `admin.py` always won -- and `ADMIN_LOG_LOCATION` was whatever that race decided.


def _image_disposition(img) -> str:
    """
    A short folder-name label for an image, derived from evidence.

    Replaces the old `label` field, which stored a verdict divorced from the
    evidence beside it -- 1,853 production images carried a FAIL remark while
    labelled "good" (internal design notes).
    """
    if img.admin_verdict:
        return img.admin_verdict
    if img.safety_status in ("rejected", "flagged"):
        return img.safety_status
    if img.safety_status == "safe":
        return "safe"
    return "unchecked"


def _count(queryset, group_by: str):
    """
    `COUNT(*)` of `queryset` per `group_by`, as a correlated scalar subquery.

    Used for the changelist counts instead of `Count()` over a join. Two
    `Count`s over two multi-valued joins multiply each other's rows, and
    `distinct=True` only hides that -- the database still builds the product.
    The caller filters `queryset` on `OuterRef("pk")`.
    """
    return Coalesce(
        Subquery(
            queryset.order_by().values(group_by).annotate(n=Count("pk")).values("n"),
            output_field=IntegerField(),
        ),
        0,
    )


# The changelist thumbnail. Small and fixed so the browser can lay the page out
# before any image arrives, and lazy so only the rows on screen are fetched.
LIST_THUMB = 96


def _thumb(url, alt="", size=LIST_THUMB, background=""):
    """
    An `<img>` for a list or inline row.

    There is no reduced copy of a contributed photograph, so a list of them is
    a list of originals; what this changes is how many are fetched and when.
    `ImageAdmin` used to render 200 per page at 400x400 with no `loading`
    hint, which is 200 full-resolution downloads and decodes before the page
    settles. Explicit `width`/`height` reserve the box (no layout shift), and
    `object-fit` crops rather than squashes.
    """
    if not url:
        return "--"
    style = "object-fit:cover;border-radius:3px"
    if background:
        style += f";background:{background}"
    return format_html(
        '<img src="{}" alt="{}" width="{}" height="{}" loading="lazy" '
        'decoding="async" style="{}">',
        url,
        alt,
        size,
        size,
        style,
    )


def _file_url(field):
    try:
        return field.url if field else ""
    except ValueError:
        return ""


def _site_thumb_url(site):
    """The 160px `preview` derivative when it is on disk, else the thumbnail."""
    if not site.thumbnail:
        return ""
    name = derivative_path(site.thumbnail.name, "preview")
    try:
        if default_storage.exists(name):
            return default_storage.url(name)
    except (NotImplementedError, OSError, ValueError):
        pass
    return _file_url(site.thumbnail)
