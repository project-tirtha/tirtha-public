"""
Read-only JSON API under `/api/v1/` (internal design notes).

**Plain Django, not DRF.** The surface is a handful of read endpoints over ~60
sites; DRF's serializers, viewsets and routers would add a large dependency and
a layer of indirection for that. Revisit when the write endpoints arrive (6.5
uploads, 6.7 accounts) -- adding DRF later is easy, and removing it would not
be.

**Versioned from the first commit.** `/api/v1/` costs nothing now and is the
only way a breaking change can later ship without coordinating a frontend
deploy to the same second.

Two rules hold everywhere in this module:

- **Hidden is hidden.** Sites and runs marked hidden never appear, at any
  endpoint. Production hides test rows and unpublished work behind that flag
  (§8), so a leak here publishes them.
- **No contributor email, ever.** Names come from `Contributor.public_name`,
  which returns "Anonymous contributor" unless the person opted in (§11.2).

**Layout.** This was one `api.py`; it is now a package grouped by endpoint
area, and every name it defined is re-exported here, so `tirtha.api.X` (what
`urls.py`, `views.py`, `embed.py` and the tests use) still resolves:

- `common`         -- visibility scopes, pagination and rate-limit defaults,
  `_rate_limited`, the ETag helpers (`_conditional`), `_not_found`.
- `catalogue`      -- `sites` (and its catalogue cache), `site_detail`,
  `categories`, `document`, `serialize_site`, `_map_status`.
- `runs`           -- `run_detail`, `serialize_run`, `_artifact` / `_sibling`.
- `accounts`       -- `session`, `sign_out`, `leaderboard`, `profile`.
- `platform_stats` -- `stats`, `site_stats`.
- `site_requests`  -- `request_site`.
- `discovery`      -- `discover` and the Overpass proxy helpers.

The modules for sites and stats are not called `sites` and `stats` because
those are the names of view functions exported from here; a submodule of the
same name would be shadowed by the function, and a `mock.patch` aimed at the
module would land on the function instead.

**Patch where a name is used.** A function moved to a submodule reads its
globals from that submodule, so a test that patches e.g. `cache` must target
`tirtha.api.catalogue.cache`, not `tirtha.api.cache`.
"""

from __future__ import annotations

# The names `api.py` imported, still reachable as `tirtha.api.X`. Nothing in
# this package reads them from here; see "Patch where a name is used" above.
import hashlib  # noqa: F401
import json  # noqa: F401
import logging  # noqa: F401
import math  # noqa: F401
import re  # noqa: F401
from functools import lru_cache  # noqa: F401
from pathlib import Path  # noqa: F401

import bleach  # noqa: F401
from django.conf import settings  # noqa: F401
from django.core.cache import cache  # noqa: F401
from django.core.files.storage import default_storage  # noqa: F401
from django.db import connection  # noqa: F401
from django.db.models import (  # noqa: F401
    Count,
    Exists,
    IntegerField,
    OuterRef,
    Subquery,
)
from django.db.models.functions import Coalesce  # noqa: F401
from django.http import HttpResponse, JsonResponse  # noqa: F401
from django.utils import translation  # noqa: F401
from django.utils.cache import (  # noqa: F401
    add_never_cache_headers,
    get_conditional_response,
)
from django.views.decorators.http import require_GET, require_POST  # noqa: F401

from tirtha.models import (  # noqa: F401
    ARK,
    THUMBNAIL_DERIVATIVES,
    Category,
    Contribution,
    Contributor,
    Image,
    Run,
    Site,
    SiteSubmissionRequest,
    derivative_path,
)
from tirtha.overpass import (  # noqa: F401
    DISCOVERY_KIND_RANK,
    DISCOVERY_MAX_RESULTS,
    OverpassClient,
    OverpassUnavailable,
    discovery_kind,
)
from tirtha.turntable import find_turntable  # noqa: F401

from .accounts import leaderboard, profile, session, sign_out  # noqa: F401
from .catalogue import (  # noqa: F401
    _PLACEHOLDER_DESCRIPTION,
    _SITES_CACHE_PREFIX,
    DEFAULT_API_SITES_CACHE_SECONDS,
    DESCRIPTION_ATTRS,
    DESCRIPTION_PROTOCOLS,
    DESCRIPTION_TAGS,
    _catalogue_version,
    _derivative_urls,
    _description_html,
    _is_placeholder,
    _map_status,
    _public_description,
    _sanitise_description,
    _sites_cache_key,
    _sites_cache_seconds,
    _sites_response,
    annotate_map_status,
    categories,
    document,
    serialize_category,
    serialize_site,
    site_detail,
    sites,
)
from .common import (  # noqa: F401
    API_VERSION,
    DEFAULT_PAGE_SIZE,
    DEFAULT_SITE_REQUEST_RATE_LIMIT,
    DEFAULT_SITE_REQUEST_RATE_WINDOW,
    DEFAULT_TRUSTED_PROXY_DEPTH,
    DEFAULT_UPLOAD_BATCH_RATE_LIMIT,
    DEFAULT_UPLOAD_BATCH_RATE_WINDOW,
    DEFAULT_UPLOAD_MAX_BYTES_PER_BATCH,
    DEFAULT_UPLOAD_MAX_BYTES_PER_CONTRIBUTION,
    DEFAULT_UPLOAD_MAX_FILES_PER_CONTRIBUTION,
    DEFAULT_UPLOAD_MAX_OPEN_PER_CONTRIBUTOR,
    DEFAULT_UPLOAD_RATE_LIMIT,
    DEFAULT_UPLOAD_RATE_WINDOW,
    MAX_PAGE_SIZE,
    _absolute,
    _client_ip,
    _conditional,
    _conditional_bytes,
    _etag,
    _not_found,
    _published_runs,
    _rate_limited,
    _visible_runs,
    _visible_sites,
)
from .discovery import (  # noqa: F401
    DEFAULT_DISCOVER_CACHE_SECONDS,
    DEFAULT_DISCOVER_MAX_BBOX_SPAN,
    DEFAULT_DISCOVER_MIN_ZOOM,
    DEFAULT_DISCOVER_RATE_LIMIT,
    DEFAULT_DISCOVER_RATE_WINDOW,
    DEFAULT_OVERPASS_TIMEOUT,
    DISCOVER_BBOX_GRID,
    OSM_ATTRIBUTION,
    _bbox_cell,
    _cell_bbox,
    _discover_cache_key,
    _discover_response,
    _discover_timeout,
    _discovery_element,
    _parse_bbox,
    _rank_and_cap,
    _without_our_sites,
    discover,
    logger,
)
from .platform_stats import (  # noqa: F401
    _STATS_CACHE_KEY,
    _STATS_CACHE_SECONDS,
    site_stats,
    stats,
)
from .runs import (  # noqa: F401
    _MIN_RECONSTRUCTION_SECONDS,
    _MODEL_FORMATS,
    _SPLAT_FORMATS,
    VIEWER_FORMATS,
    _artifact,
    _artifact_url,
    _model_format,
    _reconstruction_seconds,
    _sibling,
    _viewer_formats,
    run_detail,
    serialize_run,
)
from .site_requests import (  # noqa: F401
    DEFAULT_SITE_REQUEST_MAX_BODY_BYTES,
    _requester,
    _text,
    _too_large,
    request_site,
)
