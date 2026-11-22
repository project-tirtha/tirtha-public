"""
Reconstruction orchestration: `BaseOps`, `MeshOps`, `GSOps` and their runners.

This was one ~2,500-line `workers.py`; it is now a package, split without
changing behaviour:

    common.py   settings-derived module constants (STATIC, ARCHIVE_ROOT, ...)
    base.py     BaseOps -- run/stage tracking, errors, cancellation, publish, ARK
    meshops.py  MeshOps -- the AliceVision path
    gsops.py    GSOps   -- COLMAP, Gaussian training, post-process
    runner.py   prerun_check, ops_runner -- what the Celery tasks call

Every name the old module had is re-exported here, so `from tirtha.workers
import GSOps` and `workers.STATIC` keep working.

Patching a constant here does NOT reach the methods: each submodule imported
its own binding from `common`. Patch it in the submodule(s) that read it, as
`tests/test_recon_pipeline_fixes._patch_constant` does.
"""

# The old module's imports, re-exported so the package exposes the same names.
import json
import os
import shlex
import shutil
from datetime import datetime
from pathlib import Path
from subprocess import CalledProcessError
from typing import Optional

import pytz
from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone
from rich.console import Console
from tirtha_recon import AliceVision, Logger

from tirtha.models import ARK, Contribution, Image, Run, Site

from ..utilsark import generate_noid, noid_check_digit
from .base import BaseOps, RunCancelledError
from .common import (
    ALICEVISION_DIRPATH,
    ARCHIVE_ROOT,
    ARK_NAAN,
    ARK_SHOULDER,
    BASE_URL,
    COLMAP421_PATH,
    COLMAP_COMPLETION,
    COLMAP_COMPLETION_TIMEOUT_S,
    COLMAP_PATH,
    COLMAP_STAGE_TIMEOUT_S,
    COLMAP_UP_AXIS,
    FASTGS_DATA_DEVICE,
    FASTGS_HOLDOUT,
    FASTGS_PYTHON,
    FASTGS_REPO,
    FASTGS_TIMEOUT_S,
    FRGS_DEVICE,
    FRGS_PATH,
    GLTFPACK_PATH,
    GS_BACKEND,
    GS_DECIMATE_ADAPTIVE,
    GS_DECIMATE_TARGET,
    GS_EMIT_LOD,
    GS_EMIT_SIBLING,
    GS_MAX_EPOCHS,
    GS_MAX_GAUSSIANS,
    GS_MAX_ITER,
    GS_SIBLING_SUFFIX,
    GS_VIEWER_ALIGN_UP,
    LOG_DIR,
    MAIL_CONTRIB_TOGGLE,
    MEDIA,
    MESHOPS_MIN_IMAGES,
    MIN_MATCH_RATIO,
    MIN_MATCHED_IMAGES,
    OBJ2GLTF_PATH,
    SPLAT_TRANSFORM_GPU,
    SPLAT_TRANSFORM_PATH,
    SPLATFACTO_ENV,
    SPLATFACTO_METHOD,
    STATIC,
    SUBPROCESS_TIMEOUT_S,
    VGGT_ENV_PATH,
    VGGT_SCRIPT_PATH,
)
from .gsops import GSOps
from .meshops import MeshOps
from .runner import ops_runner, prerun_check

__all__ = [
    # classes and entry points
    "BaseOps",
    "GSOps",
    "MeshOps",
    "RunCancelledError",
    "ops_runner",
    "prerun_check",
    # constants (common.py)
    "ALICEVISION_DIRPATH",
    "ARCHIVE_ROOT",
    "ARK_NAAN",
    "ARK_SHOULDER",
    "BASE_URL",
    "COLMAP421_PATH",
    "COLMAP_COMPLETION",
    "COLMAP_COMPLETION_TIMEOUT_S",
    "COLMAP_PATH",
    "COLMAP_STAGE_TIMEOUT_S",
    "COLMAP_UP_AXIS",
    "FASTGS_DATA_DEVICE",
    "FASTGS_HOLDOUT",
    "FASTGS_PYTHON",
    "FASTGS_REPO",
    "FASTGS_TIMEOUT_S",
    "FRGS_DEVICE",
    "FRGS_PATH",
    "GLTFPACK_PATH",
    "GS_BACKEND",
    "GS_DECIMATE_ADAPTIVE",
    "GS_DECIMATE_TARGET",
    "GS_EMIT_LOD",
    "GS_EMIT_SIBLING",
    "GS_MAX_EPOCHS",
    "GS_MAX_GAUSSIANS",
    "GS_MAX_ITER",
    "GS_SIBLING_SUFFIX",
    "GS_VIEWER_ALIGN_UP",
    "LOG_DIR",
    "MAIL_CONTRIB_TOGGLE",
    "MEDIA",
    "MESHOPS_MIN_IMAGES",
    "MIN_MATCH_RATIO",
    "MIN_MATCHED_IMAGES",
    "OBJ2GLTF_PATH",
    "SPLAT_TRANSFORM_GPU",
    "SPLAT_TRANSFORM_PATH",
    "SPLATFACTO_ENV",
    "SPLATFACTO_METHOD",
    "STATIC",
    "SUBPROCESS_TIMEOUT_S",
    "VGGT_ENV_PATH",
    "VGGT_SCRIPT_PATH",
    # the old module's imports
    "ARK",
    "AliceVision",
    "CalledProcessError",
    "Console",
    "Contribution",
    "Image",
    "IntegrityError",
    "Logger",
    "Optional",
    "Path",
    "Run",
    "Site",
    "datetime",
    "generate_noid",
    "json",
    "noid_check_digit",
    "os",
    "pytz",
    "settings",
    "shlex",
    "shutil",
    "timezone",
]
