"""
Email utility functions for sending various notifications in Tirtha.

"""

import logging
from typing import Optional, List

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
    mesh_id: str,
    mesh_name: str,
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
        mesh_id: ID of the mesh being processed
        mesh_name: Name of the mesh being processed
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
    subject = f"Contribution Processing Failed: {mesh_name} - {processing_step}"

    # Template context for rendering emails
    context = {
        "contribution_id": contribution_id,
        "mesh_id": mesh_id,
        "mesh_name": mesh_name,
        "contributor_email": contributor_email,
        "processing_step": processing_step,
        "error_message": error_message,
        "log_file_path": log_file_path,
        "run_id": run_id,
        "operation_type": operation_type,
        "contribution_admin_url": f"{settings.BASE_URL}/admin/tirtha/contribution/{contribution_id}/change/",
        "mesh_admin_url": f"{settings.BASE_URL}/admin/tirtha/mesh/{mesh_id}/change/",
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

Contribution ID: {contribution_id}
CH Site: {mesh_name} ({mesh_id})
Contributor: {contributor_email}
Processing Step: {processing_step}
Operation Type: {operation_type or "Unknown"}
Run ID: {run_id or "N/A"}

Error Details:
{error_message}

Log File: {log_file_path or "Not available"}

View contribution: {settings.BASE_URL}/admin/tirtha/contribution/{contribution_id}/change/
View CH Site: {settings.BASE_URL}/admin/tirtha/mesh/{mesh_id}/change/
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
    mesh_id: str,
    mesh_name: str,
    contributor_email: str,
    error_message: str,
    log_file_path: Optional[str] = None,
) -> bool:
    """
    Send email notification for ImageOps processing failures.

    Args:
        contribution_id: ID of the contribution that failed
        mesh_id: ID of the mesh being processed
        mesh_name: Name of the mesh being processed
        contributor_email: Email of the contributor
        error_message: Error message from the exception
        log_file_path: Path to the log file (optional)

    Returns:
        bool: True if email sent successfully, False otherwise

    """
    return send_contribution_processing_failure_email(
        contribution_id=contribution_id,
        mesh_id=mesh_id,
        mesh_name=mesh_name,
        contributor_email=contributor_email,
        processing_step="Image Processing",
        error_message=error_message,
        log_file_path=log_file_path,
        operation_type="ImageOps",
    )


def send_reconstruction_failure_email(
    contribution_id: str,
    mesh_id: str,
    mesh_name: str,
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
        mesh_id: ID of the mesh being processed
        mesh_name: Name of the mesh being processed
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
        mesh_id=mesh_id,
        mesh_name=mesh_name,
        contributor_email=contributor_email,
        processing_step=processing_step,
        error_message=error_message,
        log_file_path=log_file_path,
        run_id=run_id,
        operation_type=operation_type,
    )
