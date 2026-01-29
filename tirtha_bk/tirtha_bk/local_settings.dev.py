"""
Development - Local Dev Settings

"""

import os
from pathlib import Path

# Development defaults
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret-key-change-me")
TIME_ZONE = "Asia/Kolkata"
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# Middleware
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "tirtha.middleware.SecurityHeadersMiddleware",
]

# Installed apps
INSTALLED_APPS = [
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

# Default attributes used to create default mesh & contributor
DEFAULT_MESH_NAME = "Meditation Center"  # Default mesh to use for new runs
DEFAULT_MESH_ID = "9zpT9kVZwP9XxAbG"
ADMIN_NAME = "Tirtha Admin"
ADMIN_MAIL = "tadmin@example.com"

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
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
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "static"),
]  # JS, CSS, images, favicon
STATIC_URL = "/" + PRE_URL + "/static/"
STATIC_ROOT = os.path.join(PROD_DIR, "static")

# Media files
MEDIA_URL = PRE_URL + "media/"
MEDIA_ROOT = os.path.join(PROD_DIR, "media")

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

# Security options relaxed for local development, swap back
GOOGLE_LOGIN = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False

## Worker-related settings
# GS
COLMAP_PATH = "colmap"  # NOTE: Ensure the binary is on system/conda env PATH
GS_MAX_ITER = 30_000
ALPHA_CULL_THRESH = 0.005  # Threshold to delete translucent gaussians - lower values remove more (usually better quality)
MIN_MATCHED_IMAGES = 5  # Minimum number of matched images required
MIN_MATCH_RATIO = 0.10  # Minimum ratio of matched images to total images

ALICEVISION_DIRPATH = BASE_DIR / "bin21"
NSFW_MODEL_DIRPATH = BASE_DIR / "nn_models/nsfw_model/mobilenet_v2_140_224/"
MANIQA_MODEL_FILEPATH = BASE_DIR / "static/artifacts/ckpt_kadid10k.pt"
OBJ2GLTF_PATH = "obj2gltf"  # NOTE: Ensure the binary is on system PATH
GLTFPACK_PATH = "gltfpack"  # NOTE: Ensure the binary is on system PATH
MESHOPS_MIN_IMAGES = 10  # Minimum number of images required to run meshops
MESHOPS_MAX_IMAGES = (
    500  # Maximum number of images to use for meshops - to avoid OOM issues
)
MESHOPS_CONTRIB_DELAY = 0.005
FILE_UPLOAD_MAX_MEMORY_SIZE = (
    10_485_760 * 2
)  # 20 MiB (each file max - post compression)
DATA_UPLOAD_MAX_NUMBER_FILES = 2_000  # 2_000 files # Max number of files per upload

## Celery
## NOTE: RabbitMQ setup skipped for local dev - tasks run eagerly
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_ACKS_LATE = True  # To prevent tasks from being lost
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
