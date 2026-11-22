"""
Demo deployment — public, read-only (internal design notes).

Serves the upgraded stack at /project/tirthademo/ against the same restored
production data beta uses. Three things differ from beta, and each is a safety
property rather than a preference:

- **Read-only.** `DEMO_READ_ONLY` blocks every write endpoint. A public upload
  endpoint with nobody watching the queue is a liability, and a demo does not
  need one. Site requests are blocked for the same reason.
- **No reconstruction.** There is no Celery worker here. Nothing may enqueue
  GPU work from a public surface.
- **DEBUG is off and hosts are pinned.** This is exposed to the internet, which
  beta is not.

Crawling is disallowed in full (`/robots.txt`, `/llms.txt`): the models are
CC BY-NC-SA / BY-NC-ND and the records carry contributor identities.
"""

import os

from .env import env, env_bool, env_int, env_required
from .settings_base import *  # noqa: F403

DEPLOYMENT_NAME = "Project Tirtha (demo)"

# Off. This is public.
DEBUG = False
SECRET_KEY = env_required("SECRET_KEY", debug=False)
ALLOWED_HOSTS = (env("ALLOWED_HOSTS", "smlab.niser.ac.in") or "").split(",")
CSRF_TRUSTED_ORIGINS = ["https://smlab.niser.ac.in"]
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ---------------------------------------------------------------------------
# Cookie namespace.
#
# The demo answers on the SAME hostname as production -- side by side under a
# different `PRE_URL` -- and runs against a DIFFERENT database. With Django's
# defaults all of them issue `sessionid` and `csrftoken` at path `/`, so the
# browser keeps one cookie for three deployments that cannot read each other's
# sessions.
#
# `SessionMiddleware.process_response` deletes the cookie, host-wide, whenever
# it receives a session key it cannot find in its own database. So a contributor
# signed in on production who merely *opened* this deployment in another tab was
# silently signed out of production, mid-upload, having taken no action here.
#
# Cookie identity is (name, domain, path), so a distinct name -- not merely a
# distinct path -- is what makes these independent. It also removes the
# cross-deployment fixation surface `views.verifyToken` reasons about.
SESSION_COOKIE_NAME = "tirthademo_sessionid"
CSRF_COOKIE_NAME = "tirthademo_csrftoken"

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

TIME_ZONE = env("TIME_ZONE", "Asia/Kolkata")
PRE_URL = env("PRE_URL", "project/tirthademo/")

TIRTHA_ROOT = env("TIRTHA_ROOT", "/var/www/tirtha_demo")
PROD_DIR = f"{TIRTHA_ROOT}/prod/"
NFS_DIR = f"{TIRTHA_ROOT}/archive/"
ARCHIVE_ROOT = f"{NFS_DIR}archives"
LOG_DIR = f"{PROD_DIR}logs"
LOG_LOCATION = f"{LOG_DIR}/tirthaDjango.log"
ADMIN_LOG_LOCATION = f"{LOG_DIR}/tirthaAdminDjango.log"
for _d in (PROD_DIR, NFS_DIR, LOG_DIR, ARCHIVE_ROOT):
    os.makedirs(_d, exist_ok=True)


# Rotated, at INFO, one file each for the app and the admin (settings_base.py).
LOGGING = rotating_file_logging(LOG_LOCATION, ADMIN_LOG_LOCATION)

STATIC_URL = "/" + PRE_URL + "static/"
STATIC_ROOT = os.path.join(PROD_DIR, "static")
# The live media, read-only. 13 GB, and copying it would buy nothing: the demo
# never writes. The `images/nsfw` quarantine directories are empty (verified),
# so nothing withheld is exposed.
MEDIA_URL = "/" + PRE_URL + "media/"
MEDIA_ROOT = env("MEDIA_ROOT", "/var/www/tirtha/prod/media")

# Shares beta's database. Safe precisely because the demo cannot write: see
# DEMO_READ_ONLY below.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", "tirthadb_beta"),
        "USER": env("DB_USER", "tirtha_demo_ro"),
        "PASSWORD": env_required("DB_PWD", debug=False),
        "HOST": env("DB_HOST", "localhost"),
        "PORT": env("DB_PORT", ""),
    }
}
if DATABASES["default"]["NAME"] == "tirthadb":
    raise RuntimeError(
        "The demo is configured against the production database. Refusing."
    )

# Blocks every write endpoint (see tirtha.middleware.ReadOnlyMiddleware).
DEMO_READ_ONLY = env_bool("DEMO_READ_ONLY", True)

# The shared middleware, plus the one that makes this deployment read-only.
MIDDLEWARE = [*MIDDLEWARE, "tirtha.middleware.ReadOnlyMiddleware"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Google sign-in is off: the demo has no reason to collect identities.

# Defaults the post_migrate signal expects (see tirtha/signals.py).
DEFAULT_SITE_ID = "9zpT9kVZwP9XxAbG"
DEFAULT_SITE_NAME = "Meditation Center"
ADMIN_NAME = "Tirtha Admin"

GOOGLE_LOGIN = False
ADMIN_MAIL = env("ADMIN_MAIL", "admin@example.org")
OAUTH_CONF = {}

REACT_SHELL_ENABLED = True
VITE_MANIFEST_PATH = env(
    "VITE_MANIFEST_PATH",
    str(BASE_DIR.parent / "frontend" / "dist" / ".vite" / "manifest.json"),
)

# Published artifacts are rewritten to the demo's own converted SOG. The stored
# ARK URLs point at the live site's legacy `.splat`, which the 6.4 viewer
# cannot read (internal design notes).
# Served from `<root>/models/`, deliberately NOT from STATIC_ROOT: a
# `collectstatic --clear` wipes STATIC_ROOT, and it deleted these once already.
# Production has the same hazard -- its published models live under
# static/models/ -- which is worth knowing before anyone runs --clear there.
DEMO_MODEL_ROOT = f"{TIRTHA_ROOT}/models"

# Kopeshwar Temple. The newest run is converted because the site page shows
# runs newest-first, so that is the one a visitor actually loads.
_KOPESHWAR = (
    "https://smlab.niser.ac.in/project/tirtha/static/models/hHqeWBhb92BDfJfo/published/"
)


# The demo holds its own converted copies (internal design notes).
#
# `ARTIFACT_ROOT` cannot do this job here: it offers a `.sog` beside a `.splat`
# under the *same* URL prefix, and an ARK minted by production carries
# `/project/tirtha/`, which on this host resolves to production's static tree --
# where no conversions live. So the demo maps each legacy URL onto its own
# prefix explicitly, which is exactly what this setting is for.
#
# Built by scanning, not by hand: 56 pairs is a list that would rot.
def _demo_sog_rewrites():
    import os

    root = os.path.join(STATIC_ROOT, "models")
    pairs = []
    for base, _, files in os.walk(root):
        for name in files:
            if not name.endswith(".sog"):
                continue
            rel = os.path.relpath(os.path.join(base, name), root)
            pairs.append(
                (
                    f"https://smlab.niser.ac.in/project/tirtha/static/models/"
                    f"{rel[:-4]}.splat",
                    f"https://smlab.niser.ac.in/{PRE_URL}static/models/{rel}",
                )
            )
    return pairs


# **What the deployed viewer can actually read** (internal design notes), in
# preference order.
#
# This is the coupling that makes the rewrites above a liability rather than an
# improvement. The `.sog` conversions were made for the PlayCanvas GSplat viewer
# that 20 targets. The React UI now shipping reads `.splat`, `.spz` and `.ply`
# through `@mkkellogg/gaussian-splats-3d` -- the same library and version as the
# old public site -- and cannot read `.sog` at all.
#
# So with the rewrites on, every one of the 56 converted runs is offered to a
# viewer that cannot draw it. The client skips them (`firstViewableRun`) and the
# page says "no 3D model yet" for sites that have had a published model for
# years. That is worse than the legacy format.
#
# A list rather than a single format, because the archive holds several: legacy
# `.splat`, and `.sog` + `.spz` for everything reconstructed since 5.4. Stated
# as the viewer's capability rather than as a rewrite direction, because that is
# the fact the decision turns on -- change the viewer, change this, and both the
# rewrites and `_artifact_url` follow.
VIEWER_ARTIFACT_FORMATS = (".splat", ".ply", ".glb")

DEMO_ARTIFACT_REWRITES = (
    _demo_sog_rewrites() if ".sog" in VIEWER_ARTIFACT_FORMATS else []
)

COLMAP_PATH = env("COLMAP_PATH", "colmap")
FRGS_PATH = env("FRGS_PATH", "frgs")
SPLAT_TRANSFORM_PATH = env("SPLAT_TRANSFORM_PATH", "splat-transform")
COLMAP_WORKSPACE_DIR = f"{PROD_DIR}colmap_ws"
ALICEVISION_DIRPATH = env("ALICEVISION_DIRPATH", str(BASE_DIR / "bin21"))
OBJ2GLTF_PATH = env("OBJ2GLTF_PATH", "obj2gltf")
GLTFPACK_PATH = env("GLTFPACK_PATH", "gltfpack")
VGGT_ENABLE = False
VGGT_SCRIPT_PATH = ""
VGGT_ENV_PATH = ""
ALPHA_CULL_THRESH = 0.005
MESHOPS_MIN_IMAGES = env_int("MESHOPS_MIN_IMAGES", 25)
MESHOPS_MAX_IMAGES = env_int("MESHOPS_MAX_IMAGES", 1000)
MIN_MATCHED_IMAGES = 5
MIN_MATCH_RATIO = 0.10
ARK_NAAN = env("ARK_NAAN", "74218")
ARK_SHOULDER = env("ARK_SHOULDER", "/t1")
BASE_URL = "https://smlab.niser.ac.in/" + PRE_URL

# No worker runs here, and nothing may enqueue GPU work from a public surface.
CELERY_TASK_ALWAYS_EAGER = False
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "amqp://localhost")

DBBACKUP_STORAGE = "django.core.files.storage.FileSystemStorage"
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

# Carried over from the beta configuration: settings that only a signal or
# view reads, and which a from-scratch config is easy to miss.
ADMINS = []
CONTRIB_IGNORE_LIST = []
DATA_UPLOAD_MAX_NUMBER_FILES = env_int("DATA_UPLOAD_MAX_NUMBER_FILES", 1000)
# No outbound mail from a public demo: it has no one to notify, and a demo
# that can email real contributors is a way to spam them by accident.
EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"

FALLBACK_ARK_RESOLVER = "https://n2t.net"
MAIL_CONTRIB_TOGGLE = False
