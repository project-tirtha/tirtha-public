"""
Email utility functions for sending various notifications in Tirtha.

"""

import logging
from typing import List, Optional

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

# Setup logging
logger = logging.getLogger(__name__)


def get_admin_emails() -> List[str]:
    """
    Get admin email addresses from settings.

    Returns:
        List[str]: List of admin email addresses

    """
    admin_emails = [email for name, email in getattr(settings, "ADMINS", [])]
    # Fallback to ADMIN_MAIL setting
    if not admin_emails:
        admin_emails = [
            settings.ADMIN_MAIL,
        ]
    return admin_emails


def send_contribution_processing_failure_email(
    contribution_id: str,
    site_id: str,
    site_name: str,
    contributor_email: str,
    processing_step: str,
    error_message: str,
    log_file_path: Optional[str] = None,
    run_id: Optional[str] = None,
    operation_type: Optional[str] = None,
) -> bool:
    """
    Send email notification to admins when contribution processing fails.

    Args:
        contribution_id: ID of the contribution that failed
        site_id: ID of the site being processed
        site_name: Name of the site being processed
        contributor_email: Email of the contributor
        processing_step: Name of the processing step that failed
        error_message: Error message from the exception
        log_file_path: Path to the log file (optional)
        run_id: ID of the run that failed (optional)
        operation_type: Type of operation (ImageOps, MeshOps, GSOps) (optional)

    Returns:
        bool: True if email sent successfully, False otherwise

    """
    logger.info(
        f"Sending contribution processing failure notification for {contribution_id}"
    )

    # Prepare email content
    subject = f"Contribution Processing Failed: {site_name} - {processing_step}"

    # Template context for rendering emails
    context = {
        "contribution_id": contribution_id,
        "site_id": site_id,
        "site_name": site_name,
        "contributor_email": contributor_email,
        "processing_step": processing_step,
        "error_message": error_message,
        "log_file_path": log_file_path,
        "run_id": run_id,
        "operation_type": operation_type,
        "contribution_admin_url": f"{settings.BASE_URL}/admin/tirtha/contribution/{contribution_id}/change/",
        "site_admin_url": f"{settings.BASE_URL}/admin/tirtha/site/{site_id}/change/",
        "base_admin_url": f"{settings.BASE_URL}/admin/",
    }

    # Render email templates
    html_message = None
    try:
        html_message = render_to_string(
            "tirtha/emails/contribution_processing_failure.html", context
        )
        text_message = render_to_string(
            "tirtha/emails/contribution_processing_failure.txt", context
        )
    except Exception as e:
        logger.error(f"Failed to render processing failure email templates: {e}")
        # Fallback to plain text message
        text_message = f"""
Contribution processing has failed in Tirtha:

Heritage Site: {site_name} ({site_id})
Contributor: {contributor_email}
Processing Step: {processing_step}
Operation Type: {operation_type or "Unknown"}
Run ID: {run_id or "N/A"}

Error Details:
{error_message}

Log File: {log_file_path or "Not available"}

View contribution: {settings.BASE_URL}/admin/tirtha/contribution/{contribution_id}/change/
View Heritage Site: {settings.BASE_URL}/admin/tirtha/site/{site_id}/change/
Admin Panel: {settings.BASE_URL}/admin/

Please investigate this failure as it may require manual intervention.

Best regards,
Tirtha System
        """.strip()

    # Get admin emails from settings
    admin_emails = get_admin_emails()

    try:
        # Create email message
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", settings.ADMIN_MAIL),
            to=admin_emails,
        )

        # Add HTML version if available
        if html_message:
            email.attach_alternative(html_message, "text/html")

        email.send(fail_silently=False)
        logger.info(
            f"Processing failure notification sent successfully for contribution: {contribution_id}"
        )
        return True

    except Exception as e:
        logger.error(
            f"Failed to send processing failure notification for contribution {contribution_id}: {e}"
        )
        return False


def send_image_processing_failure_email(
    contribution_id: str,
    site_id: str,
    site_name: str,
    contributor_email: str,
    error_message: str,
    log_file_path: Optional[str] = None,
    processing_step: str = "Image Processing",
) -> bool:
    """
    Send email notification for ImageOps processing failures.

    Args:
        contribution_id: ID of the contribution that failed
        site_id: ID of the site being processed
        site_name: Name of the site being processed
        contributor_email: Email of the contributor
        error_message: Error message from the exception
        log_file_path: Path to the log file (optional)
        processing_step: Which step failed. `tasks.post_save_contrib_imageops`
            passes "Image Processing (ImageOps)"; it had no parameter to land
            in, so every ImageOps failure raised `TypeError` *inside the
            notifier* and the admin mail was never sent. The caller's own
            `except Exception` then filed the signature mismatch as a
            contribution-lookup failure, which is how it survived: this is the
            ingest queue's only alerting path, and it has never once alerted.
            The production journal has it twice, on 2026-09-05, both times
            behind `SafetyUnavailable('nudenet is not installed')` -- the outage
            that stopped every upload being checked.

    Returns:
        bool: True if email sent successfully, False otherwise

    """
    return send_contribution_processing_failure_email(
        contribution_id=contribution_id,
        site_id=site_id,
        site_name=site_name,
        contributor_email=contributor_email,
        processing_step=processing_step,
        error_message=error_message,
        log_file_path=log_file_path,
        operation_type="ImageOps",
    )


def send_reconstruction_failure_email(
    contribution_id: str,
    site_id: str,
    site_name: str,
    contributor_email: str,
    processing_step: str,
    error_message: str,
    log_file_path: Optional[str] = None,
    run_id: Optional[str] = None,
    operation_type: Optional[str] = None,
) -> bool:
    """
    Send email notification for reconstruction processing failures (MeshOps/GSOps).

    Args:
        contribution_id: ID of the contribution that failed
        site_id: ID of the site being processed
        site_name: Name of the site being processed
        contributor_email: Email of the contributor
        processing_step: Name of the processing step that failed
        error_message: Error message from the exception
        log_file_path: Path to the log file (optional)
        run_id: ID of the run that failed (optional)
        operation_type: Type of operation (MeshOps, GSOps) (optional)

    Returns:
        bool: True if email sent successfully, False otherwise

    """
    return send_contribution_processing_failure_email(
        contribution_id=contribution_id,
        site_id=site_id,
        site_name=site_name,
        contributor_email=contributor_email,
        processing_step=processing_step,
        error_message=error_message,
        log_file_path=log_file_path,
        run_id=run_id,
        operation_type=operation_type,
    )


def send_contribution_processing_success_email(
    contribution_id: str,
    site_id: str,
    site_name: str,
    contributor_email: str,
    contributor_name: str,
    operation_type: str,
    run_id: Optional[str] = None,
    site_url: Optional[str] = None,
    processing_duration: Optional[str] = None,
    ark_url: Optional[str] = None,
    ark_id: Optional[str] = None,
) -> bool:
    """
    Send email notification to contributor when their contribution processing finishes successfully.

    Args:
        contribution_id: ID of the contribution that was processed
        site_id: ID of the site that was processed
        site_name: Name of the site that was processed
        contributor_email: Email of the contributor
        contributor_name: Name of the contributor
        operation_type: Type of operation (MeshOps, GSOps)
        run_id: ID of the run that completed (optional)
        site_url: Public URL to view the processed site (optional)
        processing_duration: How long the processing took (optional)
        ark_url: ARK URL for persistent access to this specific run (optional)
        ark_id: ARK ID for this specific run (optional)

    Returns:
        bool: True if email sent successfully, False otherwise

    """
    # Check if contributor emails are enabled
    if not getattr(settings, "MAIL_CONTRIB_TOGGLE", True):
        logger.info(
            f"Contributor success emails disabled - skipping notification for contribution: {contribution_id}"
        )
        return True

    logger.info(
        f"Sending contribution processing success notification for {contribution_id}"
    )

    # Prepare email content
    # Says what happened, rather than announcing it. The previous subject was
    # "Your Contribution to {site_name} is Ready!" -- an exclamation mark and a
    # capitalised noun phrase, over a body of ==== rules and a tagline about
    # preserving heritage for future generations. It read as marketing from a
    # platform rather than a note from a lab.
    subject = f"Your photographs of {site_name} are now a 3D model"

    # Template context for rendering emails
    context = {
        "contribution_id": contribution_id,
        "site_id": site_id,
        "site_name": site_name,
        "contributor_email": contributor_email,
        "contributor_name": contributor_name,
        "operation_type": operation_type,
        "run_id": run_id,
        "site_url": site_url or f"{settings.BASE_URL}/site/{site_id}/",
        "processing_duration": processing_duration,
        "ark_url": ark_url,
        "ark_id": ark_id,
        "arks_info_url": "https://arks.org/",
        "base_url": settings.BASE_URL,
    }

    # Render email templates
    html_message = None
    try:
        html_message = render_to_string(
            "tirtha/emails/contribution_processing_success.html", context
        )
        text_message = render_to_string(
            "tirtha/emails/contribution_processing_success.txt", context
        )
    except Exception as e:
        logger.error(f"Failed to render success email templates: {e}")
        # Fallback to plain text message
        operation_display = (
            "Photogrammetry"
            if operation_type == "aV"
            else "Gaussian Splatting"
            if operation_type == "GS"
            else operation_type
        )
        text_message = f"""
Dear {contributor_name},

Your contribution to the Tirtha platform has been successfully processed.

Contribution Details:
=====================
Heritage Site: {site_name} ({site_id})
Processing Type: {operation_display}
{f"Processing Time: {processing_duration}" if processing_duration else ""}

{
            f'''
ARK Persistent URL: {ark_url}
ARK ID: {ark_id}

The ARK (Archival Resource Key) provides a persistent URL to this specific snapshot 
of the heritage site's 3D model. This URL can be used for academic citations or 
long-term reference. Learn more about ARKs at: https://arks.org/
'''
            if ark_id
            else ""
        }

You can now view your processed contribution at:
{ark_url if ark_url else context["site_url"]}

Thank you for contributing to the preservation of cultural heritage!

Best regards,
The Tirtha Team
{settings.BASE_URL}
        """.strip()

    try:
        # Create email message
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", settings.ADMIN_MAIL),
            to=[contributor_email],
        )

        # Add HTML version if available
        if html_message:
            email.attach_alternative(html_message, "text/html")

        email.send(fail_silently=False)
        logger.info(
            f"Processing success notification sent successfully for contribution: {contribution_id}"
        )
        return True

    except Exception as e:
        logger.error(
            f"Failed to send processing success notification for contribution {contribution_id}: {e}"
        )
        return False


def send_admin_run_completion_email(
    contribution_id: str,
    site_id: str,
    site_name: str,
    contributor_email: str,
    contributor_name: str,
    operation_type: str,
    run_id: Optional[str] = None,
    processing_duration: Optional[str] = None,
    ark_url: Optional[str] = None,
    ark_id: Optional[str] = None,
) -> bool:
    """
    Send email notification to admins when a contribution run finishes successfully.

    Args:
        contribution_id: ID of the contribution that was processed
        site_id: ID of the site that was processed
        site_name: Name of the site that was processed
        contributor_email: Email of the contributor
        contributor_name: Name of the contributor
        operation_type: Type of operation (MeshOps, GSOps)
        run_id: ID of the run that completed (optional)
        processing_duration: How long the processing took (optional)
        ark_url: ARK URL for persistent access to this specific run (optional)
        ark_id: ARK ID for this specific run (optional)

    Returns:
        bool: True if email sent successfully, False otherwise

    """
    logger.info(f"Sending admin run completion notification for {contribution_id}")

    # Prepare email content
    subject = f"Run Completed Successfully: {site_name} - {operation_type}"

    # Template context for rendering emails
    context = {
        "contribution_id": contribution_id,
        "site_id": site_id,
        "site_name": site_name,
        "contributor_email": contributor_email,
        "contributor_name": contributor_name,
        "operation_type": operation_type,
        "run_id": run_id,
        "processing_duration": processing_duration,
        "ark_url": ark_url,
        "ark_id": ark_id,
        "arks_info_url": "https://arks.org/",
        "base_url": settings.BASE_URL,
        "contribution_admin_url": f"{settings.BASE_URL}/admin/tirtha/contribution/{contribution_id}/change/",
        "site_admin_url": f"{settings.BASE_URL}/admin/tirtha/site/{site_id}/change/",
        "base_admin_url": f"{settings.BASE_URL}/admin/",
    }

    # Render email templates
    html_message = None
    try:
        html_message = render_to_string(
            "tirtha/emails/admin_run_completion.html", context
        )
        text_message = render_to_string(
            "tirtha/emails/admin_run_completion.txt", context
        )
    except Exception as e:
        logger.error(f"Failed to render admin run completion email templates: {e}")
        # Fallback to plain text message
        operation_display = (
            "Photogrammetry"
            if operation_type == "aV"
            else "Gaussian Splatting"
            if operation_type == "GS"
            else operation_type
        )
        text_message = f"""
CONTRIBUTION RUN COMPLETED SUCCESSFULLY - TIRTHA PLATFORM

Hello Admin,

A contribution run has been completed successfully on the Tirtha platform.

Run Details:
============
Heritage Site: {site_name} ({site_id})
Contributor: {contributor_name} ({contributor_email})
Processing Type: {operation_display}
{f"Run ID: {run_id}" if run_id else ""}
{f"Processing Duration: {processing_duration}" if processing_duration else ""}

{
            f'''ARK Details:
==============
ARK ID: {ark_id}
ARK URL: {ark_url}
The ARK provides a persistent identifier for this specific run's output.
Learn more about ARKs: https://arks.org/'''
            if ark_id
            else ""
        }

Admin Actions:
==============
View Contribution: {context["contribution_admin_url"]}
View Heritage Site: {context["site_admin_url"]}
Admin Panel: {context["base_admin_url"]}

The processing completed successfully and the contributor has been notified.

Best regards,
Tirtha System
{settings.BASE_URL}
        """.strip()

    # Get admin emails from settings
    admin_emails = get_admin_emails()

    try:
        # Create email message
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", settings.ADMIN_MAIL),
            to=admin_emails,
        )

        # Add HTML version if available
        if html_message:
            email.attach_alternative(html_message, "text/html")

        email.send(fail_silently=False)
        logger.info(
            f"Admin run completion notification sent successfully for contribution: {contribution_id}"
        )
        return True

    except Exception as e:
        logger.error(
            f"Failed to send admin run completion notification for contribution {contribution_id}: {e}"
        )
        return False


# --- Telling the contributor, not just the admins ---------------------------

#: What a failing stage means, in words a contributor can act on. The stage
#: names come from `GSOps._run_order` / `MeshOps._run_order`.
#:
#: Deliberately not the exception text. The admin mail carries that, and it is
#: full of absolute paths, command lines and library internals -- none of which
#: belongs in a stranger's inbox, and some of which describes this server.
_FAILURE_GUIDANCE = {
    "run_colmap": (
        "The photographs could not be aligned with each other. This usually "
        "means there was not enough overlap between consecutive views, or the "
        "subject moved between them.",
        "Photographs taken every few steps around the subject, each overlapping "
        "the last by roughly two thirds, give alignment the best chance.",
    ),
    "run_frgs": (
        "The photographs aligned, but the model itself could not be built from them.",
        "This is usually about coverage: parts of the subject seen from only "
        "one angle cannot be reconstructed.",
    ),
    "run_meshopt": (
        "The model was built but could not be prepared for the web viewer.",
        "Nothing is wrong with your photographs; this one is ours to fix.",
    ),
    "run_postprocess": (
        "The model was built but could not be prepared for the web viewer.",
        "Nothing is wrong with your photographs; this one is ours to fix.",
    ),
    # The stages after the model exists.
    #
    # These were unmapped, so `.get` returned `(None, None)` and the template
    # printed only its unconditional closing paragraph -- "a failure is usually
    # about coverage" -- over an opening line saying no model could be built.
    # Both were false: `run_ark` failed on the Marcus Aurelius run *after*
    # COLMAP registered 97 of 97 images, FRGS trained for 2h8m and `scene.sog`
    # was written. The contributor was told to go back and re-photograph a
    # statue that had reconstructed correctly.
    "run_gaussian_cut": (
        "The model was built, but a step after it did not finish.",
        "Nothing is wrong with your photographs; this one is ours to fix.",
    ),
    "run_cleanup": (
        "The model was built, but a step after it did not finish.",
        "Nothing is wrong with your photographs; this one is ours to fix.",
    ),
    "run_ark": (
        "The model was built, but its permanent identifier could not be issued.",
        "Nothing is wrong with your photographs; this one is ours to fix.",
    ),
}

# The only two stages whose failure is genuinely about how the site was
# photographed. Everything else has already got past alignment and training, so
# advice about overlap and exposure is not just unhelpful there -- it is wrong,
# and it asks someone to redo work that succeeded.
_PHOTOGRAPHY_STAGES = frozenset({"run_colmap", "run_frgs"})

# Stages that only run once a model exists, so the mail must not open by saying
# none could be built.
_POST_MODEL_STAGES = frozenset(
    {"run_gaussian_cut", "run_meshopt", "run_postprocess", "run_cleanup", "run_ark"}
)


def send_contributor_run_failed_email(
    site_name: str,
    site_url: str,
    contributor_email: str,
    contributor_name: str,
    processing_step: str,
) -> bool:
    """
    Tell the contributor their batch did not reconstruct, and why.

    Separate from `send_contribution_processing_failure_email`, which goes to
    `get_admin_emails()` and whose body carries `/admin/...` links, the raw
    exception and a log path. That message is right for an operator and wrong
    for a contributor twice over: it leaks the admin surface, and it answers a
    question they did not ask.

    So this maps the failing *stage* to a sentence about photographs, and never
    forwards the exception. A contributor can act on "there was not enough
    overlap between views"; they can do nothing with a `CalledProcessError`.

    Gated on `MAIL_CONTRIB_TOGGLE`, like the success mail: a deployment that has
    not decided to write to contributors should not start doing so because a run
    failed.
    """
    if not getattr(settings, "MAIL_CONTRIB_TOGGLE", True):
        logger.info(
            "MAIL_CONTRIB_TOGGLE is off; not telling %s about the failed run.",
            contributor_email,
        )
        return True
    if not contributor_email:
        return False

    reason, guidance = _FAILURE_GUIDANCE.get(processing_step, (None, None))
    context = {
        "contributor_name": contributor_name or contributor_email.split("@")[0],
        "site_name": site_name,
        "site_url": site_url,
        "base_url": settings.BASE_URL,
        "reason": reason,
        "guidance": guidance,
        "about_photography": processing_step in _PHOTOGRAPHY_STAGES,
        "model_built": processing_step in _POST_MODEL_STAGES,
    }
    try:
        html_message = render_to_string(
            "tirtha/emails/contributor_run_failed.html", context
        )
        text_message = render_to_string(
            "tirtha/emails/contributor_run_failed.txt", context
        )
        email = EmailMultiAlternatives(
            subject=f"About your contribution to {site_name}",
            body=text_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[contributor_email],
        )
        email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=False)
        logger.info("Told %s that the run for %s failed.", contributor_email, site_name)
        return True
    except Exception:
        # Never let a notification failure become a pipeline failure: the run has
        # already failed, and the admin mail carries the detail regardless.
        logger.exception("Could not send the contributor failure mail.")
        return False


def send_new_contribution_email(contribution_id: str) -> bool:
    """
    Tell the admins a contribution arrived, and how many photographs it has.

    **Why this is here and not in `signals.py`.** It was a closure inside
    `notify_admin_new_contribution`, a `post_save` receiver on Contribution --
    and `views.upload` creates the Contribution on one line and stores the
    photographs on the next. So `images.count()` was 0 every time, and every
    admin notification this project has ever sent read "Number of Images: 0".
    `transaction.on_commit` did not help: with no atomic block around the view
    the INSERT commits at once and the callback runs immediately, still before a
    single file is written.

    Sent from the ingest queue now, after the files are on disk, so the count is
    the real one -- and so a mail host that accepts a connection and stops
    responding cannot hold a gunicorn worker for the length of an upload.

    Returns True when the mail was handed to the backend, like the rest of this
    module. Never raises: an upload that succeeded must not be reported as
    failed because the admins could not be told about it.
    """
    from .models import Contribution, Image

    try:
        contribution = Contribution.objects.select_related("site", "contributor").get(
            ID=contribution_id
        )
    except Contribution.DoesNotExist:
        logger.error(f"No contribution {contribution_id} to notify about.")
        return False

    contributor_email = contribution.contributor.email
    if contributor_email in getattr(settings, "CONTRIB_IGNORE_LIST", []):
        logger.info(f"Skipping contribution notification for {contributor_email}.")
        return False

    site = contribution.site
    image_count = contribution.images.count()

    context = {
        "contribution": contribution,
        "image_count": image_count,
        "total_images": Image.objects.filter(contribution__site=site).count(),
        "total_contributions": site.contributions.count(),
        "contribution_admin_url": (
            f"{settings.BASE_URL}/admin/tirtha/contribution/{contribution.ID}/change/"
        ),
        "site_admin_url": f"{settings.BASE_URL}/admin/tirtha/site/{site.ID}/change/",
        "base_admin_url": f"{settings.BASE_URL}/admin/",
    }

    subject = f"New contribution to {site.name}: {image_count} photograph(s)"

    html_message = None
    try:
        html_message = render_to_string(
            "tirtha/emails/new_contribution_notification.html", context
        )
        text_message = render_to_string(
            "tirtha/emails/new_contribution_notification.txt", context
        )
    except Exception as exc:
        logger.error(f"Failed to render the new-contribution templates: {exc}")
        text_message = (
            f"A new contribution has been added to Tirtha.\n\n"
            f"Heritage site: {site.name} ({site.ID})\n"
            f"Location: {site.district}, {site.state}, {site.country}\n"
            f"Contributor: {contribution.contributor.name or 'Unknown'} "
            f"({contributor_email})\n"
            f"Number of images: {image_count}\n\n"
            f"View contribution: {context['contribution_admin_url']}\n"
            f"View site: {context['site_admin_url']}\n"
        )

    try:
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", settings.ADMIN_MAIL),
            to=get_admin_emails(),
        )
        if html_message:
            email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=False)
        logger.info(
            f"Admin notified of contribution {contribution.ID} "
            f"({image_count} image(s))."
        )
        return True
    except Exception as exc:
        logger.error(f"Failed to notify admins of {contribution.ID}: {exc}")
        return False


def send_new_contributor_email(contributor_id: str) -> bool:
    """
    Tell the admins a new contributor signed up and needs activating.

    Moved here from `signals.notify_admin_new_contributor`, which sent it from
    inside the sign-in request (review 2026-11-22 §4). Run on the `ingest`
    queue by `tasks.notify_admin_new_contributor_task`.

    Never raises; returns True when the mail was handed to the backend.
    """
    from .models import Contributor

    try:
        contributor = Contributor.objects.get(ID=contributor_id)
    except (Contributor.DoesNotExist, ValueError):
        logger.error(f"No contributor {contributor_id} to notify about.")
        return False

    # Activated (or banned) in the meantime: nothing left to ask the admins.
    if contributor.active:
        logger.info(f"Contributor {contributor.email} is already active; not mailing.")
        return False

    admin_url = f"{settings.BASE_URL}/admin/tirtha/contributor/{contributor.ID}/change/"
    context = {
        "contributor": contributor,
        "admin_url": admin_url,
        "base_admin_url": f"{settings.BASE_URL}/admin/",
    }
    subject = f"New Contributor Sign-up: {contributor.name or contributor.email}"

    html_message = None
    try:
        html_message = render_to_string(
            "tirtha/emails/new_contributor_notification.html", context
        )
        text_message = render_to_string(
            "tirtha/emails/new_contributor_notification.txt", context
        )
    except Exception as exc:
        logger.error(f"Failed to render the new-contributor templates: {exc}")
        text_message = (
            "A new contributor has signed up for Tirtha and requires activation:\n\n"
            f"Name: {contributor.name or 'Not provided'}\n"
            f"Email: {contributor.email}\n"
            "Registration Time: "
            f"{contributor.created_at.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n"
            "Please log in to the admin panel to activate this contributor:\n"
            f"{admin_url}\n\n"
            "Best regards,\nTirtha System\n"
        )

    try:
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", settings.ADMIN_MAIL),
            to=get_admin_emails(),
        )
        if html_message:
            email.attach_alternative(html_message, "text/html")
        email.send(fail_silently=False)
        logger.info(f"Admin notified of new contributor {contributor.email}.")
        return True
    except Exception as exc:
        logger.error(
            f"Failed to send admin notification for new contributor "
            f"{contributor.email}: {exc}"
        )
        return False
