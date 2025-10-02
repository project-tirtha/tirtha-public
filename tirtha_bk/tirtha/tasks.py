from pathlib import Path

from django.conf import settings

# Local imports
from .celery import app
from celery.schedules import crontab
from celery.utils.log import get_task_logger
from .utils import Logger

cel_logger = get_task_logger(__name__)
LOG_DIR = Path(settings.LOG_DIR)
MESHOPS_CONTRIB_DELAY = settings.MESHOPS_CONTRIB_DELAY  # hours
BACKUP_INTERVAL = crontab(
    minute=0, hour=0, day_of_week=0
)  # Every 1 week at 00:00 on Sunday
DBCLEANUP_INTERVAL = crontab(
    minute=0, hour=0, day_of_week=0
)  # Every week at 00:00 on Sunday


@app.task(bind=True)
def post_save_contrib_imageops(self, contrib_id: str, recons_type: str = "all") -> None:
    """
    Triggers `ImageOps`, when a `Contribution` instance is created & saved.

    Parameters
    ----------
    self : Task
        Celery task instance (when bind=True)
    contrib_id : str
        The `Contribution` instance's UUID.
    recons_type : str, optional
        The reconstruction type, by default "all" ["GS", "aV"].

    """

    # Check images
    cel_logger.info(
        f"post_save_contrib_imageops (task_id={self.request.id}): Triggering ImageOps for contrib_id: {contrib_id}."
    )
    cel_logger.info(
        f"post_save_contrib_imageops (task_id={self.request.id}): Checking images for contrib_id: {contrib_id}..."
    )
    from .imageops import ImageOps
    from .models import Contribution

    try:
        iops = ImageOps(contrib_id=contrib_id)
        # FIXME: TODO: Till the VRAM + concurrency issue is fixed, skipping image checks.
        iops.check_images()
        # cel_logger.info(
        #     f"post_save_contrib_imageops: Finished checking images for contrib_id: {contrib_id}."
        # )
        cel_logger.info(
            f"Skipping image checks for contrib_id: {contrib_id} due to VRAM + concurrency issues. FIXME:"
        )
    except Exception as e:
        cel_logger.error(f"ImageOps failed for contribution {contrib_id}: {e}")

        # Send email notification about the failure
        try:
            from .email_utils import send_image_processing_failure_email

            # Get contribution details for the email
            try:
                contribution = Contribution.objects.get(ID=contrib_id)
                contributor_email = contribution.contributor.email
                mesh_id = str(contribution.mesh.ID)
                mesh_name = contribution.mesh.name

                send_image_processing_failure_email(
                    contribution_id=contrib_id,
                    mesh_id=mesh_id,
                    mesh_name=mesh_name,
                    contributor_email=contributor_email,
                    processing_step="Image Processing (ImageOps)",
                    error_message=str(e),
                    log_file_path=str(iops.logger._log_file)
                    if "iops" in locals() and hasattr(iops.logger, "_log_file")
                    else None,
                )
            except Exception as contrib_error:
                cel_logger.error(
                    f"Failed to get contribution details for email: {contrib_error}"
                )

        except Exception as email_error:
            cel_logger.error(
                f"Failed to send ImageOps failure notification: {email_error}"
            )

        # Re-raise the original exception
        raise e

    # FIXME: TODO: MESHOPS_CONTRIB_DELAY = 0.1 (6 minutes), till the image checks are fixed.
    # Create mesh after MESHOPS_CONTRIB_DELAY hours

    cel_logger.info(
        f"post_save_contrib_imageops (task_id={self.request.id}): Will trigger reconstruction pipelines for {contrib_id} after {MESHOPS_CONTRIB_DELAY} hours..."
    )
    recon_runner_task.apply_async(
        args=(
            contrib_id,
            recons_type,
        ),
        countdown=MESHOPS_CONTRIB_DELAY * 60 * 60,
    )


@app.task(bind=True)
def recon_runner_task(self, contrib_id: str, recons_type: str = "all") -> None:
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

    """
    ops = ["GS", "aV"] if recons_type == "all" else [recons_type]

    from .workers import prerun_check, ops_runner

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


@app.task
def backup_task():
    """
    Backs up the database & media files using django-dbbackup.

    """
    from django.core.management import call_command

    # Setup logger
    bak_logger = Logger(name="db_backup", log_path=LOG_DIR)

    # Backup database & media files
    bak_logger.info("backup_task: Backing up database & media files...")
    cel_logger.info("backup_task: Backing up database & media files...")
    call_command("dbbackup")  # LATE_EXP: Add other options
    bak_logger.info("backup_task: Backed up database.")
    call_command("mediabackup")  # LATE_EXP: Add other options
    bak_logger.info("backup_task: Backed up media files.")
    cel_logger.info("backup_task: Backed up database & media files.")


@app.task
def db_cleanup_task():
    """
    Cleans up the database.
    - Removes contributors with no contributions for privacy reasons.

    """
    cln_logger = Logger(name="db_cleanup", log_path=LOG_DIR)
    cln_logger.info("db_cleanup_task: Cleaning up database...")


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    # Calls backup_task() every BACKUP_INTERVAL.
    sender.add_periodic_task(BACKUP_INTERVAL, backup_task.s(), name="backup_task")

    # Calls db_cleanup_task() every DBCLEANUP_INTERVAL.
    sender.add_periodic_task(
        DBCLEANUP_INTERVAL, db_cleanup_task.s(), name="db_cleanup_task"
    )
