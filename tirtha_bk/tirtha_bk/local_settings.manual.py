# NOTE: Rename to local_settings.py after editing
"""
Fields marked CHANGEME: need to be changed before deployment

"""

import os
from pathlib import Path

from .env import env_bool, env_int, env_required

# Everything every deployment shares; this file sets only what differs.
# Keep this import first: the settings below override some of its values.
from .settings_base import *  # noqa: F403

# SECURITY WARNING: do not run with debug turned on in production!
DEBUG = env_bool("DEBUG", False)

# SECURITY WARNING: keep the secret key used in production secret.
# Required when DEBUG is False: a generated key differs per gunicorn worker,
# so sessions signed by one worker are rejected by the next.
SECRET_KEY = env_required(
    "SECRET_KEY",
    debug=DEBUG,
    default="django-insecure-dev-key-not-for-production",
    hint='Generate one with `python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"`.',
)
TIME_ZONE = os.getenv("TIME_ZONE", "Asia/Kolkata")  # CHANGEME:
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin-allow-popups"
_allowed = os.getenv("ALLOWED_HOSTS")
if _allowed:
    ALLOWED_HOSTS = [h.strip() for h in _allowed.split(",") if h.strip()]
else:
    ALLOWED_HOSTS = [
        "localhost",
        "0.0.0.0",
        os.getenv("HOST_IP", "127.0.0.1"),
    ]  # CHANGEME:

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
MIDDLEWARE.append("tirtha.middleware.SecurityHeadersMiddleware")

# Application definition
INSTALLED_APPS = [
    "tirtha.apps.TirthaConfig",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_cleanup.apps.CleanupConfig",  # For cleaning up orphaned files in media
    "django_extensions",
    "dbbackup",  # django-dbbackup
]

# Tirtha specific settings
BASE_DIR = Path(__file__).resolve().parent.parent
PRE_URL = os.getenv("PRE_URL", "project/tirtha/")  # CHANGEME: e.g., "/tirtha/"

PROD_DIR = os.getenv(  # Short term storage for current runs # CHANGEME:
    "PROD_DIR", "/var/www/tirtha/prod/"
)
NFS_DIR = os.getenv(  # Long term storage for old runs # CHANGEME: Does not need to use NFS and can be on the same system
    "NFS_DIR", "/var/www/tirtha/archive/"
)
ARCHIVE_ROOT = f"{NFS_DIR}archives"
LOG_DIR = f"{PROD_DIR}logs"
LOG_LOCATION = LOG_DIR + "/django.log"
ADMIN_LOG_LOCATION = LOG_DIR + "/admin.log"

# Static files
# `PRE_URL` already ends in a slash; the extra one here produced
# `/project/tirtha//static/`. This file is the template the cutover copies from
# (the internal cutover notes), so the doubled slash would have gone straight into
# the new production configuration.
STATIC_URL = os.getenv("STATIC_URL", "/" + PRE_URL + "static/")
STATIC_ROOT = os.path.join(os.getenv("PROD_DIR", PROD_DIR), "static")

# Media files
MEDIA_URL = os.getenv("MEDIA_URL", "/" + PRE_URL + "media/")
MEDIA_ROOT = os.path.join(os.getenv("PROD_DIR", PROD_DIR), "media")
# STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Default attributes used to create default site & contributor
# NOTE: You will need to create the default contributor manually before running the server for the first time -> Needed in `post_migrate_create_defaults()` in `tirtha_bk/tirtha/signals.py.
DEFAULT_SITE_NAME = os.getenv("DEFAULT_SITE_NAME", "Meditation Center")
DEFAULT_SITE_ID = os.getenv("DEFAULT_SITE_ID", "9zpT9kVZwP9XxAbG")
ADMIN_NAME = os.getenv("DEFAULT_USER_NAME", "Tirtha Admin")  # CHANGEME:
ADMIN_MAIL = os.getenv("DEFAULT_USER_MAIL", "tadmin@example.com")  # CHANGEME:

# Sign in with Google
GOOGLE_LOGIN = os.getenv("GOOGLE_LOGIN", "False").lower() == "true"
GOOGLE_CLIENT_ID = os.getenv(
    "GOOGLE_CLIENT_ID", ""
)  # CHANGEME: https://developers.google.com/identity/gsi/web/guides/overview
GOOGLE_CLIENT_SECRET = env_required(
    "GOOGLE_CLIENT_SECRET",
    debug=DEBUG or not GOOGLE_LOGIN,
    default="",
    hint="Only required when GOOGLE_LOGIN is on.",
)  # CHANGEME: https://developers.google.com/identity/gsi/web/guides/overview
COOKIE_EXPIRE_TIME = int(os.getenv("COOKIE_EXPIRE_TIME", "3600"))  # 1 hour
SESSION_COOKIE_SAMESITE = os.getenv(
    "SESSION_COOKIE_SAMESITE", "Lax"
)  # NOTE: Lax needed for authlib | See: https://docs.djangoproject.com/en/5.1/ref/settings/#std-setting-SESSION_COOKIE_SAMESITE
SESSION_COOKIE_SECURE = True if GOOGLE_LOGIN else False
CSRF_COOKIE_SAMESITE = "Strict"
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_PRELOAD = True
SECURE_HSTS_SECONDS = (
    31536000  # 1 year # NOTE: If sometime HTTPS is disabled, this should be removed
)
SECURE_BROWSER_XSS_FILTER = True
SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# OAuth app config
OAUTH_CONF = {
    "OAUTH2_CLIENT_ID": GOOGLE_CLIENT_ID,
    "OAUTH2_CLIENT_SECRET": GOOGLE_CLIENT_SECRET,
    "OAUTH2_META_URL": "https://accounts.google.com/.well-known/openid-configuration",
    "OAUTH2_SCOPE": "openid profile email",
    "OAUTH2_REDIRECT_URI": "",  # CHANGEME: e.g., "https://yourdomain.com/project/tirtha/verifyToken/"
}

# Django admin access (tirtha/admin_auth.py; DEPLOY.md, "Admin access").
# ADMIN_GOOGLE_EMAILS   addresses allowed to reach the admin by "Sign in with
#                       Google" on the admin login page. Compared
#                       case-insensitively; each must belong to exactly one
#                       existing active staff User (Users are never created).
#                       Empty: nobody gets in through Google.
# ADMIN_PASSWORD_LOGIN  False removes the username/password form and refuses
#                       a password POST to the admin login. Production sets it
#                       False; `manage.py admin_login_link <username>` is the
#                       way in if Google is unavailable (needs shell access).
ADMIN_GOOGLE_EMAILS = [
    e.strip() for e in os.getenv("ADMIN_GOOGLE_EMAILS", "").split(",") if e.strip()
]  # CHANGEME: e.g., "admin@example.org,staff@example.org"
ADMIN_PASSWORD_LOGIN = env_bool("ADMIN_PASSWORD_LOGIN", True)

# Database
# https://docs.djangoproject.com/en/4.1/ref/settings/#databases
# NOTE: For help, see https://www.digitalocean.com/community/tutorials/how-to-set-up-django-with-postgres-nginx-and-gunicorn-on-ubuntu-22-04
DB_NAME = os.getenv("DB_NAME", "dbtirtha")  # CHANGEME:
DB_USER = os.getenv("DB_USER", "dbtirthauser")  # CHANGEME:
DB_PWD = env_required("DB_PWD", debug=DEBUG, default="")  # CHANGEME: set DB password
DB_HOST = os.getenv("DB_HOST", "localhost")  # CHANGEME:
DB_PORT = os.getenv("DB_PORT", "")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": DB_NAME,
        "USER": DB_USER,
        "PASSWORD": DB_PWD,
        "HOST": DB_HOST,
        "PORT": DB_PORT,
    }
}

# django-dbbackup
DBBACKUP_STORAGE = "django.core.files.storage.FileSystemStorage"
DBBACKUP_STORAGE_OPTIONS = {
    "location": os.getenv("DBBACKUP_LOCATION", f"{NFS_DIR}/db_backups/")
}  # CHANGEME: To store backups

## RabbitMQ + Celery
RMQ_USER = os.getenv("RMQ_USER", "rmqtirthauser")  # CHANGEME:
RMQ_PWD = os.getenv("RMQ_PWD", "rmqtirthapwd")  # CHANGEME:
RMQ_VHOST = os.getenv(
    "RMQ_VHOST", "rmqtirtha"
)  # CHANGEME: NOTE: You can also use the default vhost ("/").
CELERY_BROKER_URL = f"pyamqp://{RMQ_USER}:{RMQ_PWD}@localhost/{RMQ_VHOST}"
CELERY_TASK_ACKS_LATE = True  # To prevent tasks from being lost
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # Disable prefetching
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1  # NOTE: For memory release: https://stackoverflow.com/questions/17541452/celery-does-not-release-memory
CELERY_BROKER_CONNECTION_RETRY = False
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_CONNECTION_MAX_RETRIES = 10

## Worker-related settings
# GS
COLMAP_PATH = os.getenv("COLMAP_PATH", "colmap")  # CHANGEME: Path to colmap executable
ALPHA_CULL_THRESH = float(
    os.getenv("ALPHA_CULL_THRESH", "0.005")
)  # Threshold to delete translucent gaussians - lower values remove more (usually better quality)
MIN_MATCHED_IMAGES = int(os.getenv("MIN_MATCHED_IMAGES", "5"))
MIN_MATCH_RATIO = float(os.getenv("MIN_MATCH_RATIO", "0.10"))

# MR
# NOTE: Defaulting to Meshroom 2021 for now. 2023/25 will require further changes
ALICEVISION_DIRPATH = Path(
    os.getenv("ALICEVISION_DIRPATH", BASE_DIR / "bin21")
)  # CHANGEME:
# NOTE: See `Requirements` section in README.md

OBJ2GLTF_PATH = os.getenv("OBJ2GLTF_PATH", "obj2gltf")
GLTFPACK_PATH = os.getenv("GLTFPACK_PATH", "gltfpack")
MESHOPS_MIN_IMAGES = int(
    os.getenv("MESHOPS_MIN_IMAGES", "10")
)  # CHANGEME: Minimum number of images required to run meshops
MESHOPS_MAX_IMAGES = int(os.getenv("MESHOPS_MAX_IMAGES", "500"))
DATA_UPLOAD_MAX_NUMBER_FILES = int(
    os.getenv("DATA_UPLOAD_MAX_NUMBER_FILES", "2000")
)  # 2_000 files # Max number of files per upload

## ARK settings
BASE_URL = os.getenv(
    "BASE_URL",
    f"http://{os.getenv('HOST_IP', '0.0.0.0')}:{os.getenv('GUNICORN_PORT', '8000')}",
)  # CHANGEME: NOTE: No trailing "/" | e.g., http://127.0.0.1
ARK_NAAN = int(
    os.getenv("ARK_NAAN", "999999")
)  # CHANGEME: Integer | NOTE: NAAN - 999999 does not exist; CHECK: https://arks.org/about/testing-arks/
ARK_SHOULDER = os.getenv(
    "ARK_SHOULDER", "/a"
)  # CHANGEME: | CHECK: https://arks.org/about/testing-arks/
FALLBACK_ARK_RESOLVER = os.getenv("FALLBACK_ARK_RESOLVER", "https://n2t.net")

## Email settings
# SMTP configuration for sending emails (e.g., new contributor notifications)
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")  # CHANGEME: Use your SMTP server
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() == "true"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")  # CHANGEME: Your email address
EMAIL_HOST_PASSWORD = env_required(
    "EMAIL_HOST_PASSWORD",
    debug=DEBUG,
    default="",
    hint="Contributor and admin notification mail is sent through this account.",
)  # CHANGEME: Your email password or app password
DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL", ADMIN_MAIL
)  # CHANGEME: Default sender email
ADMINS = [
    (
        ADMIN_NAME,
        ADMIN_MAIL,
    ),  # CHANGEME: Admin email addresses
]
SERVER_EMAIL = os.getenv(
    "SERVER_EMAIL", ADMIN_MAIL
)  # CHANGEME: For server error emails

# List of contributor email addresses to ignore when sending new contribution notifications
CONTRIB_IGNORE_LIST = [
    ADMIN_MAIL,
    SERVER_EMAIL,
    *[e.strip() for e in os.getenv("CONTRIB_IGNORE_LIST", "").split(",") if e.strip()],
]

# Toggle for sending success emails to contributors when their contribution processing finishes
# Contributors are written to: a success mail when their batch becomes a model,
# and a plain-language note when it does not. It was False -- "admins are mailed,
# contributors are not" -- so somebody who photographed a site all afternoon
# heard nothing either way.
#
# The failure mail they receive is `send_contributor_run_failed_email`, not the
# admin one: that carries /admin/ links, the exception and a log path, none of
# which belongs in a contributor's inbox.
MAIL_CONTRIB_TOGGLE = True

# Read the same way as before; settings_base has the plain 60.
OVERPASS_TIMEOUT = int(os.getenv("OVERPASS_TIMEOUT", "60"))

# FRGS, in its own environment (see settings_base.py).
FRGS_PATH = "frgs"

# ------------------------------------------------------------- COLMAP workspace
# Durable per-site COLMAP working directories (internal design notes). Outside
# MEDIA_ROOT and outside the run folders: the workspace belongs to the site,
# outlives any single run, and must not be swept up by run archival.
COLMAP_WORKSPACE_DIR = os.path.join(PROD_DIR, "colmap_ws")

# ------------------------------------------------------------------ throttling
# Per-IP site-request throttling and TRUSTED_PROXY_DEPTH: settings_base.py.
# Uploads, per contributor. UPLOAD_RATE_LIMIT counts contributions created (a
# one-POST upload, or the first batch of a batched one); the batch limit counts
# every batched request -- each batch, finish and discard.
UPLOAD_RATE_LIMIT = env_int("UPLOAD_RATE_LIMIT", 20)
UPLOAD_RATE_WINDOW = env_int("UPLOAD_RATE_WINDOW", 600)  # seconds
UPLOAD_BATCH_RATE_LIMIT = env_int("UPLOAD_BATCH_RATE_LIMIT", 60)
UPLOAD_BATCH_RATE_WINDOW = env_int("UPLOAD_BATCH_RATE_WINDOW", 600)  # seconds
# Volume caps on a batched upload (413 `too_many_files` / `too_large`; 429
# `too_many_open`). A batch of one video alone is exempt from the batch cap.
UPLOAD_MAX_FILES_PER_CONTRIBUTION = env_int("UPLOAD_MAX_FILES_PER_CONTRIBUTION", 1000)
UPLOAD_MAX_BYTES_PER_CONTRIBUTION = env_int(
    "UPLOAD_MAX_BYTES_PER_CONTRIBUTION", 3 * 1024**3
)
UPLOAD_MAX_BYTES_PER_BATCH = env_int("UPLOAD_MAX_BYTES_PER_BATCH", 300 * 1024**2)
UPLOAD_MAX_OPEN_PER_CONTRIBUTOR = env_int("UPLOAD_MAX_OPEN_PER_CONTRIBUTOR", 3)
# A batched upload idle this long refuses more batches, and the 15-minute
# sweep finalizes it -- or deletes it, if it holds no files, its first batch
# was never acknowledged, or its contributor is banned or inactive.
UPLOAD_OPEN_TTL_MINUTES = env_int("UPLOAD_OPEN_TTL_MINUTES", 120)
