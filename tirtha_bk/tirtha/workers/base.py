"""`BaseOps`: run and stage tracking, errors, cancellation, publish, ARK, finalize."""

import json
import os
import shlex
import shutil
from datetime import datetime
from pathlib import Path
from subprocess import CalledProcessError
from typing import Optional

import pytz
from django.db import IntegrityError
from django.utils import timezone
from tirtha_recon import Logger

# Local imports
from tirtha.models import ARK, Contribution, Run, Site

from ..utilsark import generate_noid, noid_check_digit
from .common import (
    ARCHIVE_ROOT,
    ARK_NAAN,
    ARK_SHOULDER,
    BASE_URL,
    GS_EMIT_SIBLING,
    GS_SIBLING_SUFFIX,
    LOG_DIR,
    MEDIA,
    STATIC,
    SUBPROCESS_TIMEOUT_S,
)


class RunCancelledError(Exception):
    """
    Raised when a run is intentionally cancelled.

    """


class BaseOps:
    def __init__(self, siteID: str, kind: str = "aV", contrib_id: str = None) -> None:
        """
        Base class for all operations

        Parameters
        ----------
        siteID : str
            Site ID
        kind : str
            Kind of operation, one of ['aV', 'GS'], by default 'aV'
        contrib_id : str, optional
            Contribution ID that triggered this operation
        """
        self.siteID = siteID
        self.site = site = Site.objects.get(ID=siteID)
        self.siteVID = site.verbose_id
        self.siteStr = f"{self.siteVID} <=> {self.siteID}"  # Used in logging
        self.contrib_id = contrib_id
        self.cancelled = False
        self.cancel_reason = None

        # Create new Run
        self.kind = kind
        self.run = run = Run.objects.create(site=site, kind=kind)
        run.save()  # Creates run directory
        self.runID = runID = run.ID
        # Ensure run.directory is relative path for new runs
        run_dir_path = Path(run.directory)
        if run_dir_path.is_absolute():
            # This should not happen for new runs, but handle gracefully
            raise ValueError(
                f"New run directory should be relative, got absolute path: {run.directory}"
            )
        self.runDir = STATIC / "models" / run_dir_path

        # Set up Logger
        self.log_path = LOG_DIR / f"{kind}Ops/{siteID}" / self.runDir.stem
        if not self.log_path.exists():
            self.log_path.mkdir(parents=True, exist_ok=True)

        self.cls = cls = self.__class__
        # NOTE: Attributes with self.__class__ will only work if the class is instantiated.
        cls.logger = Logger(log_path=self.log_path, name=f"{cls.__name__}_{runID[:8]}")
        cls.logger.info(
            f"ID {siteID} has Verbose ID (VID) {self.siteVID}. Using VID for logging."
        )
        # Get contribution object if contrib_id provided
        self.contribution = None
        if contrib_id:
            try:
                self.contribution = Contribution.objects.get(ID=contrib_id)
            except Contribution.DoesNotExist:
                # Log warning but continue
                cls.logger.warning(f"Contribution with ID {contrib_id} does not exist.")

        # Source (images) & run directories.
        # Was `images/good`, which no longer exists: Phase 5.1 replaced the
        # good/bad label folders with measurements, leaving only `images/` and
        # the `images/nsfw` quarantine (internal design notes). Rejected content is
        # excluded because it is physically elsewhere, not because of a filter
        # here. Unit 5.5 replaces this glob with a DB-driven materialisation of
        # ordered symlinks (internal design notes).
        self.imageDir = MEDIA / f"models/{siteID}/images"

        cls.logger.info(f"Created new {kind} Run {runID} for site {self.siteStr}.")
        cls.logger.info(f"{kind} Run directory: {self.runDir}")
        cls.logger.info(f"{kind} Run log file: {cls.logger._log_file}")
        self._update_site_status("Processing")

        """
        The images the database says are usable -- not a directory listing.

        This globbed `images/` and looked up whatever it found. That set is what
        `run.images.set()` below records as the run's inputs, and what the ARK's
        image count is derived from, so an ARK asserted a count that included
        photographs a moderator had rejected and photographs nothing had ever
        checked. The reconstruction and its archival record disagreed with the
        moderation tools, silently, on both pipelines.

        `reconstructable_images` is the one place that answers "which images may
        we reconstruct from", and it is now the only source here.
        """
        from tirtha.workspace import reconstructable_images

        usable = list(reconstructable_images(self.site))
        self.imageUUIDs = [str(image.ID) for image in usable]
        # Files only: `images/nsfw/` sits inside this directory and must never
        # be fed to a reconstruction.
        by_stem = {p.stem: p for p in self.imageDir.glob("*") if p.is_file()}
        self.imageFiles = [by_stem[uuid] for uuid in self.imageUUIDs if uuid in by_stem]
        try:
            self.images = usable
            self.contributions = [image.contribution for image in self.images]
            self.contributors = [
                contribution.contributor for contribution in self.contributions
            ]

            # Set the `images` & `contributors` attributes of the Run
            # CHECK: Test `aset()`
            run.images.set(self.images)
            run.contributors.set(self.contributors)
            run.save()
            cls.logger.info(
                f"Set the `images` & `contributors` attributes of {kind} Run {runID}."
            )
        except Exception as e:
            self._handle_error(excep=e, caller="Fetching images & contributors")

        # Filled by stage methods via record_stage_metrics / _artifacts and
        # drained into the RunStage row when the stage closes.
        self._stage_metrics: dict = {}
        self._stage_artifacts: list = []

        # Specify the run order (else alphabetical order)
        self._run_order_suffix = [
            "run_cleanup",
            "run_ark",
            "run_finalize",
        ]

    def record_stage_metrics(self, **metrics) -> None:
        """
        Attach measurements to the stage currently running.

        Stage methods call this instead of returning values, because they are
        driven by name from `_run_order` and there is nowhere for a return
        value to go. Anything recorded here lands in `RunStage.metrics`
        (internal design notes).
        """
        self._stage_metrics.update(metrics)

    def record_stage_artifacts(self, *paths) -> None:
        """
        Declare what this stage produced.

        Resume reads these: a stage is skippable only when its row says
        Completed *and* its declared artifacts still exist on disk (10.4).
        """
        self._stage_artifacts.extend(str(p) for p in paths)

    def _run_all(self) -> None:
        """
        Runs all the steps with default parameters, recording each as a
        `RunStage` (internal design notes).

        Wrapped here rather than inside each stage method: every reconstruction
        goes through this loop, so one place covers both pipelines and every
        stage, and a new stage is tracked by being added to `_run_order`.

        Raises
        ------
        ValueError
            If `_run_order` is not defined
        Exception
            If any exception is raised during the execution

        """
        step_name = None
        try:
            if not self._run_order:
                raise ValueError("_run_order is not defined.")

            for ordinal, step in enumerate(self._run_order):
                step_name = step
                stage = self._begin_stage(step, ordinal)
                try:
                    getattr(self, step)()
                except Exception:
                    self._end_stage(stage, "Failed")
                    raise
                self._end_stage(stage, "Completed")
        except RunCancelledError as cancel_err:
            self.logger.info(
                f"Run cancelled during '{step_name or 'unknown'}': {cancel_err}"
            )
        except Exception as e:
            self._handle_error(excep=e, caller="_run_all")

    def _begin_stage(self, name: str, ordinal: int):
        """
        Open a stage row.

        Never fails the run: this is observability, and losing a reconstruction
        because its bookkeeping row could not be written would invert the
        priority entirely.
        """
        self._stage_metrics = {}
        self._stage_artifacts = []
        try:
            from tirtha.models import RunStage

            # A re-run of a stage on the same Run (the admin re-cut) is a new
            # attempt: `attempt=1` again violated runstage_unique_per_run_attempt
            # and the row was silently lost.
            previous = (
                RunStage.objects.filter(run=self.run, name=name)
                .order_by("-attempt")
                .values_list("attempt", flat=True)
                .first()
            )
            stage = RunStage.objects.create(
                run=self.run,
                name=name,
                ordinal=ordinal,
                attempt=(previous or 0) + 1,
            )
            stage.start(log_path=str(self.logger._log_file))
            return stage
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"Could not record stage {name}: {exc}")
            return None

    def _end_stage(self, stage, status: str) -> None:
        if stage is None:
            return
        try:
            stage.finish(
                status,
                metrics=dict(self._stage_metrics),
                artifacts=list(self._stage_artifacts),
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"Could not close stage {stage.name}: {exc}")

    def _check_output(self, out: Path, src: str) -> None:
        if not out.is_file():
            self._handle_error(
                FileNotFoundError(f"No output was produced by {src} at {out}."),
                src,
            )

    def _handle_error(self, excep: Exception, caller: str) -> None:
        """
        Handles errors during the worker execution

        Parameters
        ----------
        excep : Exception
            The exception that was raised
        caller : str
            The name of the function that raised the exception

        Raises
        ------
        excep : Exception
            The exception that was raised

        """
        # Once per run, however many frames catch the same exception.
        #
        # A stage calls this and it ends by re-raising; `_run_all` catches that
        # and calls it again with caller="_run_all". So every failure ran the
        # whole handler twice: two admin mails, two contributor mails, and the
        # second pass overwrote `run.notes` -- replacing the stage that actually
        # failed ("run_colmap") with the frame that re-caught it ("_run_all"),
        # which is the one piece of forensic detail the operator needs.
        #
        # The second contributor mail was also the *wrong* mail: `caller` misses
        # `_FAILURE_GUIDANCE`, so someone whose run died because `colmap` was not
        # on the unit's PATH was told, twice, that their photographs lacked
        # overlap.
        #
        # Re-raise rather than return: the exception must keep propagating, and
        # callers of this method rely on it never returning normally.
        if getattr(self, "_error_handled", False):
            self.logger.error(
                f"{caller} re-caught an already-handled failure; not repeating "
                "notifications."
            )
            raise excep
        self._error_handled = True

        # Log the error
        self.logger.error(f"{caller} step failed.")
        self.logger.error(f"{excep}", exc_info=True)

        # The run is Error; the site is Error only if it has nothing to show.
        # The run first, so the site's settlement sees it as finished.
        self.run.ended_at = timezone.now()
        log_file_info = (
            f"Log file: {self.logger._log_file}"
            if hasattr(self.logger, "_log_file")
            else "Log file: Not available"
        )
        self.run.notes = f"Error in {caller}:\n{str(excep)}\n\n{log_file_info}"
        self.run.save()
        self._update_run_status("Error")
        self._settle_site_after_failure()
        # Send email notification to admin about the failure
        try:
            from ..email_utils import send_reconstruction_failure_email

            # Get contribution and contributor details - use exact contribution if available
            contribution_id, contributor_email = self._resolve_contribution_details()

            send_reconstruction_failure_email(
                contribution_id=contribution_id,
                site_id=str(self.site.ID),
                site_name=self.site.name,
                contributor_email=contributor_email,
                processing_step=caller,
                error_message=str(excep),
                log_file_path=str(self.logger._log_file)
                if hasattr(self.logger, "_log_file")
                else None,
                run_id=str(self.runID),
                operation_type=self.kind + "Ops",
            )
        except Exception as email_error:
            self.logger.error(
                f"Failed to send failure notification email: {email_error}",
                exc_info=True,
            )

        # And tell the contributor, in their own terms.
        #
        # The mail above goes to `get_admin_emails()` and carries `/admin/...`
        # links, the exception text and a log path -- right for an operator,
        # wrong for the person who took the photographs. Until now they were
        # told nothing at all: a batch that failed simply never became a model
        # and nobody said why.
        #
        # In its own `try`, because a contributor who cannot be emailed must not
        # stop the admin mail, and neither must stop the exception below from
        # propagating.
        try:
            from ..email_utils import send_contributor_run_failed_email

            contrib = self.contribution
            if contrib is not None and contrib.contributor is not None:
                send_contributor_run_failed_email(
                    site_name=self.site.name,
                    site_url=f"{BASE_URL}/site/{self.site.ID}/",
                    contributor_email=contrib.contributor.email,
                    contributor_name=contrib.contributor.name,
                    processing_step=caller,
                )
        except Exception as contrib_mail_error:
            self.logger.error(
                f"Failed to notify the contributor: {contrib_mail_error}",
                exc_info=True,
            )

        raise excep

    def _resolve_contribution_details(self) -> tuple[str, str]:
        """
        Return best-effort contribution ID and contributor email for notifications.

        """

        contribution_id = "Unknown"
        contributor_email = "Unknown"

        if self.contribution:
            # Use the exact contribution that triggered this operation
            contribution_id = str(self.contribution.ID)
            contributor_email = self.contribution.contributor.email
            return contribution_id, contributor_email

        if self.contrib_id:
            # Fallback: use contrib_id string
            contribution_id = str(self.contrib_id)
            # Try to get contributor email
            try:
                contrib = Contribution.objects.get(ID=self.contrib_id)
                contributor_email = contrib.contributor.email
            except Contribution.DoesNotExist:
                pass
            return contribution_id, contributor_email

        # Fallback: the site's contributions. (A `self.run.contributions` branch
        # stood here; Run has no such relation, so it raised AttributeError and
        # the caller's `try` swallowed it -- the admin failure mail was never sent.)
        site_contributions = self.run.site.contributions
        if site_contributions.exists():
            latest_contribution = site_contributions.first()
            if latest_contribution:
                contribution_id = str(latest_contribution.ID)
                contributor_email = latest_contribution.contributor.email

        return contribution_id, contributor_email

    def _cancel_run(
        self,
        *,
        reason: str,
        caller: str,
        log_excerpt: str,
        log_file_path: Optional[Path] = None,
    ) -> None:
        """
        Cancel the current run, notify admins, and raise a cancellation error.

        """
        self.logger.warning(reason)
        if log_excerpt:
            self.logger.warning("COLMAP log excerpt:\n%s", log_excerpt)

        self.run.ended_at = timezone.now()
        log_file_info = (
            f"Log file: {log_file_path}" if log_file_path else "Log file: Not available"
        )
        notes_content = f"Cancelled: {reason}\n\n{log_file_info}"
        if log_excerpt:
            notes_content += f"\n\nCOLMAP log excerpt:\n{log_excerpt}"
        self.run.notes = notes_content
        self.run.save()
        self._update_run_status("Cancelled")
        self._settle_site_after_failure()

        try:
            from ..email_utils import send_reconstruction_failure_email

            contribution_id, contributor_email = self._resolve_contribution_details()
            error_message = reason
            if log_excerpt:
                error_message = f"{reason}\n\n{log_excerpt}"

            send_reconstruction_failure_email(
                contribution_id=contribution_id,
                site_id=str(self.site.ID),
                site_name=self.site.name,
                contributor_email=contributor_email,
                processing_step=caller,
                error_message=error_message,
                log_file_path=str(log_file_path) if log_file_path else None,
                run_id=str(self.runID),
                operation_type=self.kind + "Ops",
            )
        except Exception as email_error:
            self.logger.error(
                f"Failed to send cancellation notification email: {email_error}",
                exc_info=True,
            )

        self.cancelled = True
        self.cancel_reason = reason

        raise RunCancelledError(reason)

    def _settle_site_after_failure(self) -> None:
        """
        Set the site's status after this run failed or was cancelled.

        This used to write Error unconditionally, so a Live site whose nightly
        re-run failed dropped off the public map although its published model
        was still there and still served. `celery.restore_site_status` is the
        rule the orphaned-run sweep already uses: a site with a published,
        visible run is Live. One without stays Error, as before -- not Pending,
        which would tell the operator nothing went wrong. A site another run is
        still Processing is left to that run.

        Call it after this run's own status has left Processing.
        """
        from ..celery import PUBLISHED_RUN_STATUSES, restore_site_status

        if restore_site_status(self.site.ID) == "Live":
            self.site.status = "Live"
            self.logger.info(f"Site {self.siteStr} has a published run; left it Live.")
            return
        if Run.objects.filter(site_id=self.site.ID, status="Processing").exists():
            return
        published = Run.objects.filter(
            site_id=self.site.ID, status__in=PUBLISHED_RUN_STATUSES, hidden=False
        ).exists()
        self._update_site_status("Live" if published else "Error")

    def _update_site_status(self, status: str, **fields) -> None:
        """
        Write the site's status (and any `fields`) without touching its other columns.

        This was `self.site.status = status; self.site.save()` on a Site loaded
        when the run started, possibly hours earlier, so every status change
        wrote back the whole stale row: a name, description or `hidden` flag an
        admin changed during the run was silently reverted (review 2026-11-22
        §1 #8).

        A queryset `update()` writes only these columns. `updated_at` is
        `auto_now`, which `update()` does not apply, so it is set explicitly.
        `update()` also skips `Site.save()` and `post_save`, and that is the
        point rather than a side effect to work around:

        * `Site.save()` re-encodes the thumbnail on every call (lossy, and it
          raises when the file is missing -- which would turn a status write in
          `_handle_error` or `run_finalize` into a second failure), and
          recomputes `verbose_id` from the in-memory (stale) name.
        * The only `post_save` receiver for Site (`signals.post_save_site`)
          creates the site's media/static directories. They exist from the
          site's creation; the one this pipeline writes into,
          `STATIC/models/<site>/published`, is created where it is written
          (`_publish_artifacts`).

        The in-memory Site gets the new values, so later reads in this run see
        what was written.
        """
        values = {"status": status, **fields}
        values["updated_at"] = timezone.now()
        Site.objects.filter(pk=self.site.pk).update(**values)
        for name, value in values.items():
            setattr(self.site, name, value)
        self.logger.info(
            f"Updated site.status to '{status}' for site {self.siteStr}..."
        )

    def _update_run_status(self, status: str) -> None:
        """
        Updates the run status in the DB

        Parameters
        ----------
        status : str
            The status to update the run to

        """
        self.run.status = status
        self.run.save()
        self.logger.info(
            f"Updated {self.kind} run.status to '{status}' for run {self.runID}..."
        )

    @classmethod  # NOTE: Won't be picklable as a regular method
    def _serialRunner(
        cls, cmd, log_file: Path, timeout: Optional[float] = None, capture: bool = False
    ):
        """
        Run a command, streaming its output into the log file as it runs.

        Parameters
        ----------
        cmd : Sequence[str] | str
            The argv to run. A string is split with `shlex.split` -- there is no
            shell any more, so a path with a space or a quote cannot change what
            runs (review 2026-11-22 §1 #5, and the September review's
            "unquoted shell=True").
        log_file : Path
            Path to the log file
        timeout : float, optional
            Seconds before the command's whole process group is killed and
            `tirtha_recon.proc.StageTimeout` is raised (the run is then marked
            Error by `_handle_error`). Default `SUBPROCESS_TIMEOUT_S`.
        capture : bool
            Also return the output as one string (for short outputs such as
            `model_analyzer`'s).

        This buffered everything with `check_output` and logged it at the end,
        so a hung COLMAP mapper left an empty log, and nothing ever stopped it.
        """
        from tirtha_recon.proc import StageTimeout, run_streamed

        argv = shlex.split(cmd) if isinstance(cmd, str) else [str(a) for a in cmd]
        timeout = SUBPROCESS_TIMEOUT_S if timeout is None else timeout
        logger = Logger(log_file.stem, log_file.parent)
        log_path = Path(log_file).resolve()
        captured: list[str] = []

        def on_line(line: str) -> None:
            logger.info(line)
            if capture:
                captured.append(line)

        try:
            cls.logger.info(f"Starting command execution. Log file: {log_path}.")
            logger.info(f"Command:\n{shlex.join(argv)}")
            try:
                returncode = run_streamed(argv, timeout=timeout, on_line=on_line)
            except StageTimeout as timed_out:
                logger.error(str(timed_out))
                cls.logger.error(
                    f"{logger.name} timed out after {timeout} s. Check log file: {log_path}."
                )
                timed_out.add_note(
                    f"{logger.name} timed out. Check log file: {log_path}."
                )
                raise
            if returncode != 0:
                error = CalledProcessError(returncode, argv)
                logger.error(f"Exited with status {returncode}.")
                cls.logger.error(
                    f"Error in command execution for {logger.name}. Check log file: {log_path}."
                )
                # NOTE: Won't appear in VSCode's Jupyter Notebook (May 2024)
                error.add_note(f"{logger.name} failed. Check log file: {log_path}.")
                raise error
            cls.logger.info(f"Finished command execution. Log file: {log_path}.")
        finally:
            # A new handler is opened per call; close it, or each stage leaks a
            # file descriptor for the life of the worker.
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
        return "\n".join(captured) if capture else None

    def run_cleanup(self) -> None:
        """
        Does the following:
        1. Cleans up older errored-out runs.
        2. Copies current run's output to "published".
        2. Archives current run.
        NOTE: Errored-out runs are not deleted during their run, but only during the next run.
        This is done to allow for debugging.

        """
        kind = self.kind
        # GS publishes SOG now, not .splat: FRGS emits PLY and splat-transform
        # encodes it (internal design notes); `_publish_artifacts` maps kind to file.
        siteStr = self.siteStr
        curr_runID = self.runID
        arcDir = ARCHIVE_ROOT / self.siteID / f"{kind.lower()}cache" / self.runDir.stem

        self.logger.info(f"Cleaning up runs for site {siteStr}.")
        try:
            # 1. Delete errored-out and cancelled runs (all kinds).
            #
            # Cancelled runs (registration gate) were never pruned, so their
            # directories -- a full COLMAP + training workspace each -- piled up
            # (review 2026-11-22 §3). They are pruned exactly like Error runs:
            # kept until the next successful run of the site, for debugging.
            self.logger.info(
                f"Looking up & deleting errored-out/cancelled runs for site {siteStr}..."
            )
            runs = (
                Run.objects.filter(site=self.site, status__in=["Error", "Cancelled"])
                .exclude(ID=curr_runID)
                .order_by("-ended_at")
            )
            if len(runs) > 0:
                for run in runs:
                    # Handle both relative and absolute paths in run.directory
                    run_dir_path = Path(run.directory)
                    if run_dir_path.is_absolute():
                        # Already an absolute path (archived run)
                        runDir = run_dir_path
                    else:
                        # Relative path (active run)
                        runDir = STATIC / "models" / run_dir_path

                    self.logger.info(
                        f"{run.kind} Run {run.ID} for site {siteStr} has errors. Deleting..."
                    )
                    if runDir.exists():
                        shutil.rmtree(runDir)  # Delete folder
                    run.delete()  # Delete from DB
                    self.logger.info(
                        f"Deleted {run.kind} run {run.ID} for site {siteStr}."
                    )
            self.logger.info(
                f"Deleted {len(runs)} errored-out runs for site {siteStr}."
            )

            # 2. Copy current run's output to STATIC / "models" / siteID / "published" / output_name
            self._publish_artifacts()
            # 3. Move everything else to arcDir
            self.logger.info(
                f"Archiving {kind} run {curr_runID} for site {siteStr} to {arcDir}."
            )
            shutil.move(self.runDir, arcDir)  # Move run folder to archive
            # Store relative path to avoid creating nested directory structures in STATIC
            relative_arc_path = arcDir.relative_to(ARCHIVE_ROOT)
            self.run.directory = str(relative_arc_path)  # Update run directory
            self._update_run_status("Archived")  # Update run status & save
            self.logger.info(
                f"Archived {kind} run {curr_runID} for site {siteStr} to {arcDir}."
            )

        except Exception as e:
            self.logger.error(f"Error cleaning up runs for site {siteStr}.")
            self._handle_error(e, "run_cleanup")
        self.logger.info(f"Finished cleaning up runs for site {siteStr}.")

    @staticmethod
    def _atomic_copy(src: Path, dest: Path) -> None:
        """Copy beside `dest`, then rename over it: a visitor never downloads a half-written file."""
        tmp = dest.with_name(f".{dest.name}.tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)

    def _publish_artifacts(self) -> Path:
        """
        Copy the run's outputs from `self.opt_path` into the served tree,
        `STATIC/models/<site>/published/<site>_<run><ext>`, and set `self.arkURL`.

        Used by `run_cleanup` and by the admin re-cut (`GSOps.run_postprocess`
        on a run bound with `for_existing_run`), so both publish the same way.
        """
        kind = self.kind
        curr_runID = self.runID
        out_type = {"aV": ".glb", "GS": ".sog", "Point": ".ply"}[kind]
        out_file = {
            "aV": "decimatedOptGLB.glb",
            # splat-transform writes this (internal design notes); the old
            # postprocessed.splat came from the deleted PostProcess module.
            "GS": "scene.sog",
            "Point": "Point_Voxel.ply",
        }[kind]
        self.logger.info(
            f"Copying output for {kind} run {curr_runID} for site {self.siteStr}..."
        )
        src = self.opt_path / out_file
        self.arkURL = (
            f"models/{self.siteID}/published/{self.siteID}_{curr_runID}{out_type}"
        )
        dest = STATIC / self.arkURL
        # `signals.post_save_site` creates this when the site is created; status
        # writes no longer go through `Site.save()`, so do not rely on it here.
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not src.is_file():
            raise FileNotFoundError(f"No {out_file} to publish for run {curr_runID}.")

        # The viewer-readable sibling, next to the published artifact.
        #
        # **Required** (review 2026-11-22 §3). The React viewer cannot read
        # `.sog`; a site published without its `.splat` showed an empty canvas
        # while the run reported success. So a missing sibling now fails the
        # run, and it is checked and copied *before* the `.sog`, so a failure
        # cannot leave a new `.sog` beside no `.splat`.
        #
        # `run_postprocess` writes it; without this copy it went straight to the
        # archive with the run directory, never reaching the tree `api._sibling`
        # looks in.
        if kind == "GS" and GS_EMIT_SIBLING:
            sibling_src = self.opt_path / f"scene{GS_SIBLING_SUFFIX}"
            if not sibling_src.is_file():
                raise FileNotFoundError(
                    f"No {GS_SIBLING_SUFFIX} sibling for run {curr_runID}; the "
                    "viewer cannot read .sog, so the run is not published."
                )
            sibling_dest = dest.with_suffix(GS_SIBLING_SUFFIX)
            self._atomic_copy(sibling_src, sibling_dest)  # raises: required
            self.logger.info(f"Published sibling {sibling_dest.name}.")
            # The `.gz` beside it is only an optimisation: best-effort.
            try:
                self._precompress(sibling_dest)
            except OSError as exc:  # noqa: PERF203
                self.logger.warning(f"Could not precompress the sibling: {exc}")

        self._atomic_copy(src, dest)
        self.logger.info(
            f"Copied output for {kind} run {curr_runID} for site {self.siteStr}."
        )

        if kind == "GS" and GS_EMIT_SIBLING:
            # The compressed PLY, published the same way and for the same
            # reason: written by `run_postprocess` into the run directory,
            # and without this it would go to the archive with the rest of
            # it and never reach the tree `api._sibling` looks in.
            cply_src = self.opt_path / "scene.compressed.ply"
            if cply_src.is_file():
                cply_dest = dest.with_name(dest.stem + ".compressed.ply")
                try:
                    self._atomic_copy(cply_src, cply_dest)
                    self.logger.info(f"Published {cply_dest.name}.")
                except OSError as exc:  # noqa: PERF203
                    self.logger.warning(f"Could not publish the compressed PLY: {exc}")
            else:
                # Not a failure: the encode is best-effort and the `.splat`
                # above is what the viewer falls back to.
                self.logger.info(
                    f"No compressed PLY for run {curr_runID}; the viewer "
                    "will be offered the larger .splat."
                )
        return dest

    def run_ark(self, ark_len: int = 16) -> None:
        """
        Runs the ark generator to generate a unique ark for the run.

        Parameters
        ----------
        ark_len : int
            Length of the ark to be generated
            Default: 16

        """
        kind = self.kind
        arkURL, run, site, siteStr = self.arkURL, self.run, self.site, self.siteStr
        naan, shoulder = ARK_NAAN, ARK_SHOULDER
        run.ended_at = timezone.now()

        # Create metadata
        self.logger.info(
            f"Creating metadata for ARK for {kind} run {run.ID} for site {siteStr}..."
        )
        def_commit = (
            "This ARK was generated & is managed by Project Tirtha (https://smlab.niser.ac.in/project/tirtha/). "
            + "We are committed to maintaining this ARK as per our Terms of Use (https://smlab.niser.ac.in/project/tirtha/#terms) "
            + "and Privacy Policy (https://smlab.niser.ac.in/project/tirtha/#privacy)."
        )

        metadata = {
            "monument": {
                "name": str(site.name),
                "location": f"{site.district}, {site.state}, {site.country}",  # LATE_EXP: f"{site.lat}, {site.lon}"
                "verbose_id": str(site.verbose_id),
                # Guarded, because `.url` raises on an empty ImageField.
                #
                # `Site.thumbnail` is `blank=False`, so the admin form always
                # supplies one and this read looked total. A site created in
                # code can skip that validation, and two did -- and the whole
                # reconstruction then died *here*, in the last stage, after
                # COLMAP, 2h8m of FRGS, the cut and the publish had all
                # succeeded and `scene.sog` was already on disk. The
                # contributor was mailed that their photographs could not be
                # built into a model, which was untrue: the model existed.
                #
                # A missing preview is not a reason to throw away a finished
                # reconstruction, so it degrades to an empty string. The ARK's
                # other metadata is unaffected.
                "thumbnail": (
                    f"{BASE_URL}{site.thumbnail.url}" if site.thumbnail else ""
                ),
                "description": str(site.description),
                "completed": True if site.completed else False,
            },
            "run": {
                "ID": str(run.ID),
                "ended_at": str(run.ended_at),
                "contributors": list(run.contributors.values_list("name", flat=True)),
                "images": int(run.images.count()),
            },
            "notice": def_commit,
        }
        metadata_json = json.dumps(metadata)
        self.logger.info(
            f"Created metadata for ARK for {kind} run {run.ID} for site {siteStr}..."
        )

        # Generate ark - NOTE: Adapted from arklet
        self.logger.info(
            f"Generating ARK for {kind} run {run.ID} for site {siteStr}..."
        )
        ark, collisions = None, 0
        while True:
            noid = generate_noid(ark_len)
            base_ark_string = f"{naan}{shoulder}{noid}"
            check_digit = noid_check_digit(base_ark_string)
            ark_string = f"{base_ark_string}{check_digit}"
            try:
                ark = ARK.objects.create(
                    ark=ark_string,
                    naan=naan,
                    shoulder=shoulder,
                    assigned_name=f"{noid}{check_digit}",
                    url=f"{BASE_URL}/static/{arkURL}",
                    metadata=metadata_json,
                )
                self.logger.info(f"ARK URL: {BASE_URL}/static/{arkURL}")
                break
            except IntegrityError:
                collisions += 1
                continue
        msg = f"Generated ARK for {kind} run {run.ID} for site {siteStr} after {collisions} collision(s)."
        if collisions == 0:
            self.logger.info(msg)
        else:
            self.logger.warning(msg)

        # Update run
        run.ark = ark
        run.save()
        self.arkStr = str(ark_string)

    def run_finalize(self) -> None:
        """
        Finalizes current run

        """
        kind = self.kind
        self.logger.info(f"Finalizing {kind} run {self.runID} for site {self.siteStr}.")
        self.logger.info(f"{kind} Run {self.runID} finished for site {self.siteStr}.")
        self._update_site_status(
            "Live", reconstructed_at=datetime.now(pytz.timezone("Asia/Kolkata"))
        )
        self.logger.info(
            f"Finished finalizing {kind} run {self.runID} for site {self.siteStr}."
        )
