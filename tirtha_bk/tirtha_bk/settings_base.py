"""
Settings every tracked Tirtha settings module shares.

Imported with `from .settings_base import *` as the FIRST statement of:

    local_settings_dev.py      TIRTHA_DEV=1
    local_settings_demo.py     TIRTHA_DEMO=1  (the live public demo)
    local_settings_beta.py     TIRTHA_BETA=1  (the live beta)
    local_settings.manual.py   copied to local_settings.py by build/build.sh
    local_settings.docker.py   copied to local_settings.py for docker

Each of those then sets only what makes it different, overriding a value here
where it has to. **An edit here reaches every one of them** -- the demo and
beta services at their next restart, and every install built from the manual
or docker template.

Production's own `local_settings.py` (gitignored) is self-contained and does
NOT import this file, unless it was generated from a template that does.

What belongs here: a setting that every module above defines. Something only
some of them set stays in those modules, because putting it here would add it
to the others. Nothing here may depend on a per-deployment value (`PRE_URL`,
`TIRTHA_ROOT`, `PROD_DIR`, ...): those are set after this file runs, so a
path derived from them is computed in the deployment's own module.
"""

import os
from pathlib import Path

from .env import env, env_float, env_int

# This file sits beside the local_settings* modules, so this resolves to the
# same directory (`tirtha_bk/`, where manage.py lives) for all of them.
BASE_DIR = Path(__file__).resolve().parent.parent

# ------------------------------------------------------------------ app config
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "tirtha.middleware.SecurityHeadersMiddleware",
    "tirtha.middleware.ResetLanguageMiddleware",
]

# modeltranslation must precede django.contrib.admin so its admin classes are
# registered first (internal design notes).
INSTALLED_APPS = [
    "modeltranslation",
    "colorfield",
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

# Django's SecurityMiddleware also emits Cross-Origin-Opener-Policy, and it
# processes the response *after* SecurityHeadersMiddleware -- so it would
# re-add the header the embed route deliberately drops (internal design notes).
# Ours owns that header; behaviour for every non-embed path is unchanged.
SECURE_CROSS_ORIGIN_OPENER_POLICY = None

# ---------------------------------------------------------------- static files
# The built React bundle, collected as static content: Django serves the shell
# and reads Vite's manifest for the hashed filenames (internal design notes).
# Absent before `npm run build` -- the shell then degrades to meta tags only,
# which is what an unfurl crawler needs anyway.
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "static"),
    os.path.join(BASE_DIR, "..", "frontend", "dist"),
]  # JS, CSS, images, favicon


# ---------------------------------------------------------------------- logging
def rotating_file_logging(log_location, admin_log_location):
    """
    The LOGGING dict for a deployment: rotated, at INFO, two files.

    This replaced two `logging.basicConfig(level=NOTSET, filename=...)` calls,
    one in `admin.py` and one in `views.py`. `basicConfig` is a no-op once the
    root logger has a handler, and `admin.py` runs first (autodiscover), so the
    call in `views.py` never did anything: `LOG_LOCATION` was dead configuration
    and every line the application logged went to the *admin* log instead. At
    NOTSET, which means "log everything", including Django's own debug output.

    The result on production is a single unrotated `tirthaAdminDjango.log` of
    **674 MB**, while `tirthaDjango.log` -- the file an operator would open --
    has not been written since September 2024.

    `RotatingFileHandler` needs no logrotate entry and no cron: 10 MB a file,
    five kept, so the worst case is a bounded 50 MB per log. gunicorn's own
    access and error logs are separate and still want a logrotate drop-in
    (`build/logrotate-tirtha` covers them).

    A function rather than a setting because the paths are per-deployment, and
    nothing here may depend on a per-deployment value.
    """
    return {
        "version": 1,
        # False: Django's own loggers are configured below, and disabling them
        # silently swallowed request errors.
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
        },
        "handlers": {
            "app": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": log_location,
                "maxBytes": 10 * 1024 * 1024,
                "backupCount": 5,
                "formatter": "standard",
            },
            "admin": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": admin_log_location,
                "maxBytes": 10 * 1024 * 1024,
                "backupCount": 5,
                "formatter": "standard",
            },
            "console": {"class": "logging.StreamHandler", "formatter": "standard"},
        },
        "root": {"handlers": ["app"], "level": "INFO"},
        "loggers": {
            # The admin's own activity keeps its own file, which is what the two
            # separate paths were always meant to mean.
            "tirtha.admin": {
                "handlers": ["admin"],
                "level": "INFO",
                "propagate": False,
            },
            "django": {"handlers": ["app"], "level": "INFO", "propagate": False},
            # Every request logged at INFO is what filled 674 MB. Warnings and
            # above only -- a 4xx/5xx still lands here.
            "django.request": {
                "handlers": ["app"],
                "level": "WARNING",
                "propagate": False,
            },
            "django.server": {
                "handlers": ["console"],
                "level": "WARNING",
                "propagate": False,
            },
        },
    }


# ------------------------------------------------------------------------ email
# A bound on how long a synchronous send may hold the caller.
#
# `EmailBackend` opens a socket with no timeout unless it is given one, so an
# SMTP server that accepts the connection and then stops responding blocks
# whatever is sending -- and `notify_admin_new_contribution` sends from
# `transaction.on_commit`, which runs in the request thread. With four sync
# gunicorn workers, four uploads against an unresponsive mail host is the whole
# web tier. Django applies this to `socket.create_connection` and to the SMTP
# conversation that follows.
EMAIL_TIMEOUT = 10

# ------------------------------------------------------------------ languages
# internal design notes: English, Odia, Hindi. Only the columns exist at this stage;
# the translations themselves ship in Phase 7.
USE_I18N = True
LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("or", "Odia"), ("hi", "Hindi")]
MODELTRANSLATION_DEFAULT_LANGUAGE = "en"
MODELTRANSLATION_LANGUAGES = ("en", "or", "hi")
# Fall back to English rather than showing an empty field.
MODELTRANSLATION_FALLBACK_LANGUAGES = ("en",)

# --------------------------------------------------------------------- overpass
# Self-hosted OSM mirror, used to seed site coordinates and (Phase 6) to power
# the discovery layer. OPTIONAL: unset means the admin's "Find on OpenStreetMap"
# action says so and nothing else changes. Dev must not need a 15 GB mirror to
# show a marker (internal design notes). Stand one up with build/setup-overpass.sh.
#
# Keep it bound to localhost. The browser never talks to it directly -- Django
# proxies and caches (internal design notes).
OVERPASS_URL = env("OVERPASS_URL", "")
OVERPASS_TIMEOUT = 60

# ----------------------------------------------------------- discovery layer
# The map's "what is possible" overlay (internal design notes): heritage sites OSM
# knows about that we hold no reconstruction for. Read at call time in
# tirtha/api.py, never bound at import, so tests can override them.
#
# DISCOVER_MIN_ZOOM      below this the endpoint answers an empty layer with
#                        reason="zoom" and does not touch the mirror. An
#                        all-India heritage query is far too much to ship to a
#                        browser.
# DISCOVER_MAX_BBOX_SPAN degrees per side, a hard ceiling on the viewport. Zoom
#                        is a claim the client makes about itself and nothing
#                        binds it to the bbox, so zoom=14 with a bbox over the
#                        whole country would otherwise pass. Refused as a 400.
# DISCOVER_CACHE_SECONDS how long one snapped grid cell's results are reused.
#                        Discovery is the deliberately *unpinned* half of the
#                        Overpass split (internal design notes) -- a newly mapped temple
#                        appearing is the feature working -- so this is minutes,
#                        not days. Same LocMemCache caveat as the throttle below:
#                        with CACHES unconfigured each gunicorn worker keeps its
#                        own copy.
# DISCOVER_RATE_LIMIT    its own budget, separate from SITE_REQUEST_RATE_LIMIT.
#                        This endpoint proxies to a 15 GB database, but one
#                        visitor panning a map legitimately spends dozens of
#                        these in a session, where a site request is a rare
#                        deliberate act.
DISCOVER_MIN_ZOOM = env_int("DISCOVER_MIN_ZOOM", 12)
DISCOVER_MAX_BBOX_SPAN = env_float("DISCOVER_MAX_BBOX_SPAN", 2.0)  # degrees
DISCOVER_CACHE_SECONDS = env_int("DISCOVER_CACHE_SECONDS", 900)
DISCOVER_RATE_LIMIT = env_int("DISCOVER_RATE_LIMIT", 240)
DISCOVER_RATE_WINDOW = env_int("DISCOVER_RATE_WINDOW", 3600)  # seconds

# ------------------------------------------------------------------- worker
# Content safety runs on CPU via nudenet/ONNX (internal design notes); the model
# ships with the package, so there is no path to configure -- only the
# moderation threshold, which is policy rather than a constant.
NSFW_THRESHOLD = 0.5

# VGGT -- feature-flagged off, but `workers.py` reads both paths at module
# level, so they must exist.
VGGT_SCRIPT_PATH = "./tirtha/run_vggt.py"
VGGT_ENV_PATH = "../.venv"

# --------------------------------------------------------------------- FRGS
# The reconstruction stack lives in its own environment (internal design notes): it
# pins Python 3.13 / torch 2.11 / CUDA 13.0, which the web tier does not follow.
# FRGS is therefore invoked as a subprocess, by path -- `FRGS_PATH`, which each
# deployment sets.
FRGS_DEVICE = "cuda:0"
# Epochs, not iterations -- a budget that scales with dataset size, where the
# old GS_MAX_ITER=30000 did not (internal design notes).
GS_MAX_EPOCHS = 300

# --------------------------------------------------------------- splat-transform
# The tail of the GS pipeline (internal design notes). A Node CLI invoked by path,
# like OBJ2GLTF_PATH and GLTFPACK_PATH already are.
SPLAT_TRANSFORM_PATH = "splat-transform"
SPLAT_TRANSFORM_GPU = "0"
# Decimate before publishing. Measured on a 708 MB reconstruction: 34 MB as SOG
# alone, 9.3 MB decimated first. Set to "" to publish full quality.
# Full quality. Decimation is off.
#
# This was "25%", so every published artifact -- the `.sog`, the `.splat`, the
# `.compressed.ply` and the LoD set -- was encoded from a model with three
# quarters of its Gaussians deleted. Full quality was never published in any
# format; it survived only as `reconstruction.ply` inside the archived run.
# Fontaine Bartholdi's published PLY carries 7,025 vertices: 25% of the 28,099
# that survived training, and 7.8% of the 90,199 points COLMAP seeded.
#
# The 25% came from a measurement on a 708 MB reconstruction, where it was the
# difference between a download a phone would tolerate and one it would not.
# It was then applied unchanged to models three orders of magnitude smaller:
# that same site publishes a 255 KB `.sog` and a 219 KB `.splat`. Undecimated
# those are around a megabyte, which is a quarter of the JavaScript the page
# already loads -- and this is a heritage archive, where the detail is the
# point.
#
# Set it back to a percentage if a reconstruction ever gets large enough to
# need it; `workers.run_postprocess` guards the whole decimate step on this
# being truthy.
GS_DECIMATE_TARGET = ""
# Adaptive costs ~2x the time for the same output size; it buys quality on
# mixed-scale content (skies, large open scenes). A per-site judgement.
GS_DECIMATE_ADAPTIVE = False
# Chunked, progressively loaded output for mobile viewers.
# Off, because nothing publishes or reads it.
#
# `run_postprocess` encoded a chunked LoD pyramid on every GS run and
# `run_cleanup` never copied it into the served tree -- it went to the archive
# with the rest of the run directory. There are 0 published LoD sets on this
# deployment and 1 archived one, and `grep -rn lod frontend/src` finds nothing
# that would read it.
#
# So the pipeline paid for progressive streaming on every run and shipped a
# globally decimated model instead. Turned off rather than wired up: the viewer
# work it needs is the internal viewer notes, and a half-built feature that costs
# time every run is worse than one that is honestly absent.
GS_EMIT_LOD = False

# ------------------------------------------------------------------ celery queues
# Two queues (internal design notes): `ingest` is CPU-only and concurrent, `recon`
# owns the GPU and runs strictly one job at a time across the whole system.
#
# Start them as separate workers -- one process cannot have two concurrencies:
#   celery -A tirtha worker -Q ingest -c 8 --max-tasks-per-child=200 -n ingest@%h
#   celery -A tirtha worker -Q recon  -c 1 --max-tasks-per-child=1   -n recon@%h --beat
CELERY_TASK_DEFAULT_QUEUE = "ingest"
# Without this, a task routed to a queue no worker is consuming vanishes
# silently instead of failing loudly.
CELERY_TASK_CREATE_MISSING_QUEUES = True

# --------------------------------------------------------- reconstruction trigger
# Reconstruct when a site has this many new images, or has waited this long --
# whichever comes first (internal design notes). Bounds waste and latency together:
# without the day trigger a site with 4 new images waits forever; without the
# count trigger a busy site rebuilds constantly. GS training has no incremental
# mode, so each rebuild costs the full epoch budget regardless of how few
# images were added.
RECON_MIN_NEW_IMAGES = 25
RECON_MAX_WAIT_DAYS = 14

# ------------------------------------------------------------------- uploads
# Full resolution is the default; compression is opt-in (internal design notes).
UPLOAD_COMPRESS_QUALITY = 85
UPLOAD_COMPRESS_MAX_DIMENSION = 4096

# Video (internal design notes). 150-300, not 500: a bigger budget collides with the
# MESHOPS_MAX_IMAGES OOM guard and with COLMAP's poor scaling, and a two-minute
# walkaround at 300 frames is already heavily redundant.
VIDEO_FRAME_BUDGET = 200
# Photographs are megabytes; a two-minute 4K HEVC clip is several hundred.
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

# --------------------------------------------------------------------- masks
# Whether COLMAP should ignore masked-out regions during feature extraction.
# OFF: FRGS consumes masks in its training loop by default, so a mask reaches
# GS training without COLMAP's help, and whether COLMAP-stage masking also
# helps SfM -- or discards features that would have aided registration -- is
# unmeasured. That belongs in the Lab (internal design notes).
COLMAP_USE_MASKS = False

# Gaussian cut: remove the Gaussians the contributed masks disown
# (internal design notes). Off by default, on the precedent COLMAP_USE_MASKS set --
# a new stage on the reconstruction path earns its default by being measured,
# not by being written. A no-op without masks either way.
GAUSSIAN_CUT_ENABLE = False
# Masks required before the cut runs at all. One mask is an opinion about one
# view; the vote needs corroboration before it deletes anything.
GAUSSIAN_CUT_MIN_MASKS = 2
# Fraction of the views that *saw* a Gaussian in which it must be inside the
# subject to survive.
GAUSSIAN_CUT_KEEP_RATIO = 0.5
# Masked views that must see a Gaussian before it may be judged. Below this it
# is kept regardless: absence of evidence is not evidence of background.
GAUSSIAN_CUT_MIN_VOTES = 2
# Grow the subject before sampling, in pixels. Conservative: contributor-drawn
# masks tend to trace tight to the silhouette.
GAUSSIAN_CUT_DILATE_PX = 2
# Refuse the cut, keeping the original splat, if the vote wants more than this
# fraction. An inverted mask and a mask from another site both look like this.
GAUSSIAN_CUT_MAX_REMOVED = 0.9

# SAM3 mask generation (internal design notes). Its own environment: SAM3 pins
# torch 2.7 / CUDA 12.6 against the reconstruction stack's 2.11 / 13.0, so it
# is invoked as a subprocess, never imported. Unset means no generation.
SAM3_PYTHON = ""
SAM3_SCRIPT = ""

# ------------------------------------------------------------------ leaderboard
# Video frames count for less than deliberate photographs: a ten-minute clip
# yielding 200 frames must not outrank 50 considered shots (internal design notes).
# The exact figure is an open question (section 26); this is the knob.
VIDEO_FRAME_WEIGHT = 0.2

# ------------------------------------------------------------------ throttling
# Anonymous site requests are a decision, not an oversight (internal design notes):
# requiring an account to *ask* for a site would suppress exactly the local
# knowledge the endpoint exists to reach. The price is a write endpoint anyone
# can call, so it is rate-limited per IP -- this many submissions per window,
# counted in Django's cache rather than by adding a throttling dependency.
#
# The counter is only as global as CACHES is: with the default LocMemCache it
# lives inside a single process, so N gunicorn workers give an effective limit
# of N times this. Point CACHES at a shared backend for the exact number.
SITE_REQUEST_RATE_LIMIT = env_int("SITE_REQUEST_RATE_LIMIT", 5)
SITE_REQUEST_RATE_WINDOW = env_int("SITE_REQUEST_RATE_WINDOW", 3600)  # seconds
# How many rightmost X-Forwarded-For entries our own infrastructure wrote.
# nginx appends the connecting peer ($proxy_add_x_forwarded_for), so 1 is right
# behind nginx and 2 if a CDN is ever put in front of it; 0 ignores the header
# and trusts only REMOTE_ADDR. Everything to the left of that trusted tail is
# client-supplied and must not be believed -- taking the leftmost entry, the
# usual mistake, hands a flooder a fresh bucket per request.
TRUSTED_PROXY_DEPTH = env_int("TRUSTED_PROXY_DEPTH", 1)

# ------------------------------------------------------------------ artifacts
# The directory backing the `/static/` half of an ARK URL on this deployment.
# `_artifact_url` uses it to offer a converted `.sog` beside a legacy `.splat`
# (internal design notes) -- the ARK row itself is never rewritten. Unset means
# no rewriting, which is the safe default: pointing this at the wrong tree
# would offer viewers a URL that 404s.
ARTIFACT_ROOT = os.getenv("ARTIFACT_ROOT", "")
