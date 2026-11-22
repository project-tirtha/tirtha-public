import os
from datetime import timedelta
from pathlib import Path

from celery.schedules import crontab
from celery.utils.log import get_task_logger
from django.conf import settings
from tirtha_recon import Logger

# Local imports
from .celery import app

cel_logger = get_task_logger(__name__)

# Two queues, not one (internal design notes).
#
# `ingest` is entirely CPU since 5.1 moved NSFW to ONNX and 10.3.1 keeps COLMAP
# out of ingest, so it never contends for the GPU and its concurrency can rise
# freely. `recon` takes essentially the whole machine -- GPU plus a large share
# of 64 cores and 94 GB -- so it runs **one at a time across the entire
# system**, not one per site.
#
# This replaces a single --concurrency=1 worker shared by everything, which
# forced image checks to inherit the reconstruction bottleneck (18.3). A second
# GPU host is simply another `recon` worker on the same queue.
QUEUE_INGEST = "ingest"
QUEUE_RECON = "recon"
LOG_DIR = Path(settings.LOG_DIR)
MESHOPS_MAX_IMAGES = settings.MESHOPS_MAX_IMAGES
BACKUP_INTERVAL = crontab(minute=0, hour=0)  # Every day at 00:00
DBCLEANUP_INTERVAL = crontab(
    minute=0, hour=0, day_of_week=0
)  # Every week at 00:00 on Sunday
# Reconstruction is a scheduling decision rather than a fixed delay after each
# upload (internal design notes).
#
# Hourly by default, not nightly (review 2026-11-22 §2). Nightly meant up to 24 h
# from upload to model. An hourly pass is safe because the scheduler now keeps
# its state in the database: a site already enqueued carries
# `Site.recon_queued_at` and is skipped, and a pass enqueues nothing while the
# `recon` queue already has a message waiting. `RECON_SCHEDULE_MINUTE` /
# `RECON_SCHEDULE_HOUR` are crontab fields; "30" / "1" restores the old nightly
# cadence.
RECON_SCHEDULE_INTERVAL = crontab(
    minute=str(getattr(settings, "RECON_SCHEDULE_MINUTE", "30")),
    hour=str(getattr(settings, "RECON_SCHEDULE_HOUR", "*")),
)
# Contributions whose ImageOps never finished (review 2026-11-22 §2).
IMAGEOPS_SWEEP_INTERVAL = crontab(minute=45)
# Batched uploads left open by a client that never called `upload/finish/`.
ABANDONED_UPLOAD_SWEEP_INTERVAL = crontab(minute="*/15")
# Runs left Processing by a dead worker, between worker restarts.
ORPHAN_SWEEP_INTERVAL = crontab(
    minute=f"*/{int(getattr(settings, 'RECON_ORPHAN_SWEEP_MINUTES', 15))}"
)


def _seconds(name: str, default: float) -> float | None:
    """A time-limit setting in seconds; 0 or None disables the limit."""
    value = getattr(settings, name, default)
    return float(value) if value else None


def _hard_limit(soft: float | None, name: str, grace: float) -> float | None:
    """The hard limit: explicit, or the soft limit plus a grace period."""
    explicit = getattr(settings, name, None)
    if explicit:
        return float(explicit)
    return soft + grace if soft else None


# Time limits (review 2026-11-22 §1 #5, §2).
#
# **Celery enforces these only under the prefork pool.** The units run
# `-P threads`, where Celery ignores both limits; they are declared anyway so a
# pool change makes them effective, and the age-based orphan sweep below uses
# the same numbers to decide that a run has been Processing for impossibly long.
# The pipeline's per-stage subprocess timeouts are what actually stop a hang.
RECON_SOFT_TIME_LIMIT = _seconds("RECON_SOFT_TIME_LIMIT", 30 * 3600)
RECON_TIME_LIMIT = _hard_limit(RECON_SOFT_TIME_LIMIT, "RECON_TIME_LIMIT", 3600)
VIDEO_SOFT_TIME_LIMIT = _seconds("VIDEO_SOFT_TIME_LIMIT", 3600)
VIDEO_TIME_LIMIT = _hard_limit(VIDEO_SOFT_TIME_LIMIT, "VIDEO_TIME_LIMIT", 300)
IMAGEOPS_SOFT_TIME_LIMIT = _seconds("IMAGEOPS_SOFT_TIME_LIMIT", 2 * 3600)
IMAGEOPS_TIME_LIMIT = _hard_limit(IMAGEOPS_SOFT_TIME_LIMIT, "IMAGEOPS_TIME_LIMIT", 300)
IMAGEOPS_MAX_RETRIES = int(getattr(settings, "IMAGEOPS_MAX_RETRIES", 5))
# After the nightly reconstruction pass, so a site unlocked overnight shows up
# the same morning.
LEADERBOARD_INTERVAL = crontab(minute=0, hour=6)


# `acks_late`, on the two tasks that carry a contribution.
#
# The deployed settings module is the only one of the six that omits
# `CELERY_TASK_ACKS_LATE`; dev, manual and docker all set it True with the
# comment "To prevent tasks from being lost". In production the message is
# therefore acked on *delivery*, so a worker restart between the ack and the
# work destroys it silently -- and for these two that means a contributor's
# upload is accepted, stored, and then never checked or never extracted, with
# nothing anywhere recording that it did not happen.
#
# Set per task rather than globally on purpose. Late-acking the `recon` queue
# would redeliver a killed reconstruction on the next start, which both races
# the startup sweep that has just marked that run Error and turns a task that
# reliably crashes the worker into one that crashes it for ever. These two are
# short, and re-running one is cheap and harmless.
@app.task(
    bind=True,
    queue=QUEUE_INGEST,
    acks_late=True,
    soft_time_limit=IMAGEOPS_SOFT_TIME_LIMIT,
    time_limit=IMAGEOPS_TIME_LIMIT,
    max_retries=IMAGEOPS_MAX_RETRIES,
)
def post_save_contrib_imageops(
    self, contrib_id: str, recons_type: str = "all", recheck: bool = False
) -> None:
    """
    Triggers `ImageOps`, when a `Contribution` instance is created & saved.

    Parameters
    ----------
    self : Task
        Celery task instance (when bind=True)
    contrib_id : str
        The `Contribution` instance's UUID.
    recons_type : str, optional
        Unused; kept so messages already in the broker still deserialise.
    recheck : bool, optional
        Re-score images that were already checked (an operator's explicit
        re-run). By default only unchecked images are looked at, which makes a
        redelivery or a second enqueue for the same contribution a no-op.

    **Retried, not abandoned** (review 2026-11-22 §2). `SafetyUnavailable`
    -- the model could not be loaded -- is transient by nature, and a failure
    here used to be final: the images stayed `pending` for ever and nothing
    looked at them again. It is retried with exponential backoff (1 min, 2,
    4, ... capped at 1 h), and the failure mail goes out only when the retries
    are spent. `requeue_stuck_contributions_task` is the net under that.
    """

    # Check images
    cel_logger.info(
        f"post_save_contrib_imageops (task_id={self.request.id}): Triggering ImageOps for contrib_id: {contrib_id}."
    )
    cel_logger.info(
        f"post_save_contrib_imageops (task_id={self.request.id}): Checking images for contrib_id: {contrib_id}..."
    )
    from .imageops import ImageOps, SafetyUnavailable
    from .models import Contribution

    # An open batched upload is still arriving. Checking half of it would mark
    # it `processed` and leave the rest unchecked; `finalize_contribution`
    # enqueues this task again once the last batch is in.
    if Contribution.objects.filter(ID=contrib_id, finalized_at__isnull=True).exists():
        cel_logger.info(
            f"post_save_contrib_imageops (task_id={self.request.id}): {contrib_id} "
            "is an open upload; skipping until it is finalized."
        )
        return

    try:
        iops = ImageOps(contrib_id=contrib_id)
        # The line under this used to say "Skipping image checks ... due to
        # VRAM + concurrency issues", directly above the call that runs them.
        # The FIXME it belonged to was resolved when 18.2 moved NSFW detection
        # to ONNX on the CPU; the log line outlived it and told every reader,
        # for months, that a step had been skipped when it had not.
        iops.check_images(recheck=recheck)
        cel_logger.info(
            f"post_save_contrib_imageops: Finished checking images for contrib_id: {contrib_id}."
        )
    except Exception as e:
        retries = getattr(self.request, "retries", 0) or 0
        if isinstance(e, SafetyUnavailable) and retries < (self.max_retries or 0):
            countdown = min(60 * (2**retries), 3600)
            cel_logger.warning(
                f"ImageOps for {contrib_id}: safety model unavailable ({e}); "
                f"retry {retries + 1}/{self.max_retries} in {countdown}s."
            )
            # Raises `Retry` (or, when called directly, the original error).
            raise self.retry(exc=e, countdown=countdown) from e

        cel_logger.error(f"ImageOps failed for contribution {contrib_id}: {e}")

        # Send email notification about the failure
        try:
            from .email_utils import send_image_processing_failure_email

            # Get contribution details for the email
            try:
                contribution = Contribution.objects.get(ID=contrib_id)
                contributor_email = contribution.contributor.email
                site_id = str(contribution.site.ID)
                site_name = contribution.site.name

                send_image_processing_failure_email(
                    contribution_id=contrib_id,
                    site_id=site_id,
                    site_name=site_name,
                    contributor_email=contributor_email,
                    processing_step="Image Processing (ImageOps)",
                    error_message=str(e),
                    log_file_path=str(iops.logger._log_file)
                    if "iops" in locals() and hasattr(iops.logger, "_log_file")
                    else None,
                )
            except (Contribution.DoesNotExist, AttributeError) as contrib_error:
                cel_logger.error(
                    f"Failed to get contribution details for email: {contrib_error}"
                )

        except Exception as email_error:
            cel_logger.error(
                f"Failed to send ImageOps failure notification: {email_error}"
            )

        # Re-raise the original exception
        raise e

    # Ingest ends here. Reconstruction is no longer triggered per contribution
    # (internal design notes): `schedule_reconstructions_task` decides nightly
    # whether a site is worth reconstructing.
    #
    # Batching is the point, not a nicety. COLMAP is incremental but GS
    # training is not: it retrains from scratch for the full epoch budget
    # whether 3 images were added or 300, so batching is what amortises the
    # dominant cost. A fixed per-contribution delay could not do that -- three
    # contributions in an afternoon meant three full retrainings.
    cel_logger.info(
        f"post_save_contrib_imageops (task_id={self.request.id}): ingest complete "
        f"for {contrib_id}; reconstruction is scheduled separately."
    )


def mark_ingest_enqueued(contrib_id) -> None:
    """
    Record that ingest (ImageOps, or the video extraction that enqueues it)
    was just enqueued for this contribution, so the stuck-ingest sweep leaves
    it alone for `IMAGEOPS_STUCK_AFTER_MINUTES`. Bookkeeping: never raises.
    """
    try:
        from django.utils import timezone

        from .models import Contribution

        Contribution.objects.filter(ID=contrib_id).update(
            imageops_enqueued_at=timezone.now()
        )
    except Exception as exc:  # noqa: BLE001
        cel_logger.warning(
            f"Could not record the ingest enqueue for {contrib_id}: {exc}"
        )


def enqueue_ingest(contribution, *, on_failure_delete: bool = False) -> dict:
    """
    Enqueue a contribution's ingest -- frame extraction for each video not yet
    extracted, ImageOps when it has images -- and record it with
    `mark_ingest_enqueued`. Call it from `transaction.on_commit`.

    Shared by the one-POST upload, `upload/finish/` and the abandoned-upload
    sweep, so all three behave alike. ImageOps is skipped for a video-only
    contribution: `extract_video_frames_task` enqueues it once frames exist.

    On failure the admins are mailed. The contribution is deleted only when
    `on_failure_delete` is set *and* nothing at all was queued -- the legacy
    upload's behaviour. A finalized batched upload is kept instead:
    `requeue_stuck_contributions_task` re-enqueues it, and deleting a hundred
    photographs because a broker blinked would be the worse outcome.

    Returns `{"queued": [...], "deleted": bool}`.
    """
    outcome = {"queued": [], "deleted": False}
    queued = outcome["queued"]
    try:
        for source in contribution.videos.filter(frames_extracted=0):
            extract_video_frames_task.delay(str(source.ID))
            queued.append(f"video {source.ID}")
        if contribution.images.exists():
            post_save_contrib_imageops.delay(str(contribution.ID))
            queued.append("imageops")
        if queued:
            mark_ingest_enqueued(contribution.ID)
        return outcome
    except Exception as exc:  # noqa: BLE001 - handled here
        failure = exc

    cel_logger.error(
        f"Failed to enqueue processing for {contribution.ID} "
        f"(already queued: {queued or 'nothing'}): {failure}"
    )
    try:
        from .email_utils import send_image_processing_failure_email

        send_image_processing_failure_email(
            contribution_id=str(contribution.ID),
            site_id=str(contribution.site_id),
            site_name=contribution.site.name,
            contributor_email=contribution.contributor.email,
            error_message=f"Failed to trigger image processing task: {failure}",
        )
    except Exception as email_error:  # noqa: BLE001
        cel_logger.error(
            f"Failed to send task trigger failure notification: {email_error}"
        )

    if queued or not on_failure_delete:
        cel_logger.error(
            f"Keeping {contribution.ID}: the periodic sweep will re-enqueue "
            "whatever was not queued."
        )
        return outcome
    cel_logger.error("Deleting contribution: nothing was queued for it.")
    contribution.delete()
    outcome["deleted"] = True
    return outcome


def notify_admin_on_commit(contribution_id) -> None:
    """Queue the new-contribution mail once the rows are committed. Best-effort."""
    from django.db import transaction

    def _notify():
        try:
            notify_admin_new_contribution_task.delay(str(contribution_id))
        except Exception as exc:  # noqa: BLE001
            cel_logger.error(f"Could not queue the admin notification: {exc}")

    transaction.on_commit(_notify)


def finalize_contribution(contribution_id, *, reason: str = "finish") -> str:
    """
    Close an open (batched) contribution and start its ingest, exactly once.

    Returns one of:
      - "finalized"  -- it was open and had files; ingest and the admin mail
                        are enqueued on commit.
      - "already"    -- it was finalized before; nothing is done.
      - "deleted"    -- it was open with no files at all, and is gone.
      - "missing"    -- no such contribution.

    The row lock is what makes it exactly-once: a second `finish`, a retried
    one, or the abandoned-upload sweep racing the client all serialise on it,
    and only the caller that sees `finalized_at IS NULL` does anything.
    """
    from django.db import transaction
    from django.utils import timezone

    from .models import Contribution, Site

    with transaction.atomic():
        contribution = (
            Contribution.objects.select_for_update().filter(ID=contribution_id).first()
        )
        if contribution is None:
            return "missing"
        if contribution.finalized_at is not None:
            return "already"
        if not contribution.images.exists() and not contribution.videos.exists():
            cel_logger.info(
                f"finalize_contribution ({reason}): {contribution_id} is open and "
                "empty; deleting it."
            )
            contribution.delete()
            return "deleted"

        now = timezone.now()
        Contribution.objects.filter(ID=contribution.ID).update(finalized_at=now)
        contribution.finalized_at = now
        # Same targeted bump the one-POST upload makes, now that the
        # contribution is real.
        Site.objects.filter(ID=contribution.site_id).update(updated_at=now)

        transaction.on_commit(lambda: enqueue_ingest(contribution))
        notify_admin_on_commit(contribution.ID)

    cel_logger.info(f"finalize_contribution ({reason}): finalized {contribution_id}.")
    return "finalized"


def discard_open_contribution(contribution_id) -> str:
    """
    Delete an *open* contribution and its files, under its row lock.

    Returns "deleted", "already" (it is finalized, and is kept) or "missing".
    Row by row through the ORM, so `django_cleanup` removes each file.
    """
    from django.db import transaction

    from .models import Contribution

    with transaction.atomic():
        locked = (
            Contribution.objects.select_for_update().filter(ID=contribution_id).first()
        )
        if locked is None:
            return "missing"
        if locked.finalized_at is not None:
            return "already"
        locked.delete()
    return "deleted"


def _clear_queued_marker(contrib_id: str) -> None:
    """The site of this contribution is no longer waiting in the queue."""
    from .models import Site

    try:
        Site.objects.filter(contributions__ID=contrib_id).update(recon_queued_at=None)
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not stop a run
        cel_logger.warning(f"Could not clear the queued marker for {contrib_id}: {exc}")


@app.task(
    bind=True,
    queue=QUEUE_RECON,
    soft_time_limit=RECON_SOFT_TIME_LIMIT,
    time_limit=RECON_TIME_LIMIT,
)
def recon_runner_task(
    self, contrib_id: str, recons_type: str = "all", cond_run_av: bool = True
) -> None:
    """
    Triggers `MeshOps` & `GSOps`, when a `Run` instance is created.

    Parameters
    ----------
    self : Task
        Celery task instance (when bind=True)
    contrib_id : str
        The `Contribution` instance's UUID.
    recons_type : str, optional
        The reconstruction type, by default "all" ["GS", "aV"].
    cond_run_av : bool, optional
        Whether to conditionally run aV based on image count, by default True.
        Needed on low V/RAM devices to avoid OOM & malloc issues.

    """
    # Determine ops based on recons_type and conditional AV checks
    if recons_type == "aV":
        ops = ["aV"]
    elif recons_type == "GS":
        ops = ["GS"]
    elif recons_type == "all":
        # Default to running both aV then GS
        ops = ["aV", "GS"]
        # If conditional AV runs are enabled, check total images across the site
        if cond_run_av:
            from .models import Contribution, Image

            try:
                contrib = Contribution.objects.get(ID=contrib_id)
                site = contrib.site
                total_images = Image.objects.filter(
                    contribution__site=site, contribution__finalized_at__isnull=False
                ).count()
                if total_images > MESHOPS_MAX_IMAGES:
                    cel_logger.warning(
                        f"recon_runner_task (task_id={self.request.id}): Site {site.ID} has {total_images} images (> {MESHOPS_MAX_IMAGES}); skipping aV to avoid OOM. Running GS only."
                    )
                    ops = ["GS"]
            except Exception as e:
                cel_logger.error(
                    f"recon_runner_task (task_id={self.request.id}): Failed to check image count for contrib {contrib_id}: {e}. Proceeding with ops={ops}."
                )
    else:
        ops = [
            recons_type,
        ]

    from .workers import ops_runner, prerun_check

    # The run has left the queue (review 2026-11-22 §2), whatever happens next:
    # the scheduler may consider the site again once this task ends.
    _clear_queued_marker(contrib_id)
    try:
        _run_ops(self, contrib_id, recons_type, ops, ops_runner, prerun_check)
    finally:
        _clear_queued_marker(contrib_id)


def _run_ops(task, contrib_id, recons_type, ops, ops_runner, prerun_check) -> None:
    self = task
    cel_logger.info(
        f"recon_runner_task (task_id={self.request.id}): Running prerun checks for contrib_id: {contrib_id}..."
    )
    chk, msg = prerun_check(contrib_id, recons_type)
    cel_logger.info(
        f"recon_runner_task (task_id={self.request.id}): {contrib_id} - {msg}"
    )
    if chk:
        for op in ops:
            cel_logger.info(
                f"recon_runner_task (task_id={self.request.id}): Triggering {op}Ops for contrib_id: {contrib_id}."
            )
            cel_logger.info(
                f"recon_runner_task (task_id={self.request.id}): Running {op}Ops for {contrib_id}..."
            )
            try:
                ops_runner(contrib_id=contrib_id, kind=op)
                cel_logger.info(
                    f"recon_runner_task (task_id={self.request.id}): Finished running {op}Ops for {contrib_id}."
                )
            except Exception as e:
                cel_logger.error(
                    f"recon_runner_task (task_id={self.request.id}): {op}Ops failed for {contrib_id}: {e}"
                )
                # Note: ops_runner already sends its own notification, so we don't need to send another here
                # Re-raise to maintain the error propagation
                raise e


@app.task(queue=QUEUE_INGEST)
def notify_admin_new_contribution_task(contribution_id: str) -> None:
    """
    Tell the admins about a contribution, once its photographs are on disk.

    Enqueued by `views.upload` after `store_uploaded_images`, not fired from
    `post_save` on Contribution -- that signal runs one line after the row is
    created and one line *before* the files are written, so every admin
    notification this project has sent has read "Number of Images: 0".

    On `ingest` so an unresponsive mail host cannot hold a gunicorn worker for
    the length of an upload response.
    """
    from .email_utils import send_new_contribution_email

    send_new_contribution_email(contribution_id)


@app.task(queue=QUEUE_INGEST)
def backup_task():
    """
    Backs up the database & media files using django-dbbackup.

    """
    from django.core.management import call_command

    # Setup logger
    bak_logger = Logger(name="db_backup", log_path=LOG_DIR)

    # Say plainly when the "backup" is on the same disk as the thing it backs up.
    #
    # `NFS_DIR` reads like a network mount and defaults to `<root>/archive/`,
    # which on this deployment is the same LVM volume and the same filesystem as
    # the live media. That protects against an accidental delete, a bad
    # migration and an application bug -- all real -- and against nothing that
    # takes the disk: a controller failure, filesystem corruption, a lost volume
    # group. The name implies a guarantee the configuration does not provide, so
    # the log says so once a night rather than leaving it to be discovered.
    def _device_of(path) -> int | None:
        """
        The filesystem a path is on, or would be on.

        Walks up to the nearest existing ancestor: the backup directory is
        created on first write, so on a fresh deployment it does not exist yet
        and the question is still answerable from its parent.
        """
        candidate = Path(path).resolve()
        while True:
            try:
                return os.stat(candidate).st_dev
            except OSError:
                if candidate.parent == candidate:
                    return None
                candidate = candidate.parent

    try:
        media_dev = _device_of(settings.MEDIA_ROOT)
        backup_dev = _device_of(
            settings.DBBACKUP_STORAGE_OPTIONS.get("location", settings.NFS_DIR)
        )
        if media_dev is not None and media_dev == backup_dev:
            message = (
                "backup_task: the backup target is on the SAME filesystem as the "
                "media it backs up. This survives deletion and corruption of the "
                "data, not loss of the disk. Point NFS_DIR at real off-machine "
                "storage."
            )
            bak_logger.warning(message)
            cel_logger.warning(message)
    except OSError as exc:
        bak_logger.warning(f"backup_task: could not compare filesystems: {exc}")

    # Backup database & media files
    bak_logger.info("backup_task: Backing up database & media files...")
    cel_logger.info("backup_task: Backing up database & media files...")

    # `clean=True`, because nothing was ever removing these.
    #
    # `mediabackup` writes a *full* tar every night -- 13 GB here and growing
    # with the archive -- and `backup_task` kept every one of them forever. Two
    # nights was already 25 GB. The disk it fills is the one serving the site,
    # so the eventual failure is not "backups stopped" but "the site stopped".
    #
    # Kept separately because the two are nothing alike in size: the database
    # dump is 1.9 MB, so a month of them costs less than one media tar.
    call_command("dbbackup", clean=True)
    bak_logger.info("backup_task: Backed up database.")
    call_command("mediabackup", clean=True)
    bak_logger.info("backup_task: Backed up media files.")
    cel_logger.info("backup_task: Backed up database & media files.")


@app.task(queue=QUEUE_INGEST)
def db_cleanup_task():
    """
    Report -- and optionally remove -- contributors who never contributed.

    Signing in creates a `Contributor` row holding a name and an email address.
    Somebody who signed in, looked around and left has an identity stored here
    for no purpose, which is what this was meant to clear.

    **It did nothing.** The body was a single log line saying "Cleaning up
    database...", so a scheduled privacy control had been reporting success
    nightly without ever looking at a row. 66 of 84 contributors currently
    qualify.

    Report-only by default. Deleting somebody's record is irreversible, and the
    obvious query is wrong in a way that matters: a contributor created minutes
    ago has no contributions *yet*, and a signed-in visitor part-way through
    choosing files would be deleted underneath themselves. So there is an age
    floor, and removal is opt-in per deployment.
    """
    from django.db.models import Count
    from django.utils import timezone

    from .models import Contributor

    cln_logger = Logger(name="db_cleanup", log_path=LOG_DIR)

    min_age_days = getattr(settings, "CONTRIBUTOR_CLEANUP_MIN_AGE_DAYS", 30)
    cutoff = timezone.now() - timedelta(days=min_age_days)
    stale = (
        Contributor.objects.annotate(n=Count("contributions"))
        .filter(n=0, created_at__lt=cutoff)
        .exclude(email=getattr(settings, "ADMIN_MAIL", ""))
    )
    count = stale.count()

    if not count:
        cln_logger.info("db_cleanup_task: no contributor rows to clear.")
        return

    if not getattr(settings, "CONTRIBUTOR_CLEANUP_ENABLED", False):
        cln_logger.info(
            f"db_cleanup_task: {count} contributor(s) have no contributions and "
            f"are older than {min_age_days}d. Not removing them: set "
            "CONTRIBUTOR_CLEANUP_ENABLED = True to act on this."
        )
        return

    removed, _ = stale.delete()
    cln_logger.info(f"db_cleanup_task: removed {removed} contributor row(s).")


@app.task(
    bind=True,
    queue=QUEUE_INGEST,
    acks_late=True,
    soft_time_limit=VIDEO_SOFT_TIME_LIMIT,
    time_limit=VIDEO_TIME_LIMIT,
)
def extract_video_frames_task(self, video_id: str) -> None:
    """
    Extract frames from an uploaded video (internal design notes).

    On `ingest` because it is CPU work and must never queue behind a
    reconstruction, and as a task rather than a signal because a signal runs
    inside the request/response cycle and this takes minutes.

    Idempotent on redelivery (`acks_late`): `extract_frames` skips a video
    whose frames are already stored and clears a partial set before starting
    again, so a redelivered message no longer inserts a second set of frames.
    """
    from .models import SourceVideo
    from .videoops import extract_frames

    cel_logger.info(f"extract_video_frames_task: starting for video {video_id}.")
    video = SourceVideo.objects.get(ID=video_id)
    result = extract_frames(video)
    cel_logger.info(
        f"extract_video_frames_task: {video_id} -> {result.kept} frame(s) kept "
        f"from {result.candidates} candidate(s); hdr={result.is_hdr}."
    )

    # Frames are images: they go through the same checks as uploaded stills.
    post_save_contrib_imageops.apply_async(
        args=(str(video.contribution.ID),), queue=QUEUE_INGEST
    )
    mark_ingest_enqueued(video.contribution.ID)


@app.task(bind=True, queue=QUEUE_RECON)
def gaussian_cut_task(self, run_id: str) -> None:
    """
    Cut an already-published reconstruction, on an operator's say-so
    (internal design notes).

    Masks arrive after the model does. The cut is part of a reconstruction when
    they are there in time, and this is the other case: somebody has drawn a
    mask for a site that was reconstructed last month, and re-running the whole
    pipeline to use it would spend hours of GPU time retraining a model that is
    already correct.

    On `recon`, not `ingest`, even though it is far cheaper than a
    reconstruction: it rewrites the artifacts a published run is being served
    from, and the recon queue's single slot is what stops it doing that while
    the same run is being trained or published by something else.

    Failure here leaves the run alone. `GSOps.for_existing_run` replaces
    `_handle_error` precisely so a bad mask cannot mark a good published
    reconstruction -- and its Site -- as Error.
    """
    from tirtha.models import Run, RunStage
    from tirtha.workers import GSOps

    run = Run.objects.get(ID=run_id)
    ops = GSOps.for_existing_run(run)

    def begin_stage(name: str, ordinal: int):
        """
        Open a stage row as a *new attempt* (review 2026-11-22 §4).

        `ops._begin_stage` always writes `attempt=1`, which the original run
        already used for these stage names, so the insert hit
        `runstage_unique_per_run_attempt` and `_begin_stage` swallowed the
        error: no re-cut was ever recorded. Retries get a new row.
        """
        ops._stage_metrics = {}
        ops._stage_artifacts = []
        try:
            from django.db.models import Max

            highest = RunStage.objects.filter(run=run, name=name).aggregate(
                top=Max("attempt")
            )["top"]
            stage = RunStage.objects.create(
                run=run,
                name=name,
                ordinal=ordinal,
                attempt=(highest or 0) + 1,
            )
            log_file = getattr(getattr(ops, "logger", None), "_log_file", "")
            stage.start(log_path=str(log_file or ""))
            return stage
        except Exception as exc:  # noqa: BLE001 - bookkeeping only
            cel_logger.warning(f"gaussian_cut_task: could not record {name}: {exc}")
            return None

    # Appended after whatever the original run recorded, so the history reads in
    # order and the re-cut is visibly a later event rather than a rewrite of one.
    highest = (
        RunStage.objects.filter(run=run)
        .order_by("-ordinal")
        .values_list("ordinal", flat=True)
        .first()
    )
    ordinal = (highest + 1) if highest is not None else 0

    for name, call in (
        ("run_gaussian_cut", lambda: ops.run_gaussian_cut(force=True)),
        ("run_postprocess", ops.run_postprocess),
    ):
        stage = begin_stage(name, ordinal)
        ordinal += 1
        try:
            call()
        except Exception:
            ops._end_stage(stage, "Failed")
            cel_logger.exception(f"gaussian_cut_task: {name} failed for run {run_id}")
            raise
        ops._end_stage(stage, "Completed")

    cel_logger.info(f"gaussian_cut_task: re-cut and republished run {run_id}")


@app.task(bind=True, queue=QUEUE_INGEST)
def refresh_leaderboard_task(self):
    """
    Recompute the boards and award badges (internal design notes).

    Scheduled, not per request: the ranking is an aggregate across Image joins
    and changes only when a reconstruction completes.
    """
    from .leaderboard import (
        PERIOD_ALL,
        award_badges,
        compute,
        current_period,
        previous_period,
    )

    # The previous month too (review 2026-11-22 §4). This runs at 06:00, so
    # the first refresh of a new month was the first to see the previous
    # month's last 18 hours -- and it only recomputed the *new* month, so those
    # hours never reached the board they belong to.
    for period in (PERIOD_ALL, previous_period(), current_period()):
        entries = compute(period)
        cel_logger.info(
            f"refresh_leaderboard_task: {period} -> {len(entries)} entries."
        )
    awarded = award_badges()
    cel_logger.info(f"refresh_leaderboard_task: {awarded} new badge(s) awarded.")


def _recon_queue_waiting() -> int | None:
    """
    How many messages are waiting in the `recon` queue, or None if unknown.

    A passive `queue.declare` -- one broker round trip, and it counts *ready*
    messages only: the reconstruction a worker is running has been delivered
    and is not counted. Returns None in eager mode (tests) and whenever the
    broker cannot be asked; the caller then relies on `Site.recon_queued_at`.
    """
    if app.conf.task_always_eager:
        return None
    try:
        with app.connection_for_read() as conn:
            conn.ensure_connection(max_retries=1, timeout=5)
            _, count, _ = conn.default_channel.queue_declare(
                queue=QUEUE_RECON, passive=True
            )
            return int(count)
    except Exception as exc:  # noqa: BLE001 - an unknown answer, not an error
        cel_logger.info(f"Could not read the {QUEUE_RECON} queue depth: {exc}")
        return None


@app.task(bind=True, queue=QUEUE_INGEST)
def schedule_reconstructions_task(self):
    """
    Decide which sites are worth reconstructing, and enqueue them
    (internal design notes).

    Runs hourly by default (`RECON_SCHEDULE_MINUTE`/`RECON_SCHEDULE_HOUR`).
    State lives in the database (review 2026-11-22 §2):

    * a site that is enqueued carries `Site.recon_queued_at`, so the next pass
      does not enqueue it again while it waits behind a multi-hour run;
    * a site whose last `RECON_MAX_CONSECUTIVE_FAILURES` scheduled attempts
      all failed cools down for `RECON_FAILURE_COOLDOWN_HOURS` (24), doubling
      per further failure up to `RECON_FAILURE_COOLDOWN_MAX_HOURS` (168),
      measured from the last failure's end -- so it cannot hold a slot on every
      pass, and is never stopped for good. A success, or an operator setting
      `Site.recon_failures_reset_at`, clears it;
    * nothing is enqueued while the `recon` queue already has a message
      waiting, so the FIFO never grows a backlog that cannot be reordered.

    **Oldest-waiting-first**, so a popular site cannot starve the rest.

    Scheduling runs on `ingest` even though it enqueues `recon` work: deciding
    is cheap and must not queue behind a multi-hour reconstruction.
    """
    from django.utils import timezone

    from .models import Site
    from .scheduler import backed_off_sites, sites_awaiting_reconstruction

    for site, state in backed_off_sites():
        cel_logger.warning(
            f"schedule_reconstructions_task: {site.name} ({site.ID}) skipped -- "
            f"its last {state.failures} scheduled reconstruction(s) failed; "
            f"cooling down until {state.cooldown_until:%Y-%m-%d %H:%M %Z}. Use "
            "the admin's 'Reset reconstruction back-off' to retry it sooner."
        )

    waiting = _recon_queue_waiting()
    if waiting:
        cel_logger.info(
            f"schedule_reconstructions_task: {waiting} reconstruction(s) already "
            "waiting in the queue; enqueueing nothing this pass."
        )
        return

    candidates = sites_awaiting_reconstruction()
    if not candidates:
        cel_logger.info("schedule_reconstructions_task: nothing waiting.")
        return

    cel_logger.info(
        f"schedule_reconstructions_task: {len(candidates)} site(s) waiting."
    )

    # A ceiling on how many one pass may enqueue.
    #
    # `recon` is one slot across the machine and a reconstruction is hours, so
    # everything queued beyond what can actually run soon is not scheduling,
    # it is a backlog with no way to reorder it: the queue is FIFO, the
    # candidate list is deliberately oldest-first, and once nine are enqueued a
    # site that becomes more urgent tomorrow waits behind all of them.
    #
    # It also bounds the blast radius of a bulk change. Anything that makes a
    # large number of images reconstructable at once -- a moderation sweep, the
    # `safety_status` backfill that 19,321 rows are waiting on -- would
    # otherwise enqueue every affected site in a single pass and mail every
    # contributor as each finished.
    #
    # Nothing is lost by deferring: the candidates are recomputed from scratch
    # every pass, so a site not taken now is simply first in line next time,
    # and its `waiting_days` has grown, which is what sorts it.
    limit = getattr(settings, "RECON_MAX_ENQUEUE_PER_RUN", 2)
    if limit and limit > 0 and len(candidates) > limit:
        cel_logger.info(
            f"schedule_reconstructions_task: enqueueing {limit} of "
            f"{len(candidates)}; the rest are first in line next pass."
        )
        candidates = candidates[:limit]

    for candidate in candidates:
        cel_logger.info(
            f"  {candidate.site.name}: {candidate.pending_images} new image(s), "
            f"oldest waiting {candidate.waiting_days:.1f}d ({candidate.reason})."
        )
        # Marked *before* the enqueue: a fast worker can start the task, and
        # clear the marker, before `apply_async` even returns.
        site_id = getattr(candidate.site, "pk", None)
        queued = Site.objects.filter(ID=site_id) if site_id else Site.objects.none()
        queued.update(recon_queued_at=timezone.now())
        try:
            recon_runner_task.apply_async(
                args=(str(candidate.contribution_id), "all"),
                queue=QUEUE_RECON,
            )
        except Exception:
            queued.update(recon_queued_at=None)
            raise


@app.task(queue=QUEUE_INGEST)
def notify_admin_new_contributor_task(contributor_id: str) -> None:
    """
    Mail the admins about a sign-up that needs activating.

    Enqueued from `signals.notify_admin_new_contributor` on commit, so SMTP is
    never spoken from inside the sign-in request (review 2026-11-22 §4).
    """
    from .email_utils import send_new_contributor_email

    send_new_contributor_email(contributor_id)


@app.task(bind=True, queue=QUEUE_INGEST)
def requeue_stuck_contributions_task(self) -> int:
    """
    Re-enqueue contributions whose ingest never finished (review 2026-11-22 §2).

    A contribution older than `IMAGEOPS_STUCK_AFTER_MINUTES` (60) that is still
    not `processed` lost its ImageOps message -- a broker restart, a worker
    killed past the retries, a failed enqueue -- or its video extraction. Its
    images stay `pending`, which keeps them out of every reconstruction, and
    nothing ever looked at them again.

    Both tasks are idempotent now, so re-enqueueing one that is merely slow is
    harmless -- but not free, so a contribution whose ingest was enqueued
    (`Contribution.imageops_enqueued_at`) within the same window is skipped. Bounded by age (`IMAGEOPS_SWEEP_MAX_AGE_DAYS`, 2) so a
    contribution that fails permanently is not retried for ever, and by count
    (`IMAGEOPS_SWEEP_MAX_PER_RUN`, 20) per pass.
    """
    from datetime import timedelta as _td

    from django.utils import timezone

    from .models import Contribution

    now = timezone.now()
    stuck_after = _td(
        minutes=int(getattr(settings, "IMAGEOPS_STUCK_AFTER_MINUTES", 60))
    )
    max_age = _td(days=float(getattr(settings, "IMAGEOPS_SWEEP_MAX_AGE_DAYS", 2)))
    per_run = int(getattr(settings, "IMAGEOPS_SWEEP_MAX_PER_RUN", 20))

    # Not one enqueued within the window either: it may simply be waiting in
    # a busy queue, and a second message would run the checks twice.
    stuck = (
        Contribution.objects.filter(
            processed=False,
            # An open batched upload has not been handed to ingest at all;
            # `finalize_abandoned_uploads_task` is what closes it.
            finalized_at__isnull=False,
            contributed_at__lt=now - stuck_after,
            contributed_at__gt=now - max_age,
        )
        .exclude(imageops_enqueued_at__gt=now - stuck_after)
        .order_by("contributed_at")[:per_run]
    )

    requeued = 0
    for contribution in stuck:
        # `frames_extracted` is set only once extraction completed.
        videos = [v for v in contribution.videos.all() if not v.frames_extracted]
        try:
            if videos:
                # Extraction enqueues the image checks itself when it is done.
                for video in videos:
                    extract_video_frames_task.apply_async(
                        args=(str(video.ID),), queue=QUEUE_INGEST
                    )
            elif contribution.images.exists():
                post_save_contrib_imageops.apply_async(
                    args=(str(contribution.ID),), queue=QUEUE_INGEST
                )
            else:
                continue
        except Exception as exc:  # noqa: BLE001 - try the rest
            cel_logger.error(
                f"requeue_stuck_contributions_task: could not re-enqueue "
                f"{contribution.ID}: {exc}"
            )
            continue
        mark_ingest_enqueued(contribution.ID)
        requeued += 1
        cel_logger.warning(
            f"requeue_stuck_contributions_task: re-enqueued {contribution.ID} "
            f"({'video extraction' if videos else 'ImageOps'}), unprocessed since "
            f"{contribution.contributed_at:%Y-%m-%d %H:%M}."
        )
    return requeued


@app.task(bind=True, queue=QUEUE_INGEST)
def finalize_abandoned_uploads_task(self) -> dict:
    """
    Close batched uploads the client never finished.

    An open contribution idle for longer than `UPLOAD_OPEN_TTL_MINUTES` (120)
    -- no batch since -- was abandoned: the tab closed, the phone slept, the
    network went. What happens to it:

    - **deleted** if its contributor is now banned or inactive;
    - **deleted** if the client never acknowledged a batch
      (`confirmed_batches == 0`): it may never have learnt the id, and a
      Cancel or a closed tab during the first batch is indistinguishable from
      a lost connection, so the photographs are not kept against a wish to
      cancel;
    - **finalized** otherwise if it holds files, because the photographs
      already sent are valuable and the contributor took them on site;
    - deleted if it holds nothing.

    All are logged. Every decision goes through `finalize_contribution` or
    `discard_open_contribution`, under the row lock, so a client finishing at
    the same moment is not finalized twice, and a finalized one is never
    deleted.
    """
    from django.db.models import F
    from django.db.models.functions import Coalesce
    from django.utils import timezone

    from .models import Contribution

    ttl = timedelta(minutes=upload_open_ttl_minutes())
    cutoff = timezone.now() - ttl
    stale = (
        Contribution.objects.filter(finalized_at__isnull=True)
        .annotate(touched=Coalesce(F("last_batch_at"), F("contributed_at")))
        .filter(touched__lt=cutoff)
        .values_list(
            "ID",
            "confirmed_batches",
            "contributor__banned",
            "contributor__active",
        )
    )
    result = {"finalized": 0, "deleted": 0}
    for contribution_id, confirmed, banned, active in list(stale):
        try:
            if banned or not active or not confirmed:
                why = (
                    "its contributor is banned or inactive"
                    if banned or not active
                    else "the client never acknowledged a batch"
                )
                if discard_open_contribution(contribution_id) == "deleted":
                    result["deleted"] += 1
                    cel_logger.warning(
                        f"finalize_abandoned_uploads_task: deleted abandoned upload "
                        f"{contribution_id} (idle > {ttl}; {why})."
                    )
                continue
            outcome = finalize_contribution(contribution_id, reason="abandoned")
        except Exception as exc:  # noqa: BLE001 - try the rest
            cel_logger.error(
                f"finalize_abandoned_uploads_task: could not close "
                f"{contribution_id}: {exc}"
            )
            continue
        if outcome == "finalized":
            result["finalized"] += 1
            cel_logger.warning(
                f"finalize_abandoned_uploads_task: auto-finalized abandoned upload "
                f"{contribution_id} (idle > {ttl})."
            )
        elif outcome == "deleted":
            result["deleted"] += 1
            cel_logger.warning(
                f"finalize_abandoned_uploads_task: deleted empty abandoned upload "
                f"{contribution_id} (idle > {ttl})."
            )
    return result


def upload_open_ttl_minutes() -> int:
    """How long a batched upload may sit idle before it counts as abandoned."""
    return int(getattr(settings, "UPLOAD_OPEN_TTL_MINUTES", 120))


@app.task(bind=True, queue=QUEUE_INGEST)
def sweep_orphaned_runs_task(self) -> int:
    """
    Release runs left Processing by a dead worker, between restarts
    (review 2026-11-22 §1 #5, §2). See `celery.sweep_orphaned_runs`.

    On `ingest`, because a sweep on `recon` would wait behind the very run it
    is meant to check on.
    """
    from .celery import sweep_orphaned_runs

    return sweep_orphaned_runs()


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    # Calls backup_task() every BACKUP_INTERVAL.
    sender.add_periodic_task(BACKUP_INTERVAL, backup_task.s(), name="backup_task")

    # Calls db_cleanup_task() every DBCLEANUP_INTERVAL.
    sender.add_periodic_task(
        DBCLEANUP_INTERVAL, db_cleanup_task.s(), name="db_cleanup_task"
    )

    # Evaluates the reconstruction trigger (internal design notes); hourly by
    # default, see RECON_SCHEDULE_INTERVAL.
    sender.add_periodic_task(
        RECON_SCHEDULE_INTERVAL,
        schedule_reconstructions_task.s(),
        name="schedule_reconstructions_task",
    )

    # Rebuilds the leaderboards and awards badges (internal design notes).
    sender.add_periodic_task(
        LEADERBOARD_INTERVAL,
        refresh_leaderboard_task.s(),
        name="refresh_leaderboard_task",
    )

    # Re-enqueues ingest that never finished (review 2026-11-22 §2).
    sender.add_periodic_task(
        IMAGEOPS_SWEEP_INTERVAL,
        requeue_stuck_contributions_task.s(),
        name="requeue_stuck_contributions_task",
    )

    # Closes batched uploads their client never finished.
    sender.add_periodic_task(
        ABANDONED_UPLOAD_SWEEP_INTERVAL,
        finalize_abandoned_uploads_task.s(),
        name="finalize_abandoned_uploads_task",
    )

    # Releases runs orphaned by a dead worker without waiting for a restart.
    sender.add_periodic_task(
        ORPHAN_SWEEP_INTERVAL,
        sweep_orphaned_runs_task.s(),
        name="sweep_orphaned_runs_task",
    )
