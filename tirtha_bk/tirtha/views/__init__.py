"""
Tirtha application views for handling site visualization, uploads, and authentication.

Split by concern; every name `tirtha.views` exposed as a single module is still
importable from here, and `urls.py` routes by `views.<name>` as before:

  common.py   configuration read from settings at import
  auth.py     Google OAuth client, `signin`, `verifyToken`, `_authenticate_user`
  uploads.py  `pre_upload_check`, `upload`, the batched upload, finish, discard
  pages.py    search, ARK resolution, legacy redirects, how-to, robots/llms

**Patch a name where it is looked up.** A test that changes a module-level name
a view reads -- `GOOGLE_LOGIN`, `prepare_upload`, `read_exif`, `transaction` --
must patch the submodule that holds the view (`tirtha.views.auth.GOOGLE_LOGIN`,
`tirtha.views.uploads.prepare_upload`); patching the re-export here changes
nothing the view sees.
"""

# ruff: noqa: F401

# Re-exported collaborators: names the single module carried at top level.
import json
import logging
import re
import uuid
from datetime import timedelta
from pathlib import Path

from authlib.integrations.django_client import OAuth
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import F, Max, Q
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import iri_to_uri
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST
from google.auth.transport import requests
from google.oauth2 import id_token
from tirtha_bk.views import handler403, handler404

from ..admin_auth import link_admin_user
from ..exif import read_exif
from ..models import (
    ARK,
    Contribution,
    Contributor,
    Image,
    Run,
    Site,
    SiteSlugHistory,
    SourceVideo,
)
from ..tasks import (
    extract_video_frames_task,
    mark_ingest_enqueued,
    notify_admin_new_contribution_task,
    post_save_contrib_imageops,
)
from ..uploads import prepare_upload
from ..utilsark import parse_ark
from ..videoops import ACCEPTED_SUFFIXES as ACCEPTED_VIDEO_SUFFIXES

# The package's own names.
from .auth import (
    _authenticate_user,
    _safe_next,
    google,
    oauth,
    signin,
    verifyToken,
)
from .common import (
    ADMIN_MAIL,
    FALLBACK_ARK_RESOLVER,
    GOOGLE_LOGIN,
    LOG_LOCATION,
    OAUTH_CONF,
    PRE_URL,
)
from .pages import (
    _resolve_site,
    howto,
    legacy_site_redirect,
    llms_txt,
    resolveARK,
    robots_txt,
    search,
)
from .uploads import (
    _CLIENT_KEY,
    _MAX_DISCARD_KEYS,
    UploadRefused,
    _admit_into_open_upload,
    _batch_body,
    _batch_error,
    _batch_target,
    _batch_throttle,
    _batch_too_large,
    _client_keys,
    _contribution_limits,
    _contribution_throttle,
    _discard_by_keys,
    _gib,
    _is_batched,
    _open_upload_ttl,
    _open_uploads_holding,
    _signed_in_contributor,
    _upload_batch,
    _upload_setting,
    attach_masks,
    pre_upload_check,
    store_uploaded_images,
    upload,
    upload_discard,
    upload_finish,
)
