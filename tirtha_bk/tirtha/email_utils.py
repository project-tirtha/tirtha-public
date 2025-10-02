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

Heritage Site: {mesh_name} ({mesh_id})
Contributor: {contributor_email}
Processing Step: {processing_step}
Operation Type: {operation_type or "Unknown"}
Run ID: {run_id or "N/A"}

Error Details:
{error_message}

Log File: {log_file_path or "Not available"}

View contribution: {settings.BASE_URL}/admin/tirtha/contribution/{contribution_id}/change/
View Heritage Site: {settings.BASE_URL}/admin/tirtha/mesh/{mesh_id}/change/
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


def send_contribution_processing_success_email(
    contribution_id: str,
    mesh_id: str,
    mesh_name: str,
    contributor_email: str,
    contributor_name: str,
    operation_type: str,
    run_id: Optional[str] = None,
    mesh_url: Optional[str] = None,
    processing_duration: Optional[str] = None,
    ark_url: Optional[str] = None,
    ark_id: Optional[str] = None,
) -> bool:
    """
    Send email notification to contributor when their contribution processing finishes successfully.

    Args:
        contribution_id: ID of the contribution that was processed
        mesh_id: ID of the mesh that was processed
        mesh_name: Name of the mesh that was processed
        contributor_email: Email of the contributor
        contributor_name: Name of the contributor
        operation_type: Type of operation (MeshOps, GSOps)
        run_id: ID of the run that completed (optional)
        mesh_url: Public URL to view the processed mesh (optional)
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
    subject = f"Your Contribution to {mesh_name} is Ready!"

    # Template context for rendering emails
    context = {
        "contribution_id": contribution_id,
        "mesh_id": mesh_id,
        "mesh_name": mesh_name,
        "contributor_email": contributor_email,
        "contributor_name": contributor_name,
        "operation_type": operation_type,
        "run_id": run_id,
        "mesh_url": mesh_url or f"{settings.BASE_URL}/mesh/{mesh_id}/",
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
Heritage Site: {mesh_name} ({mesh_id})
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
{ark_url if ark_url else context["mesh_url"]}

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
    mesh_id: str,
    mesh_name: str,
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
        mesh_id: ID of the mesh that was processed
        mesh_name: Name of the mesh that was processed
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
    subject = f"Run Completed Successfully: {mesh_name} - {operation_type}"

    # Template context for rendering emails
    context = {
        "contribution_id": contribution_id,
        "mesh_id": mesh_id,
        "mesh_name": mesh_name,
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
        "mesh_admin_url": f"{settings.BASE_URL}/admin/tirtha/mesh/{mesh_id}/change/",
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
Heritage Site: {mesh_name} ({mesh_id})
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
View Heritage Site: {context["mesh_admin_url"]}
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
