"""
The operator UI: the `tirtha` app's Django admin.

This was one 3,100-line `admin.py`. It is now a package, one module per model
area, and importing the package registers every `ModelAdmin` (Django's
autodiscover imports `tirtha.admin`):

    sites.py          Site
    contributors.py   Contributor
    contributions.py  Contribution, and the Contribution inlines
    images.py         Image, and the Image inline
    runs.py           Run and RunStage, and their inlines
    arks.py           ARK
    site_requests.py  SiteSubmissionRequest
    downloads.py      the ZIP downloads (`zip_response`) and the files they carry
    permissions.py    `_may`, the per-verb permission check
    common.py         thumbnails, counts and labels shared by the above

Every name `tirtha.admin` exposed as a single module is re-exported below, so
`from tirtha.admin import X` keeps working, as do the tests' patch targets
such as `tirtha.admin.os.replace` and `tirtha.admin.recon_runner_task.delay`
(both patch an attribute of a shared object, not a module global).
"""

# Re-exported: the names the single-module `admin.py` imported.
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone as dj_timezone
from django.utils.html import format_html, format_html_join, mark_safe
from django.utils.translation import ngettext

from ..models import (
    ARK,
    Contribution,
    Contributor,
    Image,
    Run,
    RunStage,
    Site,
    SiteSubmissionRequest,
    derivative_path,
)
from ..overpass import OverpassClient, OverpassUnavailable
from ..tasks import (
    mark_ingest_enqueued,
    post_save_contrib_imageops,
    recon_runner_task,
)
from ..turntable import TURNTABLE_SUFFIXES, find_turntable, turntable_path

# The admin itself. Importing these modules is what registers the ModelAdmins.
from .arks import ARKAdmin
from .common import (
    ADMIN_LOG_LOCATION,
    LIST_THUMB,
    _count,
    _file_url,
    _image_disposition,
    _site_thumb_url,
    _thumb,
)
from .contributions import (
    ContributionAdmin,
    ContributionInlineContributor,
    ContributionInlineMesh,
    ContributionsInline,
    UploadStateFilter,
)
from .contributors import ContributorAdmin
from .downloads import (
    ZIP_CHUNK,
    _image_files,
    _iter_zip,
    _models_root,
    _published_files,
    _run_dir,
    _sanitize,
    _walk,
    _ZipSink,
    zip_response,
)
from .images import (
    HasMaskFilter,
    ImageAdmin,
    ImageAdminForm,
    ImageInlineContribution,
    _moderate,
)
from .permissions import _may
from .runs import (
    ContributorInlineRun,
    ImageInlineRun,
    RunAdmin,
    RunInlineMesh,
    RunReplaceForm,
    RunStageAdmin,
    RunStageInlineContribution,
    RunStageInlineRun,
    _StageInline,
)
from .site_requests import SiteSubmissionRequestAdmin
from .sites import SiteAdmin, SiteAdminForm

__all__ = [
    # stdlib and Django, as imported by the old module
    "json",
    "logging",
    "os",
    "re",
    "shutil",
    "tempfile",
    "zipfile",
    "datetime",
    "timezone",
    "Path",
    "forms",
    "settings",
    "admin",
    "messages",
    "PermissionDenied",
    "ValidationError",
    "ContentFile",
    "default_storage",
    "UploadedFile",
    "Count",
    "IntegerField",
    "OuterRef",
    "Q",
    "Subquery",
    "Coalesce",
    "Http404",
    "HttpResponse",
    "StreamingHttpResponse",
    "redirect",
    "render",
    "reverse",
    "dj_timezone",
    "format_html",
    "format_html_join",
    "mark_safe",
    "ngettext",
    # tirtha
    "ARK",
    "Contribution",
    "Contributor",
    "Image",
    "Run",
    "RunStage",
    "Site",
    "SiteSubmissionRequest",
    "derivative_path",
    "OverpassClient",
    "OverpassUnavailable",
    "mark_ingest_enqueued",
    "post_save_contrib_imageops",
    "recon_runner_task",
    "TURNTABLE_SUFFIXES",
    "find_turntable",
    "turntable_path",
    # common
    "ADMIN_LOG_LOCATION",
    "LIST_THUMB",
    "_count",
    "_file_url",
    "_image_disposition",
    "_site_thumb_url",
    "_thumb",
    # downloads
    "ZIP_CHUNK",
    "_ZipSink",
    "_image_files",
    "_iter_zip",
    "_models_root",
    "_published_files",
    "_run_dir",
    "_sanitize",
    "_walk",
    "zip_response",
    # permissions
    "_may",
    # admins, forms, filters and inlines
    "ARKAdmin",
    "ContributionAdmin",
    "ContributionInlineContributor",
    "ContributionInlineMesh",
    "ContributionsInline",
    "UploadStateFilter",
    "ContributorAdmin",
    "HasMaskFilter",
    "ImageAdmin",
    "ImageAdminForm",
    "ImageInlineContribution",
    "_moderate",
    "ContributorInlineRun",
    "ImageInlineRun",
    "RunAdmin",
    "RunInlineMesh",
    "RunReplaceForm",
    "RunStageAdmin",
    "RunStageInlineContribution",
    "RunStageInlineRun",
    "_StageInline",
    "SiteSubmissionRequestAdmin",
    "SiteAdmin",
    "SiteAdminForm",
]
