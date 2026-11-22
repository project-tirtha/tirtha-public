"""
Development - Local Dev Settings

Everything shared with the other deployments is in `settings_base.py`; this
file holds only what dev does differently.
"""

import os

from .settings_base import *  # noqa: F403

# Development defaults
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret-key-change-me")
TIME_ZONE = "Asia/Kolkata"
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# Default attributes used to create default site & contributor
DEFAULT_SITE_NAME = "Meditation Center"  # Default site to use for new runs
DEFAULT_SITE_ID = "9zpT9kVZwP9XxAbG"
ADMIN_NAME = "Tirtha Admin"
ADMIN_MAIL = "tadmin@example.com"

# Paths
PRE_URL = "project/tirtha/"
PROD_DIR = os.path.join(BASE_DIR, "prod")
NFS_DIR = os.path.join(BASE_DIR, "arch")
ARCHIVE_ROOT = os.path.join(NFS_DIR, "archives")
LOG_DIR = os.path.join(PROD_DIR, "logs")
LOG_LOCATION = os.path.join(LOG_DIR, "django.log")
ADMIN_LOG_LOCATION = os.path.join(LOG_DIR, "admin.log")

# Ensure folders exist
os.makedirs(PROD_DIR, exist_ok=True)
os.makedirs(NFS_DIR, exist_ok=True)
os.makedirs(ARCHIVE_ROOT, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# Static files
# `PRE_URL` already ends in a slash. This read `"/" + PRE_URL + "/static/"`,
# which produced `/project/tirtha//static/` -- a doubled slash that `vite.py`
# has to normalise around, and that breaks the prefix comparisons in
# `SecurityHeadersMiddleware._is_embeddable`, so static assets never got the
# cross-origin headers the embed route needs. The other deployments spell it
# without the extra slash; dev was the odd one out, which is the worst place
# for it -- it makes "works in dev" mean something different.
STATIC_URL = "/" + PRE_URL + "static/"
STATIC_ROOT = os.path.join(PROD_DIR, "static")

# Media files
# Leading slash, for the same reason: `request.path` always has one, so a
# prefix without it matches nothing.
MEDIA_URL = "/" + PRE_URL + "media/"
MEDIA_ROOT = os.path.join(PROD_DIR, "media")

# Under `manage.py test` this root is replaced with a scratch one, along with
# STATIC_ROOT -- in `settings.py`, because the same substitution has to reach
# every deployment and not only the one a developer runs the suite on.

# Use SQLite for local development
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
    }
}

# django-dbbackup
DBBACKUP_STORAGE = "django.core.files.storage.FileSystemStorage"
DBBACKUP_STORAGE_OPTIONS = {"location": f"{NFS_DIR}/db_backups/"}
# How many backups `backup_task` keeps (it passes `clean=True`).
#
# Kept separately because the two are nothing alike in size: the database dump
# is ~2 MB, so a month of them costs less than one media tar -- while
# `mediabackup` writes a *full* tar every night, 13 GB here and growing with the
# archive. dbbackup's own default of 10 for both would hold 130 GB of media on
# the same disk that serves the site.
DBBACKUP_CLEANUP_KEEP = 30
DBBACKUP_CLEANUP_KEEP_MEDIA = 3

# Security options relaxed for local development, swap back
GOOGLE_LOGIN = False

# OAuth — GOOGLE_LOGIN is False above, but `views.py` reads OAUTH_CONF at import
# time, so the key must exist. Env-var overrides let a developer test the real
# flow without editing this file.
OAUTH_CONF = {
    "OAUTH2_CLIENT_ID": os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
    "OAUTH2_CLIENT_SECRET": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", ""),
    "OAUTH2_META_URL": "https://accounts.google.com/.well-known/openid-configuration",
    "OAUTH2_SCOPE": "openid profile email",
    "OAUTH2_REDIRECT_URI": "http://localhost:8000/verifyToken/",
}

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False

## Worker-related settings
# GS
COLMAP_PATH = "colmap"  # NOTE: Ensure the binary is on system/conda env PATH
ALPHA_CULL_THRESH = 0.005  # Threshold to delete translucent gaussians - lower values remove more (usually better quality)
MIN_MATCHED_IMAGES = 5  # Minimum number of matched images required
MIN_MATCH_RATIO = 0.10  # Minimum ratio of matched images to total images

ALICEVISION_DIRPATH = BASE_DIR / "bin21"
OBJ2GLTF_PATH = "obj2gltf"  # NOTE: Ensure the binary is on system PATH
GLTFPACK_PATH = "gltfpack"  # NOTE: Ensure the binary is on system PATH
MESHOPS_MIN_IMAGES = 10  # Minimum number of images required to run meshops
MESHOPS_MAX_IMAGES = (
    500  # Maximum number of images to use for meshops - to avoid OOM issues
)
DATA_UPLOAD_MAX_NUMBER_FILES = 2_000  # 2_000 files # Max number of files per upload

## Celery
## NOTE: RabbitMQ setup skipped for local dev - tasks run eagerly
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # Disable prefetching
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1  # NOTE: For memory release: https://stackoverflow.com/questions/17541452/celery-does-not-release-memory
CELERY_BROKER_CONNECTION_RETRY = False
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_CONNECTION_MAX_RETRIES = 10

# ARK
BASE_URL = "http://0.0.0.0:8000/project/tirtha"  # CHANGEME: NOTE: No trailing "/" | e.g., http://127.0.0.1
ARK_NAAN = 999999  # CHANGEME: Integer | NOTE: NAAN - 999999 does not exist; CHECK: https://arks.org/about/testing-arks/
ARK_SHOULDER = "/a"  # CHANGEME: | CHECK: https://arks.org/about/testing-arks/
FALLBACK_ARK_RESOLVER = "https://n2t.net"

# EMAIL
MAIL_CONTRIB_TOGGLE = False

# FRGS, in its own environment (see settings_base.py).
FRGS_PATH = "frgs"

# Durable per-site COLMAP working directories (internal design notes). Outside
# MEDIA_ROOT and outside the run folders: the workspace belongs to the site,
# outlives any single run, and must not be swept up by run archival.
COLMAP_WORKSPACE_DIR = os.path.join(PROD_DIR, "colmap_ws")

# ------------------------------------------------- fixed, not from environment
# Dev pins these where the deployments read them from the environment
# (settings_base.py), so a developer's shell cannot change them. Dev must not
# need a 15 GB Overpass mirror to show a marker (internal design notes).
OVERPASS_URL = ""
DISCOVER_MIN_ZOOM = 12
DISCOVER_MAX_BBOX_SPAN = 2.0  # degrees
DISCOVER_CACHE_SECONDS = 900
DISCOVER_RATE_LIMIT = 240
DISCOVER_RATE_WINDOW = 3600  # seconds
SITE_REQUEST_RATE_LIMIT = 5
SITE_REQUEST_RATE_WINDOW = 3600  # seconds
# Zero because `runserver` has no proxy at all: REMOTE_ADDR is the client.
TRUSTED_PROXY_DEPTH = 0
