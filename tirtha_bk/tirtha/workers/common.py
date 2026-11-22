"""
Settings-derived constants for the reconstruction pipeline.

Moved verbatim from the old `workers.py`. Each `workers` submodule imports the
names it reads from here, so a test that patches one of these has to patch it
where the method reads it -- see `tests/test_recon_pipeline_fixes._patch_constant`.
"""

from pathlib import Path

from django.conf import settings

STATIC = Path(settings.STATIC_ROOT)
MEDIA = Path(settings.MEDIA_ROOT)
LOG_DIR = Path(settings.LOG_DIR)
ARCHIVE_ROOT = Path(settings.ARCHIVE_ROOT)
# Epochs, not iterations: FRGS budgets by epoch, which scales with dataset size
# where a fixed 30,000 iterations did not (internal design notes).
GS_MAX_EPOCHS = getattr(settings, "GS_MAX_EPOCHS", 300)
# The ceiling MCMC grows towards. FRGS's own default is -1, no limit, and MCMC
# grows geometrically -- see the note on `--opt.max-gaussians` in
# `tirtha_recon.frgs`.
GS_MAX_GAUSSIANS = getattr(settings, "GS_MAX_GAUSSIANS", 1_500_000)
FRGS_PATH = getattr(settings, "FRGS_PATH", "frgs")
# Which Gaussian pathway this deployment trains with (internal design notes).
#
# "frgs" is production. "splatfacto" is the v1 pathway, kept selectable rather
# than resurrected as a second stage: 5.3 deleted `GSOps.run_splatfacto` and a
# test asserts it stays deleted, because a method per backend is the shape that
# made swapping one a pipeline edit. This is a backend, and `backends.py` is
# where a pathway declares itself.
GS_BACKEND = getattr(settings, "GS_BACKEND", "frgs")
# FastGS (tirtha_recon.fastgs; benchmark 2026-11, internal notes). Selected with GS_BACKEND = "fastgs".
FASTGS_PYTHON = getattr(settings, "FASTGS_PYTHON", "/opt/fastgs/env/bin/python")
FASTGS_REPO = getattr(settings, "FASTGS_REPO", "/opt/FastGS")
# "auto" keeps training images on the GPU only when free VRAM clearly allows it, else in host RAM (same quality).
FASTGS_DATA_DEVICE = getattr(settings, "FASTGS_DATA_DEVICE", "auto")
# Hold out every 8th image and report test metrics, like Splatfacto's eval_mode=interval. Off: train on everything.
FASTGS_HOLDOUT = getattr(settings, "FASTGS_HOLDOUT", False)
# Viewer frame for FastGS output: nerfstudio-equivalent centring/scale, with "up" from align_up when True
# (nerfstudio's mean camera up is sideways on EXIF-rotated phone sets).
GS_VIEWER_ALIGN_UP = getattr(settings, "GS_VIEWER_ALIGN_UP", True)
# "aligner" (model_orientation_aligner, today) or "align_up" (tirtha_recon.align_up).
COLMAP_UP_AXIS = getattr(settings, "COLMAP_UP_AXIS", "aligner")
# Guarded completion of split sites with COLMAP 4.2.1 (tirtha_recon.completion). Off by default.
COLMAP_COMPLETION = getattr(settings, "COLMAP_COMPLETION", False)
COLMAP421_PATH = getattr(settings, "COLMAP421_PATH", "")
# Time limits (review 2026-11-22 §1 #5). Generous: they exist to free the single
# GPU slot from a hung process, not to bound a slow one. On expiry the stage's
# whole process group is killed and the run is marked Error.
COLMAP_STAGE_TIMEOUT_S = getattr(
    settings, "COLMAP_STAGE_TIMEOUT_S", 6 * 3600
)  # each COLMAP stage
FASTGS_TIMEOUT_S = getattr(
    settings, "FASTGS_TIMEOUT_S", 4 * 3600
)  # FastGS prepare, and training, each
COLMAP_COMPLETION_TIMEOUT_S = getattr(
    settings, "COLMAP_COMPLETION_TIMEOUT_S", 3 * 3600
)  # whole completion
# Every other command `_serialRunner` runs (splat-transform, obj2gltf, gltfpack).
SUBPROCESS_TIMEOUT_S = getattr(settings, "SUBPROCESS_TIMEOUT_S", 6 * 3600)
SPLATFACTO_ENV = getattr(settings, "SPLATFACTO_ENV", "/opt/conda/envs/splatfacto")
SPLATFACTO_METHOD = getattr(settings, "SPLATFACTO_METHOD", "splatfacto")
# Iterations, not epochs. splatfacto has no epoch budget, and this is the value
# every v1 run on this machine used.
GS_MAX_ITER = getattr(settings, "GS_MAX_ITER", 30000)
SPLAT_TRANSFORM_PATH = getattr(settings, "SPLAT_TRANSFORM_PATH", "splat-transform")
SPLAT_TRANSFORM_GPU = getattr(settings, "SPLAT_TRANSFORM_GPU", "0")
# "" disables decimation and publishes the full-quality SOG.
GS_DECIMATE_TARGET = getattr(settings, "GS_DECIMATE_TARGET", "25%")
GS_DECIMATE_ADAPTIVE = getattr(settings, "GS_DECIMATE_ADAPTIVE", False)
GS_EMIT_LOD = getattr(settings, "GS_EMIT_LOD", True)
# A second, viewer-readable artifact beside the `.sog` (internal design notes).
# The shipped viewer cannot read `.sog`; without this every new reconstruction
# is invisible in it.
#
# `.splat`, after a detour through `.spz`: splat-transform writes SPZ v4 and
# refuses below v3, while `@mkkellogg/gaussian-splats-3d` 0.4.7 rejects above
# v2 -- so an `.spz` sibling encoded and published and then failed to load.
# `.splat` is what the archive's 56 legacy ARKs already prove works here.
GS_EMIT_SIBLING = getattr(settings, "GS_EMIT_SIBLING", True)
GS_SIBLING_SUFFIX = ".splat"
FRGS_DEVICE = getattr(settings, "FRGS_DEVICE", "cuda:0")
MIN_MATCHED_IMAGES = settings.MIN_MATCHED_IMAGES
MIN_MATCH_RATIO = settings.MIN_MATCH_RATIO
MESHOPS_MIN_IMAGES = settings.MESHOPS_MIN_IMAGES
ALICEVISION_DIRPATH = settings.ALICEVISION_DIRPATH
VGGT_SCRIPT_PATH = settings.VGGT_SCRIPT_PATH
VGGT_ENV_PATH = settings.VGGT_ENV_PATH
OBJ2GLTF_PATH = settings.OBJ2GLTF_PATH
GLTFPACK_PATH = settings.GLTFPACK_PATH
COLMAP_PATH = settings.COLMAP_PATH
BASE_URL = settings.BASE_URL
ARK_NAAN = settings.ARK_NAAN
ARK_SHOULDER = settings.ARK_SHOULDER
MAIL_CONTRIB_TOGGLE = settings.MAIL_CONTRIB_TOGGLE
