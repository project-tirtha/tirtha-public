"""
Beta settings.

Beta is a second deployment against a restored copy of production, used to
rehearse changes before they reach the real thing (internal design notes).

It runs on SQLite by default. That is a deliberate trade, not a shortcut:

* SQLite holds the whole production dataset without complaint -- 60 sites,
  19,321 images, 81 runs -- so beta is useful immediately, with no privileged
  database provisioning.
* What SQLite cannot do is prove a migration works on PostgreSQL. Django
  emulates ALTER TABLE on SQLite by rebuilding the table, while PostgreSQL runs
  real DDL, so a migration can pass on one and fail on the other. PostGIS
  (Phase 2.2, 2.5) is likewise not the same as SpatiaLite.

So: SQLite beta is right for everything up to Phase 2, and Phase 2's schema
campaign wants `BETA_DB_ENGINE=postgres` and a real database. Set that and
DB_PWD when it is available.
"""

import os

from .env import env, env_bool, env_int, env_required
from .settings_base import *  # noqa: F403

DEBUG = env_bool("DEBUG", False)
SECRET_KEY = env_required(
    "SECRET_KEY",
    debug=True,  # beta is not public; a generated key is acceptable here
    default="django-insecure-beta-key",
)
TIME_ZONE = env("TIME_ZONE", "Asia/Kolkata")
ALLOWED_HOSTS = (env("ALLOWED_HOSTS", "localhost,127.0.0.1") or "").split(",")

# ---------------------------------------------------------------------------
# Cookie namespace.
#
# Beta runs against a restored *copy* of production's data under its own
# `PRE_URL`, and `ALLOWED_HOSTS` defaults to localhost -- so unlike the demo it
# does not currently share an origin with production. This is set anyway,
# because the cost is one line and the failure it prevents is silent: the moment
# beta is put behind the shared hostname (which is how it would be shown to
# anyone), Django's default `sessionid` at path `/` would start deleting
# production's cookie for every visitor who opened both.
#
# `SessionMiddleware.process_response` deletes the cookie, host-wide, whenever
# it receives a session key it cannot find in its own database. So a contributor
# signed in on production who merely *opened* this deployment in another tab was
# silently signed out of production, mid-upload, having taken no action here.
#
# Cookie identity is (name, domain, path), so a distinct name -- not merely a
# distinct path -- is what makes these independent. It also removes the
# cross-deployment fixation surface `views.verifyToken` reasons about.
SESSION_COOKIE_NAME = "tirthabeta_sessionid"
CSRF_COOKIE_NAME = "tirthabeta_csrftoken"

# This deployment is public and served over HTTPS. It set none of these, so a
# request that arrived over plain HTTP carried both cookies in cleartext -- and
# because the CSRF cookie was shared with production (above), a token read off
# the wire was replayable against production's origin.
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# ---------------------------------------------------------------------------

PRE_URL = env("PRE_URL", "beta/tirtha/")
TIRTHA_ROOT = env("TIRTHA_ROOT", str(BASE_DIR / "beta"))
PROD_DIR = env("PROD_DIR", f"{TIRTHA_ROOT}/prod/")
NFS_DIR = env("NFS_DIR", f"{TIRTHA_ROOT}/archive/")
ARCHIVE_ROOT = f"{NFS_DIR}archives"
LOG_DIR = env("LOG_DIR", f"{PROD_DIR}logs")
LOG_LOCATION = f"{LOG_DIR}/tirthaDjango.log"
ADMIN_LOG_LOCATION = f"{LOG_DIR}/tirthaAdminDjango.log"
for _d in (PROD_DIR, NFS_DIR, LOG_DIR, ARCHIVE_ROOT):
    os.makedirs(_d, exist_ok=True)

# Rotated, at INFO, one file each for the app and the admin (settings_base.py).
LOGGING = rotating_file_logging(LOG_LOCATION, ADMIN_LOG_LOCATION)

STATIC_URL = "/" + PRE_URL + "static/"
STATIC_ROOT = os.path.join(PROD_DIR, "static")
MEDIA_URL = "/" + PRE_URL + "media/"
MEDIA_ROOT = f"{PROD_DIR}media"

# --------------------------------------------------------------------- database
if env("BETA_DB_ENGINE", "sqlite") == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME", "tirthadb_beta"),
            "USER": env("DB_USER", "tirtha_beta"),
            "PASSWORD": env_required("DB_PWD", debug=False),
            "HOST": env("DB_HOST", "localhost"),
            "PORT": env("DB_PORT", ""),
        }
    }
else:
    # Defaults to the same path build/beta.env computes, so a forgotten
    # `set -a; source build/beta.env` does not silently address a different file.
    _beta_db = env("BETA_DB_PATH", f"{TIRTHA_ROOT}/beta.sqlite3")

    # Beta is always a *restored copy* of production, never a fresh database.
    # SQLite creates whatever path it is handed, so without this a mistyped or
    # unset BETA_DB_PATH produces an empty database that migrates cleanly and
    # proves nothing -- the exact failure this rail was added for. setup-beta.sh
    # sets BETA_DB_ALLOW_CREATE=1 for the one run that legitimately creates it.
    if not os.path.exists(_beta_db) and not env_bool("BETA_DB_ALLOW_CREATE", False):
        raise RuntimeError(
            f"Beta database not found at {_beta_db}. Beta must run against a "
            "restored copy of production, not an empty database. Either "
            "`set -a; source build/beta.env; set +a` first, or run "
            "build/setup-beta.sh to create it."
        )

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": _beta_db,
        }
    }

# Guard: beta must never address the production database, whatever the
# environment says. Beta exists to protect production (internal design notes).
if DATABASES["default"].get("NAME") in {"tirthadb"}:
    raise RuntimeError("Beta is configured against the production database. Refusing.")

# ------------------------------------------------------------------ app config
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
DBBACKUP_STORAGE_OPTIONS = {"location": f"{NFS_DIR}db_backups/"}
# How many backups `backup_task` keeps (it passes `clean=True`).
#
# Kept separately because the two are nothing alike in size: the database dump
# is ~2 MB, so a month of them costs less than one media tar -- while
# `mediabackup` writes a *full* tar every night, 13 GB here and growing with the
# archive. dbbackup's own default of 10 for both would hold 130 GB of media on
# the same disk that serves the site.
DBBACKUP_CLEANUP_KEEP = 30
DBBACKUP_CLEANUP_KEEP_MEDIA = 3

DEFAULT_SITE_NAME = "Meditation Center"
DEFAULT_SITE_ID = "9zpT9kVZwP9XxAbG"
ADMIN_NAME = "Tirtha Admin"
ADMIN_MAIL = env("ADMIN_MAIL", "admin@example.com")
ADMINS = [(ADMIN_NAME, ADMIN_MAIL)]
CONTRIB_IGNORE_LIST = []

# Beta never reconstructs: one machine, one GPU, and production owns it.
# Contributors are written to: a success mail when their batch becomes a model,
# and a plain-language note when it does not. It was False -- "admins are mailed,
# contributors are not" -- so somebody who photographed a site all afternoon
# heard nothing either way.
#
# The failure mail they receive is `send_contributor_run_failed_email`, not the
# admin one: that carries /admin/ links, the exception and a log path, none of
# which belongs in a contributor's inbox.
MAIL_CONTRIB_TOGGLE = True
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

GOOGLE_LOGIN = False
OAUTH_CONF = {
    "OAUTH2_CLIENT_ID": "",
    "OAUTH2_CLIENT_SECRET": "",
    "OAUTH2_META_URL": "https://accounts.google.com/.well-known/openid-configuration",
    "OAUTH2_SCOPE": "openid profile email",
    "OAUTH2_REDIRECT_URI": "http://localhost:8100/verifyToken/",
}

BASE_URL = env("BASE_URL", "http://localhost:8100")
ARK_NAAN = env_int("ARK_NAAN", 74218)
ARK_SHOULDER = env("ARK_SHOULDER", "/t1")
FALLBACK_ARK_RESOLVER = "https://n2t.net"

COLMAP_PATH = env("COLMAP_PATH", "colmap")
MIN_MATCHED_IMAGES = env_int("MIN_MATCHED_IMAGES", 5)
MIN_MATCH_RATIO = 0.10
ALICEVISION_DIRPATH = BASE_DIR / "bin21"
OBJ2GLTF_PATH = env("OBJ2GLTF_PATH", "obj2gltf")
GLTFPACK_PATH = env("GLTFPACK_PATH", "gltfpack")
MESHOPS_MIN_IMAGES = env_int("MESHOPS_MIN_IMAGES", 10)
MESHOPS_MAX_IMAGES = env_int("MESHOPS_MAX_IMAGES", 1000)
VGGT_ENABLE = False

CELERY_BROKER_URL = env("CELERY_BROKER_URL", "memory://")
CELERY_TASK_ALWAYS_EAGER = True  # no worker; beta runs no pipeline
DATA_UPLOAD_MAX_NUMBER_FILES = env_int("DATA_UPLOAD_MAX_NUMBER_FILES", 1000)

OVERPASS_TIMEOUT = env_int("OVERPASS_TIMEOUT", 60)

# FRGS, in its own environment (see settings_base.py).
FRGS_PATH = "frgs"

# Durable per-site COLMAP working directories (internal design notes). Outside
# MEDIA_ROOT and outside the run folders: the workspace belongs to the site,
# outlives any single run, and must not be swept up by run archival.
COLMAP_WORKSPACE_DIR = f"{PROD_DIR}colmap_ws"
