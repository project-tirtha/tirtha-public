"""Module-level entry points the Celery tasks call: `prerun_check` and `ops_runner`."""

from datetime import datetime

from django.conf import settings
from rich.console import Console

# Local imports
from tirtha.models import Contribution

from .common import MAIL_CONTRIB_TOGGLE, MESHOPS_MIN_IMAGES
from .gsops import GSOps
from .meshops import MeshOps


def prerun_check(contrib_id: str, recons_type: str) -> tuple[bool, str]:
    """
    Checks if a contribution is ready for processing.

    Parameters
    ----------
    contrib_id : str
        Contribution ID
    recons_type : str
        Reconstruction type
        Options: 'GS', 'aV'

    Returns
    -------
    tuple[bool, str]
        (status, message)

    """
    contrib = Contribution.objects.get(ID=contrib_id)
    site = contrib.site
    # Counted from the database, not from a directory listing. `images/good`
    # no longer exists (5.1), and the count that matters is "images we may
    # reconstruct from" -- everything not withheld by moderation. Images that
    # have not been checked yet are excluded: an unchecked image is not
    # publishable, so it must not make a site look ready (internal design notes).
    #
    # Asked of `reconstructable_images`, not re-derived here. This counted
    # `safety_status="safe"` and stopped there, so it did not exclude
    # `admin_verdict="reject"` -- and the run that follows *does*. A moderator
    # who rejected images therefore saw the gate still say a site was ready,
    # the run start, and then materialise fewer images than the gate promised,
    # or none at all. That is the same divergence 5.5 fixed between the gate
    # and the aV path; this is the last consumer still counting its own way.
    from tirtha.workspace import reconstructable_images

    images_count = reconstructable_images(site).count()

    # Check if site is already being processed or completed
    if site.completed:
        return False, "Site already completed."
    if site.status == "Processing":
        return False, "Site already processing."
    if images_count < MESHOPS_MIN_IMAGES:
        return (
            False,
            f"Not enough images to process site. Only {images_count} good images found.",
        )
    # if site.reconstructed_at and (contrib.processed_at < site.reconstructed_at):
    #     runs = site.runs.filter(status="Archived").order_by("-ended_at")
    #     if runs and (runs[0].kind == recons_type):
    #         return (
    #             False,
    #             f"Site already reconstructed using {recons_type} using current contribution.",
    #         )

    return True, "Site ready for processing."


def ops_runner(contrib_id: str, kind: str) -> None:
    """
    Runs the appropriate operations on a `models.Site` and publishes the results.

    Parameters
    ----------
    contrib_id : str
        Contribution ID
    kind : str
        Kind of operation to run
        Options: 'aV' (AliceVision) or 'GS' (Gaussian Splatting)

    """
    ops_map = {"aV": MeshOps, "GS": GSOps}
    OP = ops_map[kind]
    op_name = OP.__name__

    contrib = Contribution.objects.get(ID=contrib_id)
    siteID = str(contrib.site.ID)
    siteVID = str(contrib.site.verbose_id)

    cons = Console()  # This appears as normal printed logs in celery logs.
    cons.rule(f"{op_name} Runner Start")
    cons.print(
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Triggered by {contrib.ID} for Site {siteVID} <=> {siteID}."
    )
    cons.print(
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Starting {op_name} on Site {siteVID} <=> {siteID}."
    )

    try:
        op = OP(siteID=siteID, contrib_id=contrib_id)
        cons.print(f"Check {op.log_path} for more details.")

        # Store start time for calculating processing duration
        start_time = datetime.now()
        op._run_all()

        if getattr(op, "cancelled", False):
            cancel_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cancel_reason = getattr(op, "cancel_reason", "Run cancelled.")
            cons.print(
                f"{cancel_timestamp}: {op_name} cancelled for {siteVID} <=> {siteID}. {cancel_reason}"
            )
        else:
            # Calculate processing duration
            end_time = datetime.now()
            processing_duration = str(end_time - start_time).split(".")[
                0
            ]  # Remove microseconds

            cons.print(
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Finished {op_name} on {siteVID} <=> {siteID}."
            )

            # Send success email notification to contributor and admins
            try:
                from ..email_utils import (
                    send_admin_run_completion_email,
                    send_contribution_processing_success_email,
                )

                contributor_name = (
                    contrib.contributor.name or contrib.contributor.email.split("@")[0]
                )

                # Get ARK information if available
                ark_url = None
                ark_id = None
                if hasattr(op, "run") and op.run.ark:
                    ark_url = f"{settings.BASE_URL}/ark:/{op.run.ark.ark}"
                    ark_id = op.run.ark.ark

                # Conditionally send notification to contributor
                if MAIL_CONTRIB_TOGGLE:
                    send_contribution_processing_success_email(
                        contribution_id=contrib_id,
                        site_id=siteID,
                        site_name=contrib.site.name,
                        contributor_email=contrib.contributor.email,
                        contributor_name=contributor_name,
                        operation_type=kind,
                        run_id=str(op.runID) if hasattr(op, "runID") else None,
                        site_url=f"{settings.BASE_URL}/site/{siteID}/",
                        processing_duration=processing_duration,
                        ark_url=ark_url,
                        ark_id=ark_id,
                    )
                    cons.print(
                        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Success notification sent to {contrib.contributor.email}."
                    )

                # Send notification to admins
                send_admin_run_completion_email(
                    contribution_id=contrib_id,
                    site_id=siteID,
                    site_name=contrib.site.name,
                    contributor_email=contrib.contributor.email,
                    contributor_name=contributor_name,
                    operation_type=kind,
                    run_id=str(op.runID) if hasattr(op, "runID") else None,
                    processing_duration=processing_duration,
                    ark_url=ark_url,
                    ark_id=ark_id,
                )
                cons.print(
                    f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: Admin notification sent for run completion."
                )

            except Exception as email_error:
                cons.print(
                    f"Warning: Failed to send notification emails: {email_error}"
                )
                # NOTE: Don't raise the exception - email failure shouldn't break the processing

    except Exception as e:
        cons.print(
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: ERROR encountered in {op_name} for {siteVID} <=> {siteID}!"
        )
        cons.print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: {e}")

        # Send additional email notification if _handle_error didn't cover it
        # This covers cases where the error might occur outside of the BaseOps methods
        try:
            from ..email_utils import send_reconstruction_failure_email

            contributor_email = contrib.contributor.email

            send_reconstruction_failure_email(
                contribution_id=contrib_id,
                site_id=siteID,
                site_name=contrib.site.name,
                contributor_email=contributor_email,
                processing_step="ops_runner",
                error_message=str(e),
                log_file_path=str(op.log_path)
                if "op" in locals() and hasattr(op, "log_path")
                else None,
                run_id=str(op.runID)
                if "op" in locals() and hasattr(op, "runID")
                else None,
                operation_type=kind,
            )
        except Exception as email_error:
            cons.print(f"Failed to send failure notification email: {email_error}")

        raise e
    cons.rule(f"{op_name} Runner End")
