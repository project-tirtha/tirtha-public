"""
Image checks at ingest (internal design notes).

Two independent questions, deliberately kept apart (2.8):

**Is it safe to publish?** A decision, made by a classifier, recorded in
`safety_status`. Content moderation is the one place a verdict has to be stored,
because acting on it means physically quarantining the file.

**Is it good enough to reconstruct from?** A *measurement* -- sharpness and
exposure -- recorded as numbers with no verdict attached. Whether an image is
usable is a query against those numbers, so moving a threshold re-queries
instead of relabelling every row (2.8). The ground truth is registration
anyway: an image COLMAP fails to register is a bad image, and `RunStage`
captures that per run for free (10.4).

**Nothing here touches the GPU.** That is the point of the rewrite. MANIQA is
gone (18.1) and NSFW detection is ONNX on CPU (18.2), so image checks no longer
compete with reconstruction for VRAM -- which dissolves issue #37 rather than
solving it, and lets ingest concurrency rise freely (18.3, 10.3.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from tirtha_recon import Logger

from tirtha.models import Contribution, Image

# Settings are read when they are used, not captured at import. Binding them at
# module level makes `override_settings` silently ineffective -- the module keeps
# the value it saw first -- which is how a test can pass while exercising the
# wrong directory entirely.


def _media_root() -> Path:
    return Path(settings.MEDIA_ROOT)


def _log_dir() -> Path:
    return Path(settings.LOG_DIR) / "ImageOps"


def _nsfw_threshold() -> float:
    """
    Above this, nudenet's unsafe-class score withholds the image from the site
    and quarantines it for a human. A setting because it is moderation policy,
    not a constant.
    """
    return float(getattr(settings, "NSFW_THRESHOLD", 0.5))


# nudenet reports many classes; these are the ones that decide publication.
# Listed explicitly rather than inferred, so widening the net is a visible edit.
_UNSAFE_CLASSES = frozenset(
    {
        "FEMALE_BREAST_EXPOSED",
        "FEMALE_GENITALIA_EXPOSED",
        "MALE_GENITALIA_EXPOSED",
        "BUTTOCKS_EXPOSED",
        "ANUS_EXPOSED",
    }
)


@dataclass(frozen=True)
class QualityMeasurement:
    """Numbers about one image. No verdict -- see the module docstring."""

    sharpness: float
    exposure: float
    clipped_highlights: float
    clipped_shadows: float
    mean_luma: float

    def as_remark(self) -> str:
        return (
            f"sharpness(tenengrad)={self.sharpness:.2f} "
            f"exposure={self.exposure:.4f} "
            f"clipped_hi={self.clipped_highlights:.4f} "
            f"clipped_lo={self.clipped_shadows:.4f} "
            f"mean_luma={self.mean_luma:.2f}"
        )


def measure_quality(image_path: str | Path) -> QualityMeasurement:
    """
    Sharpness and exposure, on CPU, with no model.

    **Tenengrad** (Sobel gradient energy) rather than variance-of-Laplacian,
    because `sharp-frame-extractor` uses Tenengrad to pick frames from video
    (16.4). One measure across both media types means an uploaded photo and an
    extracted frame are directly comparable; two measures would make the
    threshold mean different things depending on where the image came from.

    No sRGB linearisation: thresholds on these quantities are hard to reason
    about in linear space, and the original code made the same choice.
    """
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image: {image_path}")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    sharpness = float(np.mean(gx**2 + gy**2))

    total = gray.size
    clipped_hi = float(np.count_nonzero(gray >= 250) / total)
    clipped_lo = float(np.count_nonzero(gray <= 5) / total)
    mean_luma = float(gray.mean())

    # One number for "is this exposed usefully": the fraction of pixels that
    # retain detail. A blown-out sky and a black interior fail it the same way,
    # which is correct -- neither contributes features.
    exposure = 1.0 - (clipped_hi + clipped_lo)

    return QualityMeasurement(
        sharpness=sharpness,
        exposure=exposure,
        clipped_highlights=clipped_hi,
        clipped_shadows=clipped_lo,
        mean_luma=mean_luma,
    )


class SafetyUnavailable(RuntimeError):
    """The safety model could not be loaded."""


class NudeNetChecker:
    """
    NSFW detection on CPU via ONNX (internal design notes).

    Replaces GantMan's `nsfw_model`, which was the sole reason TensorFlow,
    Keras and silence-tensorflow were dependencies -- an entire second deep
    learning framework for one small classifier, next to the PyTorch stack the
    reconstruction tier needs anyway.

    Imported lazily so that neither the test suite nor a web process that never
    checks an image pays for onnxruntime.
    """

    def __init__(self):
        self._detector = None

    @property
    def detector(self):
        if self._detector is None:
            try:
                from nudenet import NudeDetector
            except ImportError as exc:
                raise SafetyUnavailable(
                    "nudenet is not installed. Content safety cannot be checked, "
                    "and images must not be published unchecked."
                ) from exc
            self._detector = NudeDetector()
        return self._detector

    def score(self, image_path: str | Path) -> float:
        """
        Highest confidence among the unsafe classes; 0.0 if none are present.

        A maximum rather than a sum: one confident detection should decide the
        outcome, and summing would let several low-confidence hits cross the
        threshold together.
        """
        detections = self.detector.detect(str(image_path))
        scores = [
            float(d.get("score", 0.0))
            for d in detections
            if d.get("class") in _UNSAFE_CLASSES
        ]
        return max(scores, default=0.0)


class ImageOps:
    """
    Runs the ingest checks for one contribution.

    Writes evidence, never a usability verdict: `safety_status` because
    moderation must be acted on, and the quality numbers as measurements.
    """

    def __init__(self, contrib_id: str, safety_checker=None) -> None:
        log_dir = _log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        self.logger = Logger(
            log_path=log_dir,
            name=f"{self.__class__.__name__}_{contrib_id[:8]}",
        )
        try:
            self.contribution = Contribution.objects.get(ID=contrib_id)
        except (Contribution.DoesNotExist, ValidationError, ValueError) as excep:
            # ValidationError covers a malformed ID: Contribution.ID is a UUID,
            # so the ORM rejects a non-UUID string before it ever looks for a
            # row. To a caller that is the same failure as "no such
            # contribution" and deserves the same exception.
            self.logger.error(
                f"Contribution with ID {contrib_id} could not be loaded.",
                exc_info=True,
            )
            raise ValueError(f"Contribution {contrib_id} not found.") from excep

        self.images = self.contribution.images.all()
        self.size = len(self.images)
        if self.size == 0:
            msg = f"No images found for contribution {self.contribution.ID}."
            self.logger.error(msg)
            raise ValueError(msg)

        # Injectable so tests -- and any future swap to Falconsai (18.2) -- do
        # not have to monkeypatch a module-level import.
        self.safety_checker = safety_checker or NudeNetChecker()

    def check_image(self, img: Image) -> Image:
        """
        Check one image and record what was found.

        **A moderator's decision is never overwritten** (review 2026-11-22
        §1 #6). Re-runs come from the admin trigger, an `acks_late`
        redelivery and mixed image+video uploads, and each one used to rewrite
        `safety_status` from the classifier -- so an image a moderator had
        marked NSFW became `safe`, moved back into the served tree and fed the
        next reconstruction. An image with `safety_moderated_at` set keeps its
        status; only the quality numbers are refreshed.

        The row is re-read under a lock before writing, because the classifier
        is slow and a moderator may decide while it runs.
        """
        from django.db import transaction

        path = (_media_root() / img.image.name).resolve()
        now = timezone.now()

        score = None
        if img.safety_moderated_at is None:
            score = self.safety_checker.score(path)

        try:
            quality = measure_quality(path)
            unreadable = None
        except ValueError as exc:
            # An unreadable file is a real finding, not a crash: record it and
            # let the rest of the contribution proceed.
            quality, unreadable = None, exc

        with transaction.atomic():
            fresh = Image.objects.select_for_update().get(pk=img.pk)
            if score is not None and fresh.safety_moderated_at is None:
                fresh.safety_score = score
                fresh.safety_status = (
                    "rejected" if score >= _nsfw_threshold() else "safe"
                )
                fresh.safety_checked_at = now
            if quality is None:
                fresh.remark = f"UNREADABLE -- {unreadable}"
            else:
                fresh.sharpness_score = quality.sharpness
                fresh.exposure_score = quality.exposure
                fresh.remark = quality.as_remark()
            # Tells `signals.pre_save_image` this status came from the
            # classifier, not from a person.
            fresh._safety_from_classifier = True
            # A full save: `pre_save_image` moves a rejected file into
            # quarantine and rewrites `image.name`, which `update_fields` would
            # drop.
            fresh.save()
        return fresh

    def images_to_check(self, *, recheck: bool = False):
        """
        The images this pass looks at.

        By default only those never checked and never moderated: a redelivered
        or repeated task then does nothing for images already handled, and a
        video's frames are checked without re-scoring the stills that came
        with them. `recheck=True` (an operator's explicit re-run) re-scores
        everything except moderated images' safety, which `check_image` keeps.
        """
        if recheck:
            return list(self.images)
        return [
            img
            for img in self.images
            if img.safety_checked_at is None and img.safety_moderated_at is None
        ]

    def check_images(self, *, recheck: bool = False):
        """Check the contribution's unchecked images, then mark it processed."""
        lg = self.logger
        todo = self.images_to_check(recheck=recheck)
        total = len(todo)
        lg.info(
            f"Checking {total} of {self.size} images for {self.contribution.ID}"
            f"{' (recheck)' if recheck else ''}."
        )

        rejected = 0
        unreadable = 0
        for idx, img in enumerate(todo, start=1):
            lg.info(f"Checking image {img.ID} | [{idx}/{total}]...")
            try:
                checked = self.check_image(img)
            except SafetyUnavailable:
                # Never fall through to "publish it anyway". Leaving the status
                # pending is what keeps unchecked images out of the site.
                lg.error(
                    "Safety model unavailable; aborting checks for "
                    f"{self.contribution.ID}. Images remain unchecked.",
                    exc_info=True,
                )
                raise
            except Exception:
                # One unreadable file must not cost the other four hundred.
                #
                # `NudeNetChecker.score` runs `cv2.imread`, which returns None
                # for a truncated or non-image file and then raises
                # `AttributeError: 'NoneType' object has no attribute 'shape'`.
                # Nothing caught it, so it left `check_images` entirely: every
                # image after the bad one went unchecked, the contribution was
                # never marked processed, and because `reconstructable_images`
                # counts only `safe` rows, the whole batch stayed invisible to
                # the pipeline. One corrupt upload, and the other 499
                # photographs simply never arrived.
                #
                # The row keeps `safety_status = "pending"`, which is the safe
                # direction -- unchecked is not publishable -- and says why.
                lg.error(f"Could not check image {img.ID}; skipping it.", exc_info=True)
                # `update`, not `save`: the in-memory row may be stale, and a
                # save would run `pre_save_image` against it.
                Image.objects.filter(pk=img.pk).update(
                    remark="UNCHECKED -- the safety check could not read this file"
                )
                unreadable += 1
                continue
            if checked.safety_status == "rejected":
                rejected += 1

        lg.info(
            f"Finished {self.contribution.ID}: {total - unreadable} checked, "
            f"{rejected} withheld for moderation, {unreadable} unreadable."
        )
        Contribution.objects.filter(ID=self.contribution.ID).update(
            processed=True, processed_at=timezone.now()
        )
        return self.contribution
