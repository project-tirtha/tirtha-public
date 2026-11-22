"""
Contributions: the pre-upload check, the one-POST upload, the batched upload
(batches, finish, discard) and the helpers that store photographs and masks.
"""

import json
import logging
import re
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

# Local imports
from ..exif import read_exif
from ..models import Contribution, Contributor, Image, Site, SourceVideo
from ..tasks import (
    extract_video_frames_task,
    mark_ingest_enqueued,
    notify_admin_new_contribution_task,
    post_save_contrib_imageops,
)
from ..uploads import prepare_upload
from ..videoops import ACCEPTED_SUFFIXES as ACCEPTED_VIDEO_SUFFIXES
from .auth import _authenticate_user


@require_GET
def pre_upload_check(request):
    """Validates site and user before allowing upload."""
    # "mesh_vid" and "meshes_json" are the wire contract with static/js/main.js.
    # The model is Site now, but renaming the request and response keys would
    # break the current frontend for no lasting gain -- Phase 6 replaces both
    # sides at once, and renames them there (internal design notes).
    verbose_id = request.GET.get("mesh_vid")
    if not verbose_id:
        return JsonResponse(
            {"allowupload": False, "blur": False, "output": "Site ID is required."}
        )

    # Authenticate user
    user_info = request.session.get("tirtha_user_info")
    if not user_info:
        return JsonResponse(
            {"allowupload": False, "blur": True, "output": "Please sign in again."}
        )

    output, contrib = _authenticate_user(user_info)
    if not contrib:
        return JsonResponse({"allowupload": False, "blur": True, "output": output})

    if contrib.banned:
        return JsonResponse(
            {
                "allowupload": False,
                "blur": True,
                "output": f"{contrib.email} has been banned. Please contact the admin.",
            }
        )

    if not contrib.active:
        return JsonResponse(
            {
                "allowupload": False,
                "blur": True,
                "output": f"{contrib.email} has not been activated. Please contact the admin.",
            }
        )

    # Validate site. `exclude(hidden=True)` is the same gate `index`,
    # `search`, `api._visible_sites` and `shell` already apply -- without it
    # this endpoint answered `allowupload: true` for the 23 hidden sites that
    # are not `completed`, which is both an existence oracle and the door to
    # `upload`.
    try:
        site = Site.objects.exclude(hidden=True).get(verbose_id__exact=verbose_id)
    except Site.DoesNotExist:
        return JsonResponse(
            {
                "allowupload": False,
                "blur": False,
                "output": "Specified model was not found in database.",
            }
        )

    if site.completed:
        return JsonResponse(
            {
                "allowupload": False,
                "blur": False,
                "output": "This model is not accepting contributions at the moment.",
            }
        )

    return JsonResponse({"allowupload": True, "output": "Site found!"})


class UploadRefused(Exception):
    """A batch the open contribution may not take; `status`/`code` go to the client."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def store_uploaded_images(
    contribution,
    images,
    *,
    compress: bool,
    keys=None,
    videos=(),
    open_upload: bool = False,
    limits=None,
) -> list:
    """
    Store a contribution's photographs, assigning each one a `seq`.

    `keys`, when given, runs parallel to `images`: the batched upload's
    per-file client key, stored on each row so a retried batch can skip what
    is already held. Returns the `Image` rows stored -- unreadable files are
    skipped, not raised.

    `videos` (`(file, key)` pairs) are written and inserted alongside, inside
    the same transaction. `open_upload` is the batched upload: the
    contribution's row is locked with the inserts, and the batch is refused
    with `UploadRefused` -- nothing kept -- if the upload was finalized or
    deleted since the caller looked (a `finish`, a `discard` or the sweep
    landing mid-request), or if it would pass `limits`
    (`{"files": n, "bytes": n}`, the per-contribution caps). The contribution
    row is locked *before* the site row, the order `finalize_contribution`
    takes them in.

    Extracted from `upload` so it can be tested at all: the sequence rule below
    is the kind of thing that is easy to get wrong and impossible to check
    through a view that needs a session, a validator and a CSRF token first.

    `seq` is assigned here, not left for the workspace to guess.

    internal design notes describes `seq` as "a per-site monotonic counter ... assigned
    at ingest", and `videoops.extract_video_frames` does exactly that for video
    frames. Stills uploaded through the site got none, so every one of them fell
    back to `enumerate(reconstructable_images(site))` in `workspace.image_refs`
    -- an index over a queryset, which shifts the moment a moderator changes a
    verdict. COLMAP keys on the relative link path and links are never removed,
    so the same photograph re-registers under a new name after moderation.

    Taken under a row lock on the site, because two contributors uploading at
    once would otherwise read the same maximum and write the same `seq`.

    **Only the reservation and the inserts are under that lock**
    (review 2026-11-22 §5). Decoding, HEIC conversion and writing each file
    happen first, one file at a time, so one uploader's thousand HEICs no
    longer block every other write to the site's row -- including the
    pipeline's. Each file is written as soon as it is prepared, so the request
    never holds more than one decoded image in memory.

    The EXIF pass is outside the lock too: it re-opens every stored file and
    needs nothing the lock protects.
    """
    prepared = []
    prepared_videos = []
    received_bytes = 0
    pairs = (
        zip(images, keys, strict=True)
        if keys is not None
        else ((image, None) for image in images)
    )
    try:
        for image, key in pairs:
            try:
                stored = prepare_upload(image.read(), image.name, compress=compress)
            except Exception as exc:  # noqa: BLE001
                logging.warning(f"Rejecting unreadable upload {image.name}: {exc}")
                continue
            obj = Image(contribution=contribution, is_compressed=stored.compressed)
            obj.client_key = key
            obj.original_filename = image.name[:255]
            # Both sizes, not just the stored one.
            #
            # `original_file_size` was never written, so it is NULL for all
            # 19,418 images in the archive and the admin's "compressed" column
            # had nothing to compare against. That matters more now that full
            # resolution is the default: the only way to tell whether a
            # contribution actually arrived untouched is to see the two numbers
            # agree. `image.size` is the length as received, which differs from
            # `stored.data` whenever HEIC was converted or `compress` was asked
            # for; reading the file first does not disturb it.
            obj.original_file_size = image.size
            obj.uploaded_file_size = len(stored.data)
            # The file now, the row later: `save=False` writes to storage only.
            obj.image.save(stored.filename, ContentFile(stored.data), save=False)
            prepared.append((obj, image.name))
            received_bytes += image.size or 0

        for video, key in videos:
            source = SourceVideo(contribution=contribution, client_key=key)
            source.video.save(video.name, video, save=False)
            prepared_videos.append(source)
            received_bytes += video.size or 0

        with transaction.atomic():
            if open_upload:
                _admit_into_open_upload(
                    contribution,
                    files=len(prepared) + len(prepared_videos),
                    size=received_bytes,
                    limits=limits,
                )
            site = Site.objects.select_for_update().get(ID=contribution.site_id)
            highest = (
                Image.objects.filter(contribution__site=site)
                .exclude(seq__isnull=True)
                .order_by("-seq")
                .values_list("seq", flat=True)
                .first()
            )
            next_seq = (highest + 1) if highest is not None else 0
            for obj, _ in prepared:
                obj.seq = next_seq
                next_seq += 1
                obj.save()
            for source in prepared_videos:
                source.save()
    except BaseException:
        # Files with no row are invisible to every tool; do not leave them.
        #
        # *Every* prepared file, not only those whose object still looks
        # unsaved: a row saved inside the transaction above is rolled back with
        # it -- by an IntegrityError on a later row, by `UploadRefused`, or by
        # the COMMIT itself failing -- yet its `_state.adding` already says
        # False. Nothing has committed when we get here, so no file is in use.
        for field in [obj.image for obj, _ in prepared] + [
            source.video for source in prepared_videos
        ]:
            if field:
                try:
                    field.delete(save=False)
                except Exception:  # noqa: BLE001
                    logging.warning(f"Could not remove orphan file {field.name}")
        raise
    stored_objects = prepared

    for obj, source_name in stored_objects:
        # EXIF, read here rather than only by the backfill command.
        #
        # `date_taken`, `camera_make`, `camera_model` and `focal_length` were
        # added in 2.6 and nothing ever filled them at ingest -- all 19,394
        # images in the archive read empty. Two things depend on them: `seq`
        # orders by capture time (2.9), and COLMAP groups photographs into
        # cameras by what they were taken with.
        #
        # Read from the stored bytes, so it reflects what was actually kept.
        # A header read, no decode -- and wrapped, because a photograph with
        # unreadable EXIF is still a photograph and must not fail the upload.
        try:
            found = read_exif(Path(obj.image.path))
            if found:
                for field, value in found.items():
                    setattr(obj, field, value)
                obj.save(update_fields=list(found))
        except Exception as exc:  # noqa: BLE001
            logging.warning(f"Could not read EXIF from {source_name}: {exc}")
    return [obj for obj, _ in stored_objects]


def _admit_into_open_upload(contribution, *, files: int, size: int, limits) -> None:
    """
    Under the open contribution's row lock: may this batch join it?

    Raises `UploadRefused` if the upload was finalized or deleted meanwhile, or
    if the batch would take it past `limits`; otherwise records the bytes. Must
    run inside the transaction that inserts the batch's rows.
    """
    locked = (
        Contribution.objects.select_for_update()
        .filter(ID=contribution.ID)
        .values("finalized_at", "received_bytes")
        .first()
    )
    if locked is None or locked["finalized_at"] is not None:
        raise UploadRefused(409, "finalized", "This upload is already complete.")
    if limits:
        held = (
            Image.objects.filter(contribution_id=contribution.ID).count()
            + SourceVideo.objects.filter(contribution_id=contribution.ID).count()
        )
        if held + files > limits["files"]:
            raise UploadRefused(
                413,
                "too_many_files",
                f"One upload can hold at most {limits['files']} files.",
            )
        if locked["received_bytes"] + size > limits["bytes"]:
            raise UploadRefused(
                413,
                "too_large",
                f"One upload can hold at most {_gib(limits['bytes'])}.",
            )
    Contribution.objects.filter(ID=contribution.ID).update(
        received_bytes=F("received_bytes") + size
    )


def _gib(n: int) -> str:
    return f"{n / 1024**3:.1f} GB" if n >= 1024**3 else f"{n / 1024**2:.0f} MB"


def attach_masks(site, masks, *, scope=None, source="contributor") -> dict:
    """
    Match uploaded masks to the images they describe, by filename.

    **A mask only means something against one specific image.** It is a stencil
    in that image's pixel coordinates, so pairing it with the wrong photograph
    is not a degraded result, it is a wrong one -- the projection samples the
    subject where the subject is not, and the vote in
    `tirtha_recon.gaussian_cut` reads the building as background.

    The pairing is by name, which is the only handle a contributor has:

      * `IMG_0117.png` masks `IMG_0117.jpeg`  -- stem against stem
      * `IMG_0117.jpeg.png` masks it too      -- COLMAP's own convention, the
        one `workspace.materialise_masks` already writes
      * `<image uuid>.png` masks that image exactly, which is how a mask
        supplied *after* the contribution names its target unambiguously

    `scope` restricts the search to the images of one contribution -- the case
    where masks arrive with their photographs. Without it the whole site is
    searched, which is the "or later" case.

    Ambiguity is refused, never guessed. Two contributors uploading their own
    `IMG_0001.jpg` to the same site is ordinary, and picking either one would
    silently attach a mask to a stranger's photograph.

    Returns a report rather than raising: one bad mask in twenty should not
    discard the nineteen, and the contributor needs to be told which.
    """
    from PIL import Image as PILImage

    from ..uploads import MaskRejected, prepare_mask

    candidates = (
        list(scope)
        if scope is not None
        else list(Image.objects.filter(contribution__site=site))
    )

    # Every name a mask may legitimately use, to the images answering to it.
    # A list per key, because collisions are the thing being detected.
    index: dict[str, list] = {}
    for img in candidates:
        original = (img.original_filename or "").strip()
        keys = {str(img.ID).lower()}
        if original:
            keys.add(original.lower())
            keys.add(Path(original).stem.lower())
        for key in keys:
            index.setdefault(key, []).append(img)

    attached, rejected = 0, {}
    for upload_file in masks:
        stem = Path(upload_file.name).stem.lower()
        matches = index.get(stem, [])
        if not matches:
            rejected[upload_file.name] = (
                "no image in this contribution has that name. A mask must be "
                "named after the image it belongs to."
                if scope is not None
                else "no image at this site has that name."
            )
            continue
        if len(matches) > 1:
            rejected[upload_file.name] = (
                f"{len(matches)} images share that name, so this mask is "
                "ambiguous. Name it after the image's id instead."
            )
            continue

        target = matches[0]
        try:
            with PILImage.open(target.image.path) as handle:
                expected = handle.size
        except Exception as exc:  # noqa: BLE001
            rejected[upload_file.name] = f"could not read the image it matches ({exc})."
            continue

        try:
            data = prepare_mask(
                upload_file.read(), upload_file.name, expected_size=expected
            )
        except MaskRejected as exc:
            rejected[upload_file.name] = str(exc).split(": ", 1)[-1]
            continue

        target.mask.save(f"{target.ID}.png", ContentFile(data), save=False)
        target.mask_source = source
        target.save(update_fields=["mask", "mask_source"])
        attached += 1

    return {"attached": attached, "rejected": rejected}


@require_POST
def upload(request):
    """
    Handles image upload and creates contribution

    """
    # Authenticate user.
    #
    # **The `not user_info` check is the whole of the authorisation here.**
    # `_authenticate_user` answers "which contributor is this?", and with
    # `GOOGLE_LOGIN` off it answers "the admin" *before* it looks at the
    # session at all. This view was the only one of its four callers that
    # skipped this guard -- `pre_upload_check`, `index` and `api.session` all
    # have it -- so on any deployment with the flag off, an anonymous POST
    # created a Contribution attributed to the project admin, wrote the files,
    # and queued GPU work. `GOOGLE_LOGIN` is False in the shipped
    # `tirtha.env.example` and is the default in `local_settings.manual.py`.
    user_info = request.session.get("tirtha_user_info")
    if not user_info:
        return JsonResponse({"output": "Please sign in again."})

    output, contrib = _authenticate_user(user_info)
    if not contrib:
        return JsonResponse({"output": output})

    if contrib.banned or not contrib.active:
        return JsonResponse({"output": "Account not authorized for uploads."})

    # Batched uploads share this endpoint and everything above it; from here on
    # they have their own rules (`_upload_batch`). Without `batched=1` the
    # request is the one-POST upload, unchanged.
    if _is_batched(request):
        return _upload_batch(request, contrib)

    # Throttled, and keyed on the contributor rather than the address.
    #
    # This was the one mutating endpoint with no limit at all, and it is the
    # most expensive thing an authenticated caller can do: it writes files,
    # fully decodes every image to validate it, reads EXIF, and holds one of
    # four gunicorn workers for the length of the transfer. nginx allows a 1 GB
    # body, so a handful of concurrent requests is enough to occupy the whole
    # web tier.
    #
    # Placed after authentication so there is an identity to key on, and so an
    # unauthenticated flood is refused by the cheaper check above first.
    #
    # A batch of photographs -- any number of files -- is *one* request, so a
    # person doing real work makes a few of these an hour and will never see
    # this. The limit exists for the script that does not stop.
    from ..api import (
        DEFAULT_UPLOAD_RATE_LIMIT,
        DEFAULT_UPLOAD_RATE_WINDOW,
        _rate_limited,
    )

    throttled = _rate_limited(
        request,
        "upload",
        limit=getattr(settings, "UPLOAD_RATE_LIMIT", DEFAULT_UPLOAD_RATE_LIMIT),
        window=getattr(settings, "UPLOAD_RATE_WINDOW", DEFAULT_UPLOAD_RATE_WINDOW),
        identity=str(contrib.ID),
    )
    if throttled is not None:
        return throttled

    # Get site
    verbose_id = request.POST.get("mesh_vid")
    if not verbose_id:
        return JsonResponse({"output": "Site ID is required."})

    try:
        # Hidden sites are not contributable, same filter as `pre_upload_check`.
        # `pre_upload_check` is advisory -- nothing forces a client through it --
        # so the gate has to be repeated on the write.
        site = Site.objects.exclude(hidden=True).get(verbose_id__exact=verbose_id)
    except Site.DoesNotExist:
        return JsonResponse({"output": "Site not found."})

    if site.completed:
        return JsonResponse({"output": "This site is not accepting contributions."})

    # Files. Images and videos arrive on the same request: a contributor
    # walking around a site may have both, and making them two uploads would
    # split one visit into two contributions (internal design notes).
    images = request.FILES.getlist("images")
    videos = request.FILES.getlist("videos")
    # Masks may arrive with the photographs they describe, or long afterwards
    # for images already at this site (internal design notes). A mask-only request is
    # therefore legitimate and creates no Contribution: nothing was
    # contributed, an existing image was annotated.
    masks = request.FILES.getlist("masks")
    if not images and not videos and not masks:
        return JsonResponse({"output": "No images, videos or masks provided."})

    if masks and not images and not videos:
        # Scoped to this contributor's own photographs at this site.
        #
        # Unscoped, the candidate set was every image at the site, so any
        # signed-in contributor could attach or replace the mask on a
        # stranger's photograph -- by UUID always, by filename stem whenever
        # it was unique. A mask is not decoration: `gaussian_cut` votes with
        # it and `materialise_masks` hands it to COLMAP, so an all-black PNG
        # of the right size removes that photograph from every future
        # reconstruction. It also overwrote `mask_source`, including "admin".
        #
        # The docstring already refused *ambiguity*; this refuses *ownership*.
        # Masks arriving with their photographs are unaffected -- that path
        # passes an explicit `scope`.
        own = Image.objects.filter(
            contribution__site=site, contribution__contributor=contrib
        )
        report = attach_masks(site, masks, scope=own, source="contributor")
        if not report["attached"]:
            return JsonResponse({"output": "No mask could be matched.", **report})
        return JsonResponse(
            {
                "status": "Success",
                "output": f"Attached {report['attached']} mask(s).",
                **report,
            }
        )

    for video in videos:
        suffix = Path(video.name).suffix.lower()
        if suffix not in ACCEPTED_VIDEO_SUFFIXES:
            return JsonResponse(
                {
                    "output": (
                        f"{video.name}: only "
                        f"{', '.join(sorted(ACCEPTED_VIDEO_SUFFIXES))} videos are "
                        "accepted."
                    )
                }
            )

    # Full resolution is the default; compression is opt-in (internal design notes).
    # The parameter is inverted from the old one deliberately -- a client that
    # forgets to send it now gets the *better* behaviour, where before it got
    # the lossy one.
    compress = request.POST.get("compress", "").lower() in ("1", "true", "yes")

    contribution = Contribution.objects.create(site=site, contributor=contrib)

    store_uploaded_images(contribution, images, compress=compress)

    # Scoped to this contribution's images: a mask arriving alongside its
    # photographs describes one of *those*, and searching the whole site would
    # let `IMG_0001.jpg` match a stranger's upload from last year.
    mask_report = (
        attach_masks(site, masks, scope=contribution.images.all(), source="contributor")
        if masks
        else {"attached": 0, "rejected": {}}
    )

    sources = []
    for video in videos:
        source = SourceVideo(contribution=contribution)
        source.video.save(video.name, video, save=True)
        sources.append(source)

    if not contribution.images.exists() and not contribution.videos.exists():
        contribution.delete()
        return JsonResponse({"output": "No usable files were uploaded."})

    # A targeted update, not `site.save()` (review 2026-11-22 §1 #8). `site`
    # was loaded at the top of this request, and a full save wrote every
    # column back -- reverting anything an admin or the pipeline changed while
    # the upload streamed in (`hidden`, the name, the status a run had just
    # set). All this wanted was to bump `updated_at`, which orders the list.
    # (`contribution.save()` went too: Contribution has no auto-updated field,
    # so it wrote nothing new.)
    Site.objects.filter(ID=site.ID).update(updated_at=timezone.now())

    # Background work, enqueued once the rows are committed, in one guarded
    # block (review 2026-11-22 §4).
    #
    # Video extraction was enqueued outside the `try`, and a failure part-way
    # deleted the contribution even when ImageOps had already been queued for
    # it -- the task then ran against a row that no longer existed. Now the
    # contribution is deleted only if *nothing* was queued for it.
    #
    # ImageOps is skipped when the contribution is video-only:
    # `extract_video_frames_task` enqueues the checks itself once frames exist.
    has_images = contribution.images.exists()
    outcome = {}

    def _enqueue():
        # The failure handling lives in here, so it is correct whether this
        # runs at once (autocommit, as today) or after an outer transaction.
        queued = []
        try:
            for source in sources:
                extract_video_frames_task.delay(str(source.ID))
                queued.append(f"video {source.ID}")
            if has_images:
                post_save_contrib_imageops.delay(str(contribution.ID))
                queued.append("imageops")
            if queued:
                mark_ingest_enqueued(contribution.ID)
            return
        except Exception as exc:  # noqa: BLE001 - handled here
            failure = exc

        logging.error(
            f"Failed to enqueue processing for {contribution.ID} "
            f"(already queued: {queued or 'nothing'}): {failure}"
        )
        try:
            from ..email_utils import send_image_processing_failure_email

            send_image_processing_failure_email(
                contribution_id=str(contribution.ID),
                site_id=str(site.ID),
                site_name=site.name,
                contributor_email=contrib.email,
                error_message=f"Failed to trigger image processing task: {failure}",
            )
        except Exception as email_error:
            logging.error(
                f"Failed to send task trigger failure notification: {email_error}"
            )

        if queued:
            # Something is already working on this contribution, so it stays.
            # `requeue_stuck_contributions_task` picks up whatever was not
            # queued.
            logging.error(
                f"Keeping {contribution.ID}: {', '.join(queued)} already queued; "
                "the periodic sweep will re-enqueue the rest."
            )
            return
        logging.error("Deleting contribution: nothing was queued for it.")
        contribution.delete()
        outcome["deleted"] = True

    transaction.on_commit(_enqueue)

    if outcome.get("deleted"):
        return JsonResponse(
            {"status": "Error", "output": "Failed to process images. Please try again."}
        )

    # Here, not from `post_save` on Contribution: the images exist by now, so
    # the count in the mail is the real one. Best-effort -- a lost admin mail
    # must not undo a contribution.
    def _notify():
        try:
            notify_admin_new_contribution_task.delay(str(contribution.ID))
        except Exception as exc:  # noqa: BLE001
            logging.error(f"Could not queue the admin notification: {exc}")

    transaction.on_commit(_notify)

    return JsonResponse(
        {
            "status": "Success",
            "output": "Successfully uploaded. Thank you!",
            # Reported rather than raised: one unmatched mask should not
            # discard nineteen good photographs, but the contributor is the
            # only person who can rename it, so they have to be told.
            "masks_attached": mask_report["attached"],
            "masks_rejected": mask_report["rejected"],
        }
    )


# --------------------------------------------------------------- batched upload
#
# A contribution of 100+ phone photographs and a few videos used to travel in
# one multipart POST of up to ~900 MB: one dropped connection lost all of it,
# and a retry started again from zero. Batched, the client sends ~20 files per
# request into one *open* contribution, then calls `upload/finish/`:
#
#   POST upload/           batched=1, mesh_vid, files, image_keys/video_keys
#                          -> 200 {contribution_id, received, rejected, count}
#   POST upload/           ... plus contribution_id   (append)
#   POST upload/finish/    {contribution_id}  -> finalize, enqueue ingest + mail
#   POST upload/discard/   {contribution_id}  -> delete an open contribution
#                          {mesh_vid, keys}   -> the same, for a Cancel during
#                                                the first batch (no id yet)
#
# Nothing downstream sees an open contribution (`finalized_at IS NULL`), and
# `tasks.finalize_abandoned_uploads_task` closes one whose client went away.
#
# Volume caps (all settings, defaults in `api.py`): files and bytes per
# contribution, bytes per batch, and open uploads per contributor. Each is
# refused with its own `error` code so the client can say which.

_CLIENT_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# A discard-by-keys names at most one batch's worth of keys; more is a misuse.
_MAX_DISCARD_KEYS = 200


def _is_batched(request) -> bool:
    return request.POST.get("batched", "").lower() in ("1", "true", "yes")


def _batch_error(status: int, code: str, message: str, **extra) -> JsonResponse:
    """Batched endpoints answer with real status codes; `output` is for people."""
    return JsonResponse(
        {"status": "Error", "error": code, "output": message, **extra}, status=status
    )


def _upload_setting(name: str) -> int:
    from .. import api

    return int(getattr(settings, name, getattr(api, f"DEFAULT_{name}")))


def _batch_throttle(request, contrib):
    from ..api import _rate_limited

    return _rate_limited(
        request,
        "upload_batch",
        limit=_upload_setting("UPLOAD_BATCH_RATE_LIMIT"),
        window=_upload_setting("UPLOAD_BATCH_RATE_WINDOW"),
        identity=str(contrib.ID),
    )


def _contribution_throttle(request, contrib):
    """The one-upload-per-contribution limit, charged when one is created."""
    from ..api import _rate_limited

    return _rate_limited(
        request,
        "upload",
        limit=_upload_setting("UPLOAD_RATE_LIMIT"),
        window=_upload_setting("UPLOAD_RATE_WINDOW"),
        identity=str(contrib.ID),
    )


def _contribution_limits() -> dict:
    return {
        "files": _upload_setting("UPLOAD_MAX_FILES_PER_CONTRIBUTION"),
        "bytes": _upload_setting("UPLOAD_MAX_BYTES_PER_CONTRIBUTION"),
    }


def _batch_too_large(request, images, videos, masks):
    """
    The per-batch byte cap, or None when the batch is within it.

    Read from `CONTENT_LENGTH` and from the parsed files both: the header is
    there before anything is decoded or stored, the file sizes cover a body
    sent without one. (The CSRF middleware has already parsed the body by the
    time any view runs, so this saves the decoding and the disk, not the
    transfer; nginx bounds that.)

    **A batch of one video and nothing else is exempt**, as it was before this
    cap existed: the client sends a file over its batch size on its own, and a
    video is never split. Its ceiling stays what it was -- the client's
    per-video limit and nginx's body limit on this location.
    """
    cap = _upload_setting("UPLOAD_MAX_BYTES_PER_BATCH")
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        declared = 0
    files = sum(f.size or 0 for f in (*images, *videos, *masks))
    if max(declared, files) <= cap:
        return None
    if len(videos) == 1 and not images and not masks:
        return None
    return _batch_error(413, "too_large", f"One batch can carry at most {_gib(cap)}.")


def _client_keys(request, field: str, files: list):
    """
    The per-file keys for `files`, in the same order, or None when invalid.

    Optional -- a batch without keys is stored, it just cannot be deduplicated
    on retry -- but when present there must be exactly one well-formed key per
    file, or the pairing is meaningless.
    """
    keys = request.POST.getlist(field)
    if not keys:
        return [None] * len(files)
    if len(keys) != len(files) or not all(_CLIENT_KEY.match(k) for k in keys):
        return None
    return keys


def _open_upload_ttl() -> timedelta:
    from ..tasks import upload_open_ttl_minutes

    return timedelta(minutes=upload_open_ttl_minutes())


def _signed_in_contributor(request):
    """(contributor, None) or (None, error response) -- `upload`'s own checks."""
    user_info = request.session.get("tirtha_user_info")
    if not user_info:
        return None, _batch_error(401, "signed_out", "Please sign in again.")
    output, contrib = _authenticate_user(user_info)
    if not contrib:
        return None, _batch_error(401, "signed_out", output)
    if contrib.banned or not contrib.active:
        return None, _batch_error(
            403, "not_authorized", "Account not authorized for uploads."
        )
    return contrib, None


def _open_uploads_holding(contrib, site, keys, *, since=None):
    """
    This contributor's open uploads at `site` holding any of `keys`, newest
    first -- only those touched since `since`, when given.
    """
    queryset = (
        Contribution.objects.filter(
            contributor=contrib, site=site, finalized_at__isnull=True
        )
        .filter(Q(images__client_key__in=keys) | Q(videos__client_key__in=keys))
        .annotate(touched=Coalesce(F("last_batch_at"), F("contributed_at")))
    )
    if since is not None:
        queryset = queryset.filter(touched__gte=since)
    return queryset.distinct().order_by("-contributed_at")


def _upload_batch(request, contrib):
    """One batch of a batched upload. See the block comment above."""
    throttled = _batch_throttle(request, contrib)
    if throttled is not None:
        return throttled

    images = request.FILES.getlist("images")
    videos = request.FILES.getlist("videos")
    masks = request.FILES.getlist("masks")
    too_large = _batch_too_large(request, images, videos, masks)
    if too_large is not None:
        return too_large

    verbose_id = request.POST.get("mesh_vid")
    if not verbose_id:
        return _batch_error(400, "no_site", "Site ID is required.")
    try:
        # The same gates as the one-POST upload, on every batch: a site hidden
        # or closed mid-upload stops taking files.
        site = Site.objects.exclude(hidden=True).get(verbose_id__exact=verbose_id)
    except Site.DoesNotExist:
        return _batch_error(404, "no_site", "Site not found.")
    if site.completed:
        return _batch_error(
            403, "site_closed", "This site is not accepting contributions."
        )

    if not images and not videos:
        return _batch_error(400, "no_files", "No images or videos provided.")
    for video in videos:
        if Path(video.name).suffix.lower() not in ACCEPTED_VIDEO_SUFFIXES:
            return _batch_error(
                400,
                "bad_video",
                f"{video.name}: only "
                f"{', '.join(sorted(ACCEPTED_VIDEO_SUFFIXES))} videos are accepted.",
            )
    image_keys = _client_keys(request, "image_keys", images)
    video_keys = _client_keys(request, "video_keys", videos)
    if image_keys is None or video_keys is None:
        return _batch_error(
            400, "bad_keys", "Each file needs exactly one well-formed key."
        )
    batch_keys = [k for k in (*image_keys, *video_keys) if k]
    compress = request.POST.get("compress", "").lower() in ("1", "true", "yes")
    now = timezone.now()
    ttl = _open_upload_ttl()
    limits = _contribution_limits()
    # Cheap early refusal of a batch that could never fit; the authoritative
    # check is under the row lock, in `store_uploaded_images`.
    if len(images) + len(videos) > limits["files"]:
        return _batch_error(
            413,
            "too_many_files",
            f"One upload can hold at most {limits['files']} files.",
        )

    contribution_id = request.POST.get("contribution_id")
    if contribution_id:
        try:
            contribution_id = uuid.UUID(str(contribution_id))
        except ValueError:
            return _batch_error(404, "not_found", "Upload not found.")
        with transaction.atomic():
            contribution = (
                Contribution.objects.select_for_update()
                .filter(ID=contribution_id)
                .first()
            )
            if contribution is None:
                return _batch_error(404, "not_found", "Upload not found.")
            if contribution.contributor_id != contrib.ID:
                return _batch_error(
                    403, "forbidden", "This upload belongs to someone else."
                )
            if contribution.site_id != site.ID:
                return _batch_error(
                    403, "wrong_site", "This upload belongs to a different site."
                )
            if contribution.finalized_at is not None:
                return _batch_error(
                    409, "finalized", "This upload is already complete."
                )
            touched = contribution.last_batch_at or contribution.contributed_at
            if touched < now - ttl:
                return _batch_error(
                    409,
                    "expired",
                    "This upload was idle for too long. Please start again.",
                )
            # Touched *before* the files are written, so the abandoned-upload
            # sweep cannot close it while this batch is still being stored.
            #
            # And acknowledged: a request naming this id proves the client
            # received the answer that carried it. The sweep finalizes only an
            # upload with at least one acknowledgement (see
            # `Contribution.confirmed_batches`).
            Contribution.objects.filter(ID=contribution.ID).update(
                last_batch_at=now, confirmed_batches=F("confirmed_batches") + 1
            )
    else:
        # A first batch whose response was lost is retried as a first batch.
        # Without this it would open a second contribution: if an open one of
        # this contributor's, at this site and still within the TTL, already
        # holds any of these keys, this is that upload resuming.
        contribution = None
        if batch_keys:
            contribution = _open_uploads_holding(
                contrib, site, batch_keys, since=now - ttl
            ).first()
        if contribution is not None:
            Contribution.objects.filter(ID=contribution.ID).update(last_batch_at=now)
        else:
            # Only a request that opens a contribution counts against the
            # per-contribution limit; the batches have their own.
            throttled = _contribution_throttle(request, contrib)
            if throttled is not None:
                return throttled
            max_open = _upload_setting("UPLOAD_MAX_OPEN_PER_CONTRIBUTOR")
            with transaction.atomic():
                # The contributor's row serialises two first batches racing
                # each other past the count.
                Contributor.objects.select_for_update().filter(ID=contrib.ID).first()
                live = (
                    Contribution.objects.filter(
                        contributor=contrib, finalized_at__isnull=True
                    )
                    .annotate(touched=Coalesce(F("last_batch_at"), F("contributed_at")))
                    .filter(touched__gte=now - ttl)
                    .count()
                )
                if live >= max_open:
                    return _batch_error(
                        429,
                        "too_many_open",
                        f"You already have {live} uploads in progress. Finish "
                        "or cancel one before starting another.",
                    )
                contribution = Contribution.objects.create(
                    site=site, contributor=contrib, finalized_at=None, last_batch_at=now
                )
            logging.info(f"Opened batched upload {contribution.ID} for {site.ID}.")

    # Idempotency: skip any key this contribution already holds, and any
    # repeated within this batch.
    held = set(
        contribution.images.filter(client_key__in=batch_keys).values_list(
            "client_key", flat=True
        )
    ) | set(
        contribution.videos.filter(client_key__in=batch_keys).values_list(
            "client_key", flat=True
        )
    )
    seen = set(held)

    def _fresh(files, keys):
        out = []
        for file, key in zip(files, keys, strict=True):
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            out.append((file, key))
        return out

    new_images = _fresh(images, image_keys)
    new_videos = _fresh(videos, video_keys)

    try:
        store_uploaded_images(
            contribution,
            [f for f, _ in new_images],
            compress=compress,
            keys=[k for _, k in new_images],
            videos=new_videos,
            open_upload=True,
            limits=limits,
        )
    except UploadRefused as refused:
        # Finalized, discarded or swept since the checks above, or over a cap.
        # `store_uploaded_images` has already removed this batch's files.
        logging.info(f"Batch for {contribution.ID} refused: {refused.code}")
        return _batch_error(refused.status, refused.code, refused.message)
    except IntegrityError as exc:
        # The same batch arriving twice at once (a retry racing the original).
        # Nothing half-stored is left; a retry sorts out which.
        logging.warning(f"Batch for {contribution.ID} collided: {exc}")
        return _batch_error(
            503, "retry", "The server was busy with this upload. Retrying."
        )

    if masks:
        attach_masks(site, masks, scope=contribution.images.all(), source="contributor")

    Contribution.objects.filter(ID=contribution.ID).update(last_batch_at=timezone.now())
    held_now = set(
        contribution.images.filter(client_key__in=batch_keys).values_list(
            "client_key", flat=True
        )
    ) | set(
        contribution.videos.filter(client_key__in=batch_keys).values_list(
            "client_key", flat=True
        )
    )
    received = [k for k in dict.fromkeys(batch_keys) if k in held_now]
    rejected = [k for k in dict.fromkeys(batch_keys) if k not in held_now]
    return JsonResponse(
        {
            "status": "Success",
            "contribution_id": str(contribution.ID),
            "received": received,
            # Keys the server could not read (an undecodable image). Retrying
            # them will not help; the client reports them and moves on.
            "rejected": rejected,
            "count": contribution.images.count() + contribution.videos.count(),
        }
    )


def _batch_body(request) -> dict | None:
    """
    `contribution_id`, `mesh_vid` and `keys` from a JSON body or a form.

    A form is what `navigator.sendBeacon` sends on tab close: it cannot set
    headers, so its CSRF token travels as the `csrfmiddlewaretoken` field,
    which Django's middleware reads from a multipart body. None on bad JSON.
    """
    if request.content_type == "application/json":
        try:
            body = json.loads(request.body or b"{}")
        except ValueError:
            return None
        if not isinstance(body, dict):
            body = {}
        keys = body.get("keys") or []
        if not isinstance(keys, list):
            keys = []
        return {
            "contribution_id": body.get("contribution_id"),
            "mesh_vid": body.get("mesh_vid"),
            "keys": keys,
        }
    return {
        "contribution_id": request.POST.get("contribution_id"),
        "mesh_vid": request.POST.get("mesh_vid"),
        "keys": request.POST.getlist("keys"),
    }


def _batch_target(request, body):
    """
    (contributor, contribution, None) or (None, None, error) for finish and
    discard, by `contribution_id`.
    """
    contrib, error = _signed_in_contributor(request)
    if error is not None:
        return None, None, error
    throttled = _batch_throttle(request, contrib)
    if throttled is not None:
        return None, None, throttled
    if body is None:
        return None, None, _batch_error(400, "bad_request", "Malformed JSON.")

    contribution_id = body["contribution_id"]
    if not contribution_id:
        return (
            None,
            None,
            _batch_error(400, "bad_request", "contribution_id is required."),
        )
    try:
        contribution_id = uuid.UUID(str(contribution_id))
    except ValueError:
        return None, None, _batch_error(404, "not_found", "Upload not found.")

    contribution = (
        Contribution.objects.filter(ID=contribution_id).select_related("site").first()
    )
    if contribution is None:
        return contrib, None, None
    if contribution.contributor_id != contrib.ID:
        return (
            None,
            None,
            _batch_error(403, "forbidden", "This upload belongs to someone else."),
        )
    return contrib, contribution, None


@require_POST
def upload_finish(request):
    """
    Close a batched upload and start its processing. Idempotent: finishing a
    finalized contribution answers 200 and does nothing.
    """
    from ..tasks import finalize_contribution

    _, contribution, error = _batch_target(request, _batch_body(request))
    if error is not None:
        return error
    if contribution is None:
        return _batch_error(404, "not_found", "Upload not found.")

    outcome = finalize_contribution(contribution.ID, reason="finish")
    if outcome == "deleted":
        return _batch_error(400, "empty", "No usable files were uploaded.")
    if outcome == "missing":
        return _batch_error(404, "not_found", "Upload not found.")
    return JsonResponse(
        {
            "status": "Success",
            "output": "Successfully uploaded. Thank you!",
            "contribution_id": str(contribution.ID),
            "already_finalized": outcome == "already",
            "count": contribution.images.count() + contribution.videos.count(),
        }
    )


@require_POST
def upload_discard(request):
    """
    Delete an open batched upload and its files -- the client's Cancel.

    Never a finalized one: that is a contribution, and removing it is a
    moderator's decision. Discarding one that is already gone answers 200.

    By `contribution_id`, or -- for a Cancel (or a closed tab) during the
    first batch, before the client has learnt the id -- by `mesh_vid` and the
    batch's `keys`: then every open upload of the requester's own at that site
    holding any of those keys is discarded, and nobody else's ever is. A
    discard that overtakes its batch finds nothing; the batch then lands
    unacknowledged, and the sweep deletes it (`confirmed_batches`).
    """
    from ..tasks import discard_open_contribution

    body = _batch_body(request)
    if body is not None and not body["contribution_id"] and body["mesh_vid"]:
        return _discard_by_keys(request, body)

    _, contribution, error = _batch_target(request, body)
    if error is not None:
        return error
    if contribution is None:
        return JsonResponse({"status": "Success", "discarded": False})

    outcome = discard_open_contribution(contribution.ID)
    if outcome == "already":
        return _batch_error(
            409, "finalized", "This upload is complete and cannot be discarded."
        )
    if outcome == "missing":
        return JsonResponse({"status": "Success", "discarded": False})
    logging.info(f"Discarded open upload {contribution.ID}.")
    return JsonResponse({"status": "Success", "discarded": True})


def _discard_by_keys(request, body):
    from ..tasks import discard_open_contribution

    contrib, error = _signed_in_contributor(request)
    if error is not None:
        return error
    throttled = _batch_throttle(request, contrib)
    if throttled is not None:
        return throttled
    keys = body["keys"]
    if (
        not keys
        or len(keys) > _MAX_DISCARD_KEYS
        or not all(isinstance(k, str) and _CLIENT_KEY.match(k) for k in keys)
    ):
        return _batch_error(400, "bad_keys", "keys are required.")
    site = Site.objects.filter(verbose_id__exact=str(body["mesh_vid"])).first()
    if site is None:
        return JsonResponse({"status": "Success", "discarded": 0})
    # Any age: an expired one is about to be swept anyway, and the requester
    # asked for it gone.
    targets = list(
        _open_uploads_holding(contrib, site, keys).values_list("ID", flat=True)
    )
    discarded = 0
    for contribution_id in targets:
        if discard_open_contribution(contribution_id) == "deleted":
            discarded += 1
            logging.info(f"Discarded open upload {contribution_id} by its keys.")
    return JsonResponse({"status": "Success", "discarded": discarded})
