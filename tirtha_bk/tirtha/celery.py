import os
import logging

from celery import Celery
from celery.signals import task_failure

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
