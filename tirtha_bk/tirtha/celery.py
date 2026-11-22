import logging
import os

from celery import Celery
from celery.signals import task_failure, worker_ready

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tirtha_bk.settings")

app = Celery("tirtha")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django apps.
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")


# Setup logger for task failure handling
logger = logging.getLogger(__name__)


# --- Orphaned runs (review 2026-11-22 §1 #5, §2) ------------------------------

PUBLISHED_RUN_STATUSES = ("Archived", "Manual")
RECON_TASK_NAMES = ("tirtha.tasks.recon_runner_task", "tirtha.tasks.gaussian_cut_task")

# Written into the notes of every run `release_orphaned_runs` marks Error --
# the periodic sweep, the startup sweep and the `task_failure` handler all go
# through it -- and matched by `scheduler` so those runs never count towards
# the reconstruction back-off. A dead worker or a deploy restart says nothing
# about whether the site can reconstruct. One constant for both ends, so the
# wording of the notes can change without silently breaking the match.
RELEASED_RUN_TAG = "[released-by-tirtha]"

# What those notes said before the tag existed, matched as well so a run
# released by an older build is not counted either.
LEGACY_RELEASED_NOTE_PREFIXES = (
    "Marked Error by the periodic sweep",
    "Marked Error by the startup sweep",
    "Marked Error when its task failed",
)


def released_run_q():
    """A `Q` matching runs marked Error by a release rather than by the pipeline."""
    from django.db.models import Q

    q = Q(notes__contains=RELEASED_RUN_TAG)
    for prefix in LEGACY_RELEASED_NOTE_PREFIXES:
        q |= Q(notes__contains=prefix)
    return Q(status="Error") & q


def restore_site_status(site_id) -> str | None:
    """
    Put a site whose run was orphaned back where it sensibly was.

    The startup sweep used to set every such site to Pending, so a site that
    had been Live for months -- and was merely being re-reconstructed --
    dropped off the public map until the next successful run. A site with a
    published, visible run is Live; one without is Pending.

    Only a site still saying Processing is touched, and not while another run
    of it is genuinely Processing. Returns the status written, or None.
    """
    from tirtha.models import Run, Site

    if Run.objects.filter(site_id=site_id, status="Processing").exists():
        return None
    published = Run.objects.filter(
        site_id=site_id, status__in=PUBLISHED_RUN_STATUSES, hidden=False
    ).exists()
    status = "Live" if published else "Pending"
    updated = Site.objects.filter(ID=site_id, status="Processing").update(status=status)
    return status if updated else None


def release_orphaned_runs(runs, why: str) -> int:
    """
    Mark these Processing runs Error, say why in their notes, and restore their
    sites. Conditional on the row still being Processing, so a run that
    finished in the meantime is left alone. Returns how many were released.
    """
    from django.utils import timezone

    from tirtha.models import Run

    released = 0
    site_ids = set()
    for run in list(runs.filter(status="Processing")):
        notes = ((run.notes or "") + f"\n\n{RELEASED_RUN_TAG} {why}").strip()
        updated = Run.objects.filter(pk=run.pk, status="Processing").update(
            status="Error", ended_at=timezone.now(), notes=notes
        )
        if updated:
            released += 1
            site_ids.add(run.site_id)
    for site_id in site_ids:
        restore_site_status(site_id)
    return released


def _active_recon_sites() -> set | None:
    """
    Sites a `recon@` worker reports it is working on right now, or None when
    that cannot be known (eager mode, no recon worker answered, broker down).
    """
    if app.conf.task_always_eager:
        return None
    try:
        replies = app.control.inspect(timeout=5).active() or {}
    except Exception as exc:  # noqa: BLE001
        logger.info(f"Could not inspect the workers: {exc}")
        return None
    recon = {
        node: tasks for node, tasks in replies.items() if node.startswith("recon@")
    }
    if not recon:
        return None

    from tirtha.models import Contribution, Run

    sites = set()
    for tasks in recon.values():
        for task in tasks or []:
            if task.get("name") not in RECON_TASK_NAMES:
                continue
            args = task.get("args") or []
            if isinstance(args, str):
                # Older Celery reports a repr; nothing reliable to parse.
                return None
            if not args:
                continue
            if task["name"].endswith("recon_runner_task"):
                site = (
                    Contribution.objects.filter(ID=args[0])
                    .values_list("site_id", flat=True)
                    .first()
                )
            else:
                site = (
                    Run.objects.filter(ID=args[0])
                    .values_list("site_id", flat=True)
                    .first()
                )
            if site:
                sites.add(site)
    return sites


def sweep_orphaned_runs() -> int:
    """
    Release runs no live worker owns, without waiting for a restart.

    Run from beat every `RECON_ORPHAN_SWEEP_MINUTES` (15). Two ways to know a
    run is orphaned:

    * a `recon@` worker answers `inspect().active()` and is not working on that
      run's site -- then any run of it older than `RECON_ORPHAN_MIN_AGE_MINUTES`
      (15, so a run being created right now is not caught) is dead;
    * nobody can say (no recon worker answered) -- then only a run older than
      the recon hard time limit plus an hour, which no live run can be.
    """
    from datetime import timedelta

    from django.conf import settings
    from django.utils import timezone

    from tirtha.models import Run

    now = timezone.now()
    min_age = timedelta(
        minutes=float(getattr(settings, "RECON_ORPHAN_MIN_AGE_MINUTES", 15))
    )
    processing = Run.objects.filter(status="Processing", started_at__lt=now - min_age)
    if not processing.exists():
        restore_stranded_sites(now)
        return 0

    active = _active_recon_sites()
    if active is None:
        from tirtha.tasks import RECON_TIME_LIMIT

        if not RECON_TIME_LIMIT:
            return 0
        cutoff = now - timedelta(seconds=RECON_TIME_LIMIT) - timedelta(hours=1)
        orphans = processing.filter(started_at__lt=cutoff)
        why = (
            "Marked Error by the periodic sweep: Processing for longer than the "
            "reconstruction time limit, so no live worker can still own it."
        )
    else:
        orphans = processing.exclude(site_id__in=active)
        why = (
            "Marked Error by the periodic sweep: no recon worker reports working "
            "on this site, so the worker that owned this run is gone."
        )

    released = release_orphaned_runs(orphans, why)
    if released:
        logger.warning("Periodic sweep: released %d orphaned run(s).", released)
    restore_stranded_sites(now)
    return released


def restore_stranded_sites(now=None) -> list:
    """
    Restore Sites that say Processing with nothing behind them.

    A crash between the run's last write and the site's leaves the site at
    Processing with no Processing run. The scheduler skips Processing sites,
    so without this it was never reconstructed again until a recon worker
    restarted. A site whose `recon_queued_at` is within
    `RECON_QUEUED_TTL_HOURS` is left alone: its task is on its way and will
    set the status itself. `restore_site_status` re-checks for a Processing
    run and only touches a site still saying Processing.

    Returns the IDs of the sites restored.
    """
    from datetime import timedelta

    from django.conf import settings
    from django.db.models import Exists, OuterRef
    from django.utils import timezone

    from tirtha.models import Run, Site

    now = now or timezone.now()
    ttl = timedelta(hours=float(getattr(settings, "RECON_QUEUED_TTL_HOURS", 72)))
    stranded = (
        Site.objects.filter(status="Processing")
        .exclude(recon_queued_at__gt=now - ttl)
        .exclude(
            Exists(Run.objects.filter(site_id=OuterRef("pk"), status="Processing"))
        )
        .values_list("ID", flat=True)
    )
    restored = [site_id for site_id in stranded if restore_site_status(site_id)]
    if restored:
        logger.warning(
            "Periodic sweep: restored %d site(s) left at Processing with no "
            "Processing run: %s",
            len(restored),
            ", ".join(str(s) for s in restored),
        )
    return restored


@task_failure.connect
def task_failure_handler(
    sender=None, task_id=None, exception=None, traceback=None, einfo=None, **kwargs
):
    """
    Global error handler for all Celery tasks.
    This will send email notifications for critical task failures.

    """
    logger.error(
        f"Celery task {sender.name if sender else 'Unknown'} failed with task_id {task_id}: {exception}"
    )

    # Only send emails for contribution processing tasks that haven't already sent their own notifications
    # Note: Our tasks already handle their own error notifications, so this is a fallback for unexpected failures
    if sender and sender.name in [
        "tirtha.tasks.post_save_contrib_imageops",
        "tirtha.tasks.recon_runner_task",
    ]:
        logger.info(
            f"Task {sender.name} failed - task-level error handling should have already sent notification"
        )
        # We don't send duplicate emails here since our tasks handle their own error notifications

    # A reconstruction that died -- `WorkerLostError`, a time limit, anything
    # that escaped `_handle_error` -- must not leave its run Processing until
    # the next worker restart (review 2026-11-22 §2). Scoped to this task's
    # site: another recon host may be running a different site right now.
    if sender and sender.name == "tirtha.tasks.recon_runner_task":
        try:
            args = kwargs.get("args") or ()
            contrib_id = (
                args[0] if args else (kwargs.get("kwargs") or {}).get("contrib_id")
            )
            if contrib_id:
                from tirtha.models import Contribution, Run

                site_id = (
                    Contribution.objects.filter(ID=contrib_id)
                    .values_list("site_id", flat=True)
                    .first()
                )
                if site_id:
                    released = release_orphaned_runs(
                        Run.objects.filter(site_id=site_id),
                        "Marked Error when its task failed "
                        f"({type(exception).__name__}: {exception}).",
                    )
                    # The site may already have been set to Error by the
                    # pipeline; restore it only if it is still Processing.
                    if not released:
                        restore_site_status(site_id)
        except Exception as exc:  # noqa: BLE001 - a failure handler must not fail
            logger.error(f"Could not release the runs of a failed task: {exc}")


@worker_ready.connect
def reconcile_orphaned_runs(sender=None, **kwargs):
    """
    A run whose worker died stays "Processing" for ever. Clear it at startup.

    `_handle_error` is what moves a failed run to "Error", and it only runs when
    the exception reaches it. Anything that kills the process first -- a reboot,
    `systemctl restart`, an OOM, a `colmap` that is not on the unit's PATH and
    aborts the interpreter -- leaves the row saying `Processing`, and the `Site`
    saying it too. Nothing ever sets them back.

    That is not cosmetic. `prerun_check` refuses to start a run while a site says
    "Processing", so the site is *wedged*: contributors keep uploading and no
    reconstruction is ever scheduled again. It happened twice in one day and was
    fixed by hand both times.

    Only the `recon` worker sweeps, and only its own kind of work. That queue is
    deliberately one slot across the whole machine (internal design notes), so a
    recon worker that has just started knows nothing is in flight: any run still
    marked Processing is an orphan by definition. The `ingest` worker must not
    do this -- it starts and restarts independently, and a reconstruction may
    genuinely be running while it does.

    Runs are marked Error rather than resumed. The stage rows record what
    completed and 10.4's resume can skip that work on the next attempt, but
    deciding to re-run is an operator's call, not a side effect of a restart.
    """
    import django

    django.setup()

    from tirtha.models import Run, Site

    # Identified by node name, not by asking the broker. `inspect()` here would
    # be this worker interrogating itself before it has started consuming, which
    # blocks. The units name them `-n recon@%%h` and `-n ingest@%%h`, and
    # `build/start.sh` does the same, so the name is the contract.
    hostname = getattr(sender, "hostname", "") or ""
    if not hostname.startswith("recon@"):
        # Said out loud, because the alternative is a safety net that quietly
        # is not there. The name is a convention, not something the code can
        # enforce: rename the node in the unit and this stops running, with no
        # error and nothing missing until a crashed run wedges a site and
        # somebody has to work out why nothing reclaimed it.
        logger.info(
            f"reconcile_orphaned_runs: not sweeping from {hostname!r} "
            "(only a node named recon@... sweeps; see internal design notes)."
        )
        return

    orphaned = Run.objects.filter(status="Processing")
    count = release_orphaned_runs(
        orphaned,
        "Marked Error by the startup sweep: the worker that owned this "
        "run is gone, so it cannot still be processing. The stage rows below "
        "record how far it got.",
    )
    if not count:
        logger.info(
            f"reconcile_orphaned_runs: {hostname} swept, nothing was left Processing."
        )
    else:
        logger.warning(
            "Startup sweep: %d run(s) left at Processing by a dead worker were "
            "marked Error, and their sites restored (Live if published, else "
            "Pending).",
            count,
        )

    # A site can say Processing with no Processing run behind it -- a crash
    # between the two writes. Nothing is running on a recon worker that has
    # just started, so restore those too.
    for site_id in Site.objects.filter(status="Processing").values_list(
        "ID", flat=True
    ):
        restore_site_status(site_id)
