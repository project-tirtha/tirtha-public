"""`GSOps`: the Gaussian Splatting path (kind "GS") -- COLMAP, training, post-process."""

import os
import shutil
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from tirtha_recon import Logger

# Local imports
from tirtha.models import Image

from .base import BaseOps
from .common import (
    ARCHIVE_ROOT,
    COLMAP421_PATH,
    COLMAP_COMPLETION,
    COLMAP_COMPLETION_TIMEOUT_S,
    COLMAP_PATH,
    COLMAP_STAGE_TIMEOUT_S,
    COLMAP_UP_AXIS,
    FASTGS_DATA_DEVICE,
    FASTGS_HOLDOUT,
    FASTGS_PYTHON,
    FASTGS_REPO,
    FASTGS_TIMEOUT_S,
    FRGS_DEVICE,
    FRGS_PATH,
    GS_BACKEND,
    GS_DECIMATE_ADAPTIVE,
    GS_DECIMATE_TARGET,
    GS_EMIT_LOD,
    GS_EMIT_SIBLING,
    GS_MAX_EPOCHS,
    GS_MAX_GAUSSIANS,
    GS_MAX_ITER,
    GS_SIBLING_SUFFIX,
    GS_VIEWER_ALIGN_UP,
    LOG_DIR,
    MEDIA,
    MIN_MATCH_RATIO,
    MIN_MATCHED_IMAGES,
    SPLAT_TRANSFORM_GPU,
    SPLAT_TRANSFORM_PATH,
    SPLATFACTO_ENV,
    SPLATFACTO_METHOD,
    STATIC,
)


class GSOps(BaseOps):
    """
    Runs the Gaussian splatting pipeline

    """

    def __init__(self, siteID: str, contrib_id: str) -> None:
        super().__init__(siteID=siteID, kind="GS", contrib_id=contrib_id)

        # nerfstudio is gone (internal design notes, issue #53): unmaintained since
        # Nov 2024, with hardcoded COLMAP paths and CLI args that block newer
        # COLMAP versions. FRGS is a subprocess, so there is no import to check
        # -- `run_colmap` fails loudly if the binaries are missing.
        self._run_order = [
            "run_colmap",
            "run_frgs",
            # Between training and publication, so what gets published is what
            # the masks left behind -- and after FRGS, because a cut needs
            # Gaussians to cut.
            "run_gaussian_cut",
            "run_postprocess",
        ] + self._run_order_suffix

    def run_colmap(self) -> None:
        """
        Structure-from-motion against the site's durable workspace
        (internal design notes).

        This used to be `ns-process-data`, which ran COLMAP *inside* nerfstudio
        with hardcoded paths and CLI arguments that break against newer COLMAP
        -- one of the concrete failures behind issue #53.

        **The workspace belongs to the site, not to this run.** `database.db`
        and `sparse/` persist between runs, which is what makes continual
        reconstruction possible (5.7): adding 3 images to a 300-image site can
        extend the existing model instead of rebuilding it. It also means the
        image paths COLMAP recorded stay valid, since `seq`-named symlinks are
        stable per image.
        """
        from tirtha_recon.colmap import (
            build_continuation_plan,
            build_plan,
            build_post_stages,
            choose_model,
            commit_post,
            known_images,
            mark_done,
            prepare_fresh_sparse,
            prepare_post,
        )

        from tirtha.workspace import materialise_site, withdrawn_links

        log_path = self.log_path / "colmap.log"

        # Refresh the symlink farm: adds links for new images, never removes
        # any, because the database references every image it has registered.
        workspace, materialised = materialise_site(self.site)
        self.workspace = workspace
        self.logger.info(
            f"Workspace {workspace.root}: {materialised.linked} new link(s), "
            f"{materialised.already_present} already present."
        )
        if materialised.missing_sources:
            self.logger.warning(
                f"{len(materialised.missing_sources)} source file(s) missing; "
                "reconstruction continues without them."
            )

        image_count = len(list(workspace.images.iterdir()))
        if image_count == 0:
            self._handle_error(
                RuntimeError(f"No images materialised for {self.siteStr}."),
                "run_colmap",
            )

        # Continuation vs a fresh build (internal design notes). COLMAP is
        # incremental where FRGS is not: adding 3 images to a 300-image site
        # should extend the existing model, not rebuild it from nothing.
        #
        # The model to build on is the largest *complete* one, not the
        # highest-numbered: after a fresh run on a site COLMAP split, the
        # highest number is the smallest component. An accepted completion model
        # is stored as the next number and is then the largest, so continuations
        # extend the whole site -- unless COLMAP_COMPLETION is off, in which case
        # stored completion models are skipped (review 2026-11-22 §1 #4).
        already_known, on_disk, previous_model, new_images = self._survey_workspace(
            workspace
        )

        # A withdrawal invalidates the continuation.
        #
        # The symlink farm is append-only, and deliberately so: COLMAP's
        # database references every image it has registered by path, so pruning
        # would break a continuation rather than tidy it. The cost is that
        # **moderation cannot withdraw an image from a site reconstructed
        # once** -- a moderator rejects a photograph, the count drops
        # everywhere it is displayed, and the next run reads the link still
        # sitting in the workspace. Fontaine Bartholdi had 73 such links
        # against 0 reconstructable images when this was written.
        #
        # So when the allowed set has shrunk since the workspace was built,
        # this is not a continuation of the same site any more. The workspace
        # is moved aside, whole and dated, and the run starts clean. Moved
        # rather than deleted: it holds the only copy of a COLMAP database that
        # took hours to build, and this is a decision made by a rule rather
        # than by a person.
        withdrawn = withdrawn_links(self.site, workspace)
        if withdrawn:
            retired = workspace.root.with_name(
                f"{workspace.root.name}.withdrawn-{timezone.now():%Y%m%d-%H%M%S}"
            )
            self.logger.warning(
                f"{len(withdrawn)} image(s) withdrawn from {self.siteStr} since this "
                f"workspace was built; moving it to {retired.name} and "
                "reconstructing from scratch."
            )
            shutil.move(str(workspace.root), str(retired))
            workspace, _ = materialise_site(self.site)
            self.workspace = workspace
            already_known, on_disk, previous_model, new_images = self._survey_workspace(
                workspace
            )

        if previous_model is not None and already_known and new_images:
            self.logger.info(
                f"Continuing from {previous_model.name}: {len(new_images)} new "
                f"image(s) against {len(on_disk) - len(new_images)} already attempted."
            )
            # COLMAP reads this list from a file rather than argv -- a few
            # hundred names would not fit on a command line.
            image_list = self.colmapListPath = workspace.root / "new_images.txt"
            image_list.write_text("\n".join(new_images) + "\n")
            plan = build_continuation_plan(
                database=workspace.database,
                image_dir=workspace.images,
                sparse_dir=workspace.sparse,
                new_images=new_images,
                image_list_path=image_list,
                colmap=str(COLMAP_PATH),
                vocab_tree_path=getattr(settings, "COLMAP_VOCAB_TREE_PATH", None),
                previous=previous_model,
            )
            self.continuation = True
        elif previous_model is not None and not new_images:
            # Nothing new. Re-running SfM would burn hours to reproduce the
            # model that already exists.
            #
            # "New" is measured against the images the model's own run attempted
            # (`attempted_images`), not against database.db: a continuation that
            # crashed after feature extraction left its images in the database,
            # and this branch then republished the old model without them
            # (review 2026-11-22 §1 #3).
            self.logger.info(
                f"No new images since {previous_model.name}; reusing the "
                "existing sparse model."
            )
            self.continuation = True
            self.record_stage_metrics(
                skipped="no new images", reused_model=previous_model.name
            )
            self._check_output(previous_model / "cameras.bin", "run_colmap")
            # The model this branch decided to reuse is the model the next
            # stage trains on. `self.colmapModel` is otherwise assigned only
            # after this early return, so `run_frgs` raised
            # `AttributeError: 'GSOps' object has no attribute 'colmapModel'`
            # every time -- and this is precisely the path taken when COLMAP
            # succeeded and FRGS died, which is when skipping SfM is the whole
            # point.
            self.colmapModel = previous_model
            # The registration gate runs here too: a model it rejected before
            # must not be published by a rerun (review 2026-11-22 §3).
            self._analyze_and_gate(previous_model, log_path)
            return
        else:
            if previous_model is not None:
                # New images, but the database does not know the old ones (it is
                # missing or unreadable), so there is nothing to continue from.
                self.logger.warning(
                    f"Model {previous_model.name} exists but database.db lists no "
                    "images; rebuilding from scratch."
                )
            # A full rebuild runs into an empty `sparse/`: the old one is moved
            # aside, never deleted, so a stale model cannot win `best_model`
            # (review 2026-11-22 §3).
            moved = prepare_fresh_sparse(workspace.sparse)
            if moved is not None:
                self.logger.warning(f"Moved the previous sparse/ aside to {moved}.")
                self.record_stage_metrics(previous_sparse=moved.name)
            plan = build_plan(
                database=workspace.database,
                image_dir=workspace.images,
                sparse_dir=workspace.sparse,
                image_count=len(on_disk),
                colmap=str(COLMAP_PATH),
                vocab_tree_path=getattr(settings, "COLMAP_VOCAB_TREE_PATH", None),
                mask_dir=self._colmap_mask_dir(workspace),
                single_camera=self._one_camera_for_the_site(),
                up_axis=COLMAP_UP_AXIS,
            )
            self.continuation = False

        self.logger.info(
            f"COLMAP: {len(on_disk)} image(s) total, "
            f"{'continuation' if self.continuation else plan.matcher}."
        )

        for name, argv in plan.stages:
            self.logger.info(f"COLMAP stage: {name}")
            self._serialRunner(argv, log_path, timeout=COLMAP_STAGE_TIMEOUT_S)

        if self.continuation:
            # The plan's own output directory, not "the newest model": if the
            # mapper wrote nothing, training on whatever else is newest would
            # publish a fragment (review 2026-11-22 §3).
            current = plan.output_model
            if current is None or not (current / "cameras.bin").exists():
                self._handle_error(
                    RuntimeError(
                        f"The continuation mapper wrote no model into {current}."
                    ),
                    "run_colmap",
                )
            mark_done(
                current,
                attempted=known_images(workspace.database),
                source="continuation",
                extended=previous_model.name,
            )
        else:
            # Choose the model first, then post-process *that* model. A fresh
            # mapper run with `multiple_models=1` writes one directory per
            # component; filtering and the up axis ran on a hard-wired sparse/0
            # while training used the largest (review 2026-11-22 §3).
            sizes = choose_model(workspace.sparse)
            if not sizes:
                self._handle_error(
                    RuntimeError("COLMAP produced no sparse model."), "run_colmap"
                )
            current = sizes[0][0]
            if len(sizes) > 1:
                self.logger.warning(
                    "COLMAP split the site into components of "
                    f"{[n for _, n in sizes]} images; only {current.name} is used "
                    "unless completion succeeds."
                )
                self.record_stage_metrics(
                    components=len(sizes), dropped_images=sum(n for _, n in sizes[1:])
                )
            # Written to `sparse/N.post` and swapped in only when both steps
            # succeeded; the model counts (DONE) only after that.
            work, post_stages = build_post_stages(
                current,
                image_dir=workspace.images,
                colmap=str(COLMAP_PATH),
                up_axis=COLMAP_UP_AXIS,
            )
            prepare_post(work)
            for name, argv in post_stages:
                self.logger.info(f"COLMAP stage: {name} (model {current.name})")
                self._serialRunner(argv, log_path, timeout=COLMAP_STAGE_TIMEOUT_S)
            commit_post(current, work, attempted=known_images(workspace.database))
            if (
                COLMAP_COMPLETION
                and COLMAP421_PATH
                and sum(1 for _, n in sizes if n >= 10) > 1
            ):
                current = self._complete_split_site(workspace, current, log_path)

        self.record_stage_metrics(
            continuation=self.continuation,
            new_images=len(new_images),
            total_images=len(on_disk),
            model=current.name,
        )
        self.record_stage_artifacts(workspace.database, workspace.sparse)
        # The gate judges the model that will be trained.
        self._analyze_and_gate(current, log_path)
        self.colmapModel = current
        self.logger.info(f"Finished COLMAP; current model is {current.name}.")

    def _survey_workspace(self, workspace):
        """
        `(known in database.db, image names on disk, model to build on, new images)`.

        First finishes or undoes whatever a crash left half-done under `sparse/`
        (`recover_interrupted`). New images are those the model's own run never
        attempted (`attempted_images`), not "absent from database.db" (review
        2026-11-22 §1 #3).
        """
        from tirtha_recon.colmap import (
            attempted_images,
            best_model,
            known_images,
            recover_interrupted,
        )

        for note in recover_interrupted(workspace.sparse):
            self.logger.warning(f"{workspace.sparse}: recovered after a crash: {note}.")
        known = known_images(workspace.database)
        on_disk = sorted(p.name for p in workspace.images.iterdir() if p.is_file())
        previous = best_model(workspace.sparse, skip_completion=not COLMAP_COMPLETION)
        covered = (
            attempted_images(previous, workspace.database)
            if previous is not None
            else set()
        )
        return known, on_disk, previous, [n for n in on_disk if n not in covered]

    def _analyze_and_gate(self, model: Path, log_path: Path) -> dict:
        """`model_analyzer` on `model`, its numbers into the stage row, then the registration gate."""
        from tirtha_recon.colmap import analyzer_command, parse_model_analyzer

        self.logger.info(f"COLMAP stage: model_analyzer (model {model.name})")
        text = self._serialRunner(
            analyzer_command(model, str(COLMAP_PATH)),
            log_path,
            timeout=COLMAP_STAGE_TIMEOUT_S,
            capture=True,
        )
        stats = parse_model_analyzer(text or "")
        self.colmapMetrics = stats
        self.record_stage_metrics(**stats)
        self.logger.info(f"COLMAP statistics ({model.name}): {stats}")
        self._validate_colmap_registration(stats, log_path)
        return stats

    def _complete_split_site(self, workspace, current: Path, log_path: Path) -> Path:
        """
        Try to recover the components 3.11 dropped with one COLMAP 4.2.1 run
        (tirtha_recon.completion). The 4.2.1 model is used only if the guard
        accepts it; otherwise the run continues exactly as before. Never fails
        the run: completion is an improvement, not a requirement -- including
        when it runs past COLMAP_COMPLETION_TIMEOUT_S (its process group is
        killed and the run carries on with `current`).
        """
        from tirtha_recon.colmap import known_images
        from tirtha_recon.completion import guard, run_completion

        work = self.runDir / "completion"
        try:
            res = run_completion(
                colmap=str(COLMAP421_PATH),
                database=workspace.database,
                image_dir=workspace.images,
                work_dir=work,
                log_path=log_path,
                timeout_s=COLMAP_COMPLETION_TIMEOUT_S,
            )
            if not res.get("ok"):
                self.logger.warning(f"Completion produced no model: {res}")
                self.record_stage_metrics(completion="no model")
                return current
            verdict = guard(workspace.sparse, Path(res["model"]))
            self.logger.info(f"Completion: {res}; guard: {verdict}")
            self.record_stage_metrics(
                completion=verdict["verdict"],
                completion_images=res["images"],
                completion_mapper_s=res.get("mapper_s", 0),
            )
            if verdict["verdict"] != "PASS":
                return current
            model = Path(res["model"])
            # Up axis with align_up whatever COLMAP_UP_AXIS says: the 3.11 aligner
            # has not been tested on a model written by 4.2.1, and align_up reads both.
            from tirtha_recon.align_up import align
            from tirtha_recon.completion import store_model

            align(model, model, workspace.images, "auto")
            # Keep it in the site's workspace so later runs (continuation, or a
            # rerun with no new images) build on the whole site, not on 3.11's part.
            # Marked COMPLETION, so turning COLMAP_COMPLETION off stops it being used.
            stored = store_model(
                model,
                workspace.sparse,
                attempted=known_images(workspace.database),
                run=str(self.runID),
            )
            self.logger.info(f"Completion model stored as {stored.name}.")
            self.record_stage_metrics(completion_model=stored.name)
            return stored
        except Exception as exc:  # noqa: BLE001 -- see docstring
            self.logger.warning(f"Completion failed, using {current.name}: {exc!r}")
            self.record_stage_metrics(completion="error")
            return current

    def run_frgs(self) -> None:
        """
        Gaussian splatting with FRGS (internal design notes).

        Replaces `ns-train splatfacto-big` **and** `ns-export`: FRGS writes the
        PLY directly, so the separate export step is gone.

        Run as a subprocess against its own environment. The reconstruction
        stack pins Python 3.13 / torch 2.11 (14.1) while the web tier does not,
        so importing it here would drag that ceiling across the tier boundary
        (15).
        """
        from tirtha_recon.frgs import FrgsBackend, read_metrics

        log_path = self.log_path / "frgs.log"
        out_dir = self.runDir / "output"
        out_dir.mkdir(parents=True, exist_ok=True)

        # The stage keeps its name.
        #
        # `run_frgs` is this stage's identity in `RunStage.name`, in
        # `email_utils`'s stage->explanation map and in the admin's re-trigger
        # path. Renaming it to match whichever backend is selected would orphan
        # every historical row and rename a stage that has not changed what it
        # does -- train Gaussians from a COLMAP model. What varies is the
        # backend, and that is named in the metrics below.
        if GS_BACKEND == "splatfacto":
            from tirtha_recon.splatfacto import SplatfactoBackend

            backend = SplatfactoBackend(
                env_root=str(SPLATFACTO_ENV), method=str(SPLATFACTO_METHOD)
            )
        elif GS_BACKEND == "fastgs":
            from tirtha_recon.fastgs import FastGSBackend

            backend = FastGSBackend(
                python=str(FASTGS_PYTHON),
                repo=str(FASTGS_REPO),
                colmap=str(COLMAP_PATH),
            )
        else:
            backend = FrgsBackend(executable=str(FRGS_PATH))

        # FRGS resolves the model as `<dir>/sparse/0`, hardcoded
        # (fvdb_reality_capture/sfm_scene/adapter.py). Continuation writes
        # `sparse/1`, `sparse/2`, ... so handing it the workspace directly
        # would train on the *stale* model, silently and with no error.
        #
        # A per-run dataset view fixes that without touching the durable
        # workspace: two symlinks, pointing at the images and at whichever
        # model the mapper just produced. It also gives FRGS a path that is
        # stable for the life of the run, which its resume support requires
        # (internal design notes).
        dataset_dir = self.runDir / "dataset"
        (dataset_dir / "sparse").mkdir(parents=True, exist_ok=True)
        for link, target in (
            (dataset_dir / "images", self.workspace.images),
            (dataset_dir / "sparse" / "0", self.colmapModel),
        ):
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(target, target_is_directory=True)
        self.logger.info(f"FRGS dataset view: sparse/0 -> {self.colmapModel.name}")

        if GS_BACKEND == "fastgs":
            config = {
                "max_iterations": GS_MAX_ITER,
                "downscale_factor": 2,  # production Splatfacto's factor
                "data_device": FASTGS_DATA_DEVICE,
                "holdout": FASTGS_HOLDOUT,
                "out_ply_name": "reconstruction.ply",
                # Kills the process group and raises on expiry (review §1 #5).
                "timeout_s": FASTGS_TIMEOUT_S,
                # One retry with data_device=cpu after a CUDA OOM (review §3).
                "oom_retry": True,
            }
        elif GS_BACKEND == "splatfacto":
            config = {
                "max_iterations": GS_MAX_ITER,
                # Both halves of the output path are fixed by the backend, and
                # the run directory's name is what makes them unique per run.
                "experiment_name": self.runDir.name,
                # The name `run_postprocess` and `run_gaussian_cut` already
                # expect (see `self.splatSource` below). Publishing must not
                # care which backend produced the file.
                "out_ply_name": "reconstruction.ply",
            }
        else:
            config = {
                "production_defaults": True,
                "max_epochs": GS_MAX_EPOCHS,
                "max_gaussians": GS_MAX_GAUSSIANS,
                "device": FRGS_DEVICE,
                "extra_args": ["--io.log-path", str(out_dir / "logs")],
            }

        self.logger.info(f"Running {GS_BACKEND}; check log file: {log_path}.")
        artifact = backend.reconstruct(
            dataset_dir, config, out_dir=out_dir, log_path=log_path
        )

        # FRGS writes nothing at all -- no metrics, no checkpoints -- unless
        # --io.log-path is set, which is why it is passed above (14.1.1).
        # splatfacto's are already on the Artifact, scraped from its tensorboard
        # event file by `SplatfactoBackend.reconstruct`.
        extra = (
            {}
            if GS_BACKEND in ("splatfacto", "fastgs")
            else read_metrics(out_dir / "logs")
        )
        # Which pathway made this, recorded beside what it measured. Two runs of
        # the same site are otherwise indistinguishable in the stage row.
        self.frgsMetrics = {"backend": GS_BACKEND, **artifact.metrics, **extra}
        self.gsBackend = GS_BACKEND
        self.record_stage_metrics(
            **{
                k: v for k, v in self.frgsMetrics.items() if isinstance(v, (int, float))
            },
            # The one string kept: an admin re-cut reads it to choose the frame
            # conversion this run's output needs (`_recorded_backend`).
            backend=GS_BACKEND,
        )
        self.record_stage_artifacts(artifact.path)
        self.logger.info(f"FRGS metrics: {self.frgsMetrics}")

        if artifact.metrics.get("exit_code") not in (0, None):
            self._handle_error(
                RuntimeError(f"{GS_BACKEND} failed; see {log_path}"), "run_frgs"
            )
        self._check_output(artifact.path, "run_frgs")
        self.logger.info(f"Finished {GS_BACKEND}.")

    @staticmethod
    def _dimensions(img) -> tuple:
        """
        An image's stored pixel size, or `(None, None)` if it cannot be read.

        Read from the file, because `Image` stores no width/height columns.
        Only the header is parsed, and this runs once per run against a
        pipeline measured in hours, so the cost is noise. An unreadable file
        answers `(None, None)`, which differs from every real size and so
        settles the question as "not one camera" -- the safe direction.
        """
        try:
            return (img.image.width, img.image.height)
        except Exception:
            return (None, None)

    def _one_camera_for_the_site(self) -> bool:
        """
        Whether every reconstructable image says it came from the same camera.

        COLMAP is told one way or the other, and both mistakes are real. Left to
        itself with no EXIF it gives each photograph its own intrinsics -- 73
        cameras for 73 views of one fountain from one iPhone, each focal length
        estimated from a single image. Forced to one camera on a crowdsourced
        set it solves a single focal length for a mixture of phones and bends
        the geometry to fit.

        So this answers from what the images actually record. It is true only
        when every one of them names the same make, model and focal length, and
        at least one of them names anything at all: a site whose EXIF is empty
        -- which is the whole archive, until the ingest change that reads it
        started applying -- gets the safe answer, which is COLMAP's own.

        A single contributor photographing one monument on one phone is the
        common case here, and it is the case this exists for.
        """
        from tirtha.workspace import reconstructable_images

        # Dimensions are part of the identity, not just the EXIF triple.
        #
        # `uploads.py` applies `exif_transpose` at ingest, so one phone held
        # portrait and landscape stores 3024x4032 and 4032x3024 -- same make,
        # model and focal length, different intrinsics. COLMAP answers
        # `--ImageReader.single_camera 1` on a mixed set with
        # CAMERA_SINGLE_DIM_ERROR and *skips* the minority orientation, so the
        # side of the monument shot the other way up silently leaves the
        # reconstruction. Resolution changes on one body (12MP/48MP) do the
        # same, as does anything that went through the old browser downscale.
        cameras = {
            (
                (img.camera_make or "").strip(),
                (img.camera_model or "").strip(),
                (img.focal_length or "").strip(),
                *self._dimensions(img),
            )
            for img in reconstructable_images(self.site)
        }
        if len(cameras) != 1:
            return False
        make, model, focal, _w, _h = next(iter(cameras))
        # All three empty is "we do not know", not "one camera".
        return bool(make or model or focal)

    def _colmap_mask_dir(self, workspace):
        """
        Whether COLMAP should mask during feature extraction.

        **Open question, deliberately opt-in** (internal design notes). FRGS consumes
        masks in its training loop by default, so a mask reaches GS training
        without COLMAP's help. Whether masking *also* helps SfM robustness --
        or whether it discards features that would have aided registration --
        is unmeasured, and the Lab (§21) is where that gets settled rather than
        guessed at here.
        """
        if not getattr(settings, "COLMAP_USE_MASKS", False):
            return None
        if not workspace.masks.exists() or not any(workspace.masks.iterdir()):
            return None
        return workspace.masks

    def _validate_colmap_registration(self, stats: dict, log_path: Path) -> None:
        """
        Cancel a run COLMAP could not register enough of.

        Replaces `_validate_colmap_matches`, which regex-scraped prose out of a
        nerfstudio log ("Starting with N images", "Colmap matched N images").
        `model_analyzer` reports the same facts as numbers, so this reads them
        instead of parsing sentences that a nerfstudio release could reword.
        """
        total = stats.get("images")
        registered = stats.get("registered_images")
        if not total:
            self.logger.warning(
                "COLMAP registration statistics unavailable; skipping validation."
            )
            return

        ratio = (registered or 0) / total
        low_ratio = ratio < MIN_MATCH_RATIO
        low_count = (registered or 0) < MIN_MATCHED_IMAGES
        if not (low_ratio or low_count):
            return

        notes = []
        if low_ratio:
            notes.append(f"registration rate below {MIN_MATCH_RATIO:.0%}")
        if low_count:
            notes.append(f"fewer than {MIN_MATCHED_IMAGES} registered images")
        self._cancel_run(
            reason=(
                f"COLMAP registered only {registered or 0} of {int(total)} images "
                f"({ratio:.2%}) ({' and '.join(notes)}). Cancelling run."
            ),
            caller="run_colmap",
            log_excerpt=str(stats),
            log_file_path=log_path,
        )

    @classmethod
    def for_existing_run(cls, run) -> "GSOps":
        """
        Bind to a `Run` that already exists, instead of starting another.

        `__init__` creates a Run and its directory -- that is its job, and it is
        exactly what a re-cut must not do. An operator asking to cut a published
        reconstruction is asking about *that* reconstruction: a second Run would
        retrain from scratch for hours and produce a different model.

        So this builds the instance without `__init__` and fills in only what
        the two stages it is used for actually read. `__new__` rather than a
        flag through `__init__`, because the side effects there are the point of
        that method and threading a "do not do your job" argument through it
        would be worse than not calling it.

        **`_handle_error` is replaced.** The real one marks the Run *and the
        Site* as Error, stamps `ended_at`, rewrites `notes` and emails the
        admins. That is right for a reconstruction that failed and catastrophic
        here: the run being cut is already published and good, and a bad mask
        must not be able to retire it. The replacement logs and re-raises, which
        is the control flow `run_postprocess` expects, and leaves the database
        alone.
        """
        self = cls.__new__(cls)
        self.site = site = run.site
        self.siteID = site.ID
        self.siteVID = site.verbose_id
        self.siteStr = f"{self.siteVID} <=> {self.siteID}"
        self.kind = run.kind
        self.run = run
        self.runID = run.ID
        self.cancelled = False
        self.cancel_reason = None
        self.contrib_id = None
        self.contribution = None
        self._stage_metrics = {}
        self._stage_artifacts = []

        run_dir_path = Path(run.directory)
        # An archived run's directory is stored relative to ARCHIVE_ROOT
        # (`run_cleanup`), an active one's relative to STATIC/models -- the same
        # rule `admin._run_dir` uses. Resolving every relative path against
        # STATIC sent the re-cut of a published (archived) run to a directory
        # that does not exist.
        if run_dir_path.is_absolute():
            self.runDir = run_dir_path
        elif run.status == "Archived":
            self.runDir = ARCHIVE_ROOT / run_dir_path
        else:
            self.runDir = STATIC / "models" / run_dir_path
        self.imageDir = MEDIA / f"models/{self.siteID}/images"
        self.log_path = LOG_DIR / f"{run.kind}Ops/{self.siteID}" / self.runDir.stem
        self.log_path.mkdir(parents=True, exist_ok=True)
        cls.logger = Logger(
            log_path=self.log_path, name=f"{cls.__name__}_recut_{str(run.ID)[:8]}"
        )

        # The model this run actually trained on, not whichever is newest.
        # `run_frgs` leaves `dataset/sparse/0` pointing at it precisely because
        # continuation writes `sparse/1`, `sparse/2`, ... into the site's
        # durable workspace -- so reading the workspace now could hand the cut a
        # model whose poses never produced these Gaussians, and every projection
        # would be wrong.
        self.colmapModel = self.runDir / "dataset" / "sparse" / "0"
        self.splatSource = self.runDir / "output" / "reconstruction.ply"
        # The backend that trained *this* run decides the frame conversion, not
        # today's GS_BACKEND: a Splatfacto run re-cut after the switch to FastGS
        # would otherwise be rotated into a frame it was never in (review §3).
        self.gsBackend = cls._recorded_backend(run, self.runDir)
        # `run_postprocess` republishes into STATIC/models/... when set.
        self._recut = True

        def _reraise(excep: Exception, caller: str) -> None:
            self.logger.error(f"{caller} failed during a re-cut of run {run.ID}.")
            self.logger.error(f"{excep}", exc_info=True)
            raise excep

        self._handle_error = _reraise
        return self

    @staticmethod
    def _recorded_backend(run, run_dir: Path) -> str:
        """
        Which Gaussian backend trained `run`, from its `run_frgs` stage metrics.

        Runs before this was recorded have no `backend` metric. FastGS ones are
        recognised by what only FastGS leaves (its `prepare_s` metric, or the
        `output/fastgs_model` directory); anything else predates FastGS and so
        needed no frame conversion, whatever GS_BACKEND says now.
        """
        try:
            from tirtha.models import RunStage

            for metrics in (
                RunStage.objects.filter(run=run, name="run_frgs")
                .order_by("-attempt", "-ordinal")
                .values_list("metrics", flat=True)
            ):
                metrics = metrics or {}
                if metrics.get("backend"):
                    return str(metrics["backend"])
                if "prepare_s" in metrics:
                    return "fastgs"
        except Exception:  # noqa: BLE001 -- fall through to the disk evidence
            pass
        if (Path(run_dir) / "output" / "fastgs_model").is_dir():
            return "fastgs"
        return "legacy"

    def run_gaussian_cut(self, force: bool = False) -> None:
        """
        Remove the Gaussians the contributed masks disown (internal design notes).

        `force` is the admin trigger: an operator who has looked at the masks
        and pressed the button has overruled the deployment-wide default, and
        `GAUSSIAN_CUT_ENABLE` should not veto a decision made about one run.
        The minimum-mask floor still applies -- that one is about evidence, not
        about policy.

        FRGS already consumes masks while training, and COLMAP can mask during
        feature extraction. Neither removes anything: background Gaussians are
        still created and still published. This is the removal.

        **Additive.** The cut writes `reconstruction_cut.ply` and leaves
        `reconstruction.ply` exactly as FRGS wrote it. Publication then follows
        `self.splatSource`. A cut is irreversible and mask quality varies, so
        the artifact that cost GPU-hours stays on disk to be compared against or
        fallen back to.

        **A no-op is the normal case, and is not a failure.** Most runs have no
        masks at all; the stage records why it did nothing and returns. It is
        also off by default (`GAUSSIAN_CUT_ENABLE`), following the precedent
        `COLMAP_USE_MASKS` set in 10.2: a new stage on the reconstruction path
        earns its default by being measured in the Lab, not by being written.
        """
        source = self.runDir / "output" / "reconstruction.ply"
        self.splatSource = source

        if not force and not getattr(settings, "GAUSSIAN_CUT_ENABLE", False):
            self.logger.info("Gaussian cut disabled (GAUSSIAN_CUT_ENABLE); skipping.")
            self.record_stage_metrics(skipped=1)
            return

        from tirtha_recon.gaussian_cut import (
            CutRefused,
            CutSettings,
            cut,
            read_mask_png,
        )

        from tirtha.workspace import image_refs

        masks = {}
        for ref in image_refs(self.site):
            image = Image.objects.filter(ID=ref.image_id).first()
            if image is None or not image.mask:
                continue
            path = Path(settings.MEDIA_ROOT) / image.mask.name
            if path.exists():
                # Keyed on the *link* name, which is what COLMAP registered the
                # view under and therefore what the sparse model calls it.
                masks[ref.link_name] = read_mask_png(path)

        minimum = int(getattr(settings, "GAUSSIAN_CUT_MIN_MASKS", 2))
        if len(masks) < minimum:
            self.logger.info(
                f"{len(masks)} mask(s) for this site, need {minimum}; skipping the cut."
            )
            self.record_stage_metrics(masks=len(masks), skipped=1)
            return

        out = self.runDir / "output" / "reconstruction_cut.ply"
        settings_obj = CutSettings(
            keep_ratio=float(getattr(settings, "GAUSSIAN_CUT_KEEP_RATIO", 0.5)),
            min_votes=int(getattr(settings, "GAUSSIAN_CUT_MIN_VOTES", 2)),
            dilate_px=int(getattr(settings, "GAUSSIAN_CUT_DILATE_PX", 2)),
        )

        try:
            report = cut(
                source,
                out,
                model_dir=self.colmapModel,
                masks=masks,
                settings=settings_obj,
                max_removed_fraction=float(
                    getattr(settings, "GAUSSIAN_CUT_MAX_REMOVED", 0.9)
                ),
            )
        except CutRefused as refusal:
            # Not an error for the *run*: the reconstruction is intact and
            # publishable. The cut declining is a fact about the masks, and
            # failing the whole run over it would throw away GPU-hours because
            # somebody uploaded an inverted stencil.
            self.logger.warning(f"Gaussian cut refused: {refusal}")
            self.record_stage_metrics(refused=1, masks=len(masks))
            return

        self.logger.info(f"Gaussian cut: {report.as_dict()}")
        self.record_stage_metrics(
            masks=len(masks),
            views_used=report.views_used,
            judged=report.judged,
            removed=report.removed,
            kept=report.kept,
        )
        self.record_stage_artifacts(out)
        self._check_output(out, "run_gaussian_cut")
        self.splatSource = out

    def run_postprocess(self) -> None:
        """
        Decimate, then encode to SOG for publication (internal design notes).

        Replaces ~480 lines of hand-rolled density filtering and floater
        removal. That code did by hand what FRGS now does during optimisation
        and what splat-transform does with a maintained, GPU-accelerated
        implementation -- §10.5's instruction was to verify the coverage, then
        delete rather than port.

        **Two invocations, because splat-transform requires it**: decimation
        must be the final action of a command producing a `.ply`, so encoding
        cannot be chained onto it.

        Measured on a 708 MB reconstruction: 34 MB as SOG alone (20.8x), or
        9.3 MB decimated first (76x) -- the difference between a download a
        phone will tolerate and one it will not.
        """
        from tirtha_recon.splat_transform import SplatTransform

        log_path = self.log_path / "splat_transform.log"

        # Built beside `published/`, then swapped in by rename.
        #
        # This wrote straight into `published/`, which is the directory being
        # served. For a first run that is harmless -- nothing is there yet and
        # nobody is reading it. For a re-cut (`GSOps.for_existing_run`, the
        # admin's "cut and republish" action) the run is already live, and
        # encoding three artifacts of a few hundred megabytes takes minutes:
        # for all of that time a visitor downloading the model got a file being
        # rewritten underneath them.
        #
        # Worse on failure. `to_sog` succeeding and `to_splat` failing left a
        # cut `.sog` beside an uncut `.splat` -- two artifacts of the same run
        # disagreeing about which reconstruction they came from -- and because
        # `for_existing_run` re-raises rather than touching the database, the
        # Run stayed "Archived" with nothing recording that its published set
        # was now inconsistent.
        #
        # Staging is a sibling, so both renames are within one filesystem and
        # therefore atomic. The window in which `published/` does not exist is
        # the microseconds between two `os.replace` calls, instead of minutes.
        final_dir = self.runDir / "output" / "published"
        out_dir = self.runDir / "output" / ".published-staging"
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Whatever `run_gaussian_cut` left, which is the untouched
        # reconstruction when it did nothing. Never hardcoded back to
        # `reconstruction.ply`: that would publish the uncut splat while the
        # stage row said the cut succeeded.
        source = getattr(self, "splatSource", None) or (
            self.runDir / "output" / "reconstruction.ply"
        )
        decimated = out_dir / "decimated.ply"
        published = out_dir / "scene.sog"

        # FastGS writes in the COLMAP frame (so the Gaussian cut can use COLMAP
        # poses). The viewer expects the frame Splatfacto exports in: convert
        # after the cut, before decimation. Same transform nerfstudio would apply,
        # recomputed from the COLMAP model (tirtha_recon.gs_frame).
        #
        # The backend is the one that trained this run (`self.gsBackend`, set by
        # `run_frgs` or, for a re-cut, read from its stage metrics), not the
        # deployment's current GS_BACKEND.
        framed = None
        if getattr(self, "gsBackend", GS_BACKEND) == "fastgs":
            from tirtha_recon.fastgs import to_viewer_frame

            # Written beside the reconstruction, not into the staging directory
            # that is swapped in as `published/`: it is an intermediate, deleted
            # once everything is encoded.
            framed = self.runDir / "output" / "viewer_frame.ply"
            to_viewer_frame(
                Path(source),
                framed,
                self.colmapModel,
                align_up=bool(GS_VIEWER_ALIGN_UP),
                # the run's dataset view exists on first runs and re-cuts alike
                image_path=self.runDir / "dataset" / "images",
            )
            source = framed

        st = SplatTransform(
            executable=str(SPLAT_TRANSFORM_PATH), gpu=SPLAT_TRANSFORM_GPU
        )

        try:
            if GS_DECIMATE_TARGET:
                self.logger.info(f"Decimating to {GS_DECIMATE_TARGET}...")
                self._serialRunner(
                    st.decimate(
                        source,
                        decimated,
                        target=GS_DECIMATE_TARGET,
                        adaptive=GS_DECIMATE_ADAPTIVE,
                    ),
                    log_path,
                )
                self._check_output(decimated, "run_postprocess")
                source = decimated

            self.logger.info("Encoding to SOG...")
            self._serialRunner(
                st.to_sog(source, published),
                log_path,
            )

            # A `.compressed.ply` beside the rest.
            #
            # PlayCanvas's chunked, quantised PLY, and the shipped viewer reads
            # it with no change: `PlyParserUtils` sniffs `element chunk` in the
            # header and dispatches to `PlayCanvasCompressedPlyParser`.
            # Measured across six published models from 1 MB to 288 MB it is a
            # uniform 50% of the `.splat`, which `api._artifact_url` now prefers.
            #
            # Not instead of the `.splat`: 56 ARKs name one, superspl.at links
            # point at them, and an ARK keeps resolving to the file it named.
            # This is a smaller sibling to offer, not a replacement.
            if GS_EMIT_SIBLING:
                # A second, viewer-readable copy beside the `.sog`.
                #
                # 5.4 made SOG the published format and 6.4 targeted a viewer
                # that reads it. The React UI now shipping
                # (`@mkkellogg/gaussian-splats-3d`) cannot read `.sog` at all --
                # so without this, every newly reconstructed site would report
                # "no 3D model yet" in the interface people actually use, while
                # the file sat on disk.
                #
                # **`.splat`, not `.spz`.** 3.3.3's README marks `.splat`
                # input-only and it is not -- running the tool writes one. SPZ
                # looked like the safe alternative and is not: splat-transform
                # writes v4 and refuses below v3, the viewer rejects above v2,
                # and the two do not meet.
                #
                # Both are published; `api._artifact_url` offers whichever the
                # deployment's `VIEWER_ARTIFACT_FORMATS` names first.
                sibling = out_dir / f"scene{GS_SIBLING_SUFFIX}"
                self.logger.info(f"Encoding to {GS_SIBLING_SUFFIX} for the viewer...")
                self._serialRunner(
                    st.to_splat(source, sibling),
                    log_path,
                )

            if GS_EMIT_LOD:
                # Chunked, progressively loaded: the main win for mobile
                # viewers on limited bandwidth (internal design notes).
                lod_meta = out_dir / "lod" / "lod-meta.json"
                lod_meta.parent.mkdir(parents=True, exist_ok=True)
                self.logger.info("Generating streamed LoD...")
                self._serialRunner(
                    st.to_lod(source, lod_meta),
                    log_path,
                )
        except Exception as e:
            self._handle_error(e, "run_postprocess")

        # The compressed PLY, after everything the stage promised, and in its own
        # try.
        #
        # It was inside the block above, ahead of the `.sog` and the `.splat`.
        # That block's handler is `_handle_error`, which by its own comment
        # "must keep propagating" -- so a splat-transform refusal here destroyed
        # a reconstruction that had otherwise finished: no ARK, an archived run,
        # and a failure email to the contributor. That is not hypothetical. The
        # backfill over the 60 published splats had exactly one refusal, a
        # 12-byte file with no Gaussians in it.
        #
        # This is a 50% size saving on a file the viewer can already read
        # uncompressed. An optimisation must not be able to lose the thing it is
        # optimising. `_precompress` and the sibling copy in `run_cleanup` are
        # both best-effort for the same reason; this now matches them.
        if GS_EMIT_SIBLING:
            try:
                self.logger.info("Encoding to compressed PLY for the viewer...")
                self._serialRunner(
                    st.to_compressed_ply(source, out_dir / "scene.compressed.ply"),
                    log_path,
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(
                    f"Could not encode the compressed PLY: {exc}. The .sog and "
                    "the .splat are published; the viewer will be offered the "
                    "larger file."
                )

        # Everything the stage promised exists before anything is swapped in.
        # The `.splat` is one of them: the React viewer cannot read `.sog`
        # (review 2026-11-22 §3).
        self._check_output(published, "run_postprocess")
        if GS_EMIT_SIBLING:
            self._check_output(out_dir / f"scene{GS_SIBLING_SUFFIX}", "run_postprocess")
        self.record_stage_metrics(
            published_bytes=published.stat().st_size,
            source_bytes=Path(source).stat().st_size if Path(source).exists() else None,
        )
        # A third full copy of the model, archived to NFS with the run for no
        # reader; `reconstruction.ply` (COLMAP frame) remains the source of truth.
        if framed is not None:
            framed.unlink(missing_ok=True)

        self._swap_in_published(out_dir, final_dir)

        # Recorded against the served path, not the staging one, so the ARK and
        # the stage rows name a file that will still be there tomorrow.
        self.record_stage_artifacts(final_dir / published.name)
        self.opt_path = final_dir

        # An admin re-cut rewrote an already-published run: republish it into
        # the served tree the way `run_cleanup` does, or the new cut sat in the
        # run's archive directory and was never seen (review 2026-11-22 §3).
        if getattr(self, "_recut", False):
            if self.run.status == "Archived":
                dest = self._publish_artifacts()
                self.logger.info(f"Re-cut republished to {dest}.")
                self.record_stage_metrics(republished=1)
            else:
                self.logger.info(
                    f"Run {self.runID} is {self.run.status}, not published; "
                    "the re-cut is not republished."
                )
        self.logger.info(f"Finished post-processing GS for {self.siteStr}.")

    @staticmethod
    def _precompress(path: Path) -> None:
        """
        Write `<file>.gz` beside a published `.splat`, for nginx `gzip_static`.

        A `.splat` is raw quantised Gaussians and compresses to about 60% --
        288 MB to 171 MB on the largest one in this archive. It is deliberately
        absent from nginx's `gzip_types`, because compressing a file that size
        on every request would cost far more CPU than the bandwidth is worth.
        Compressing it once, here, costs nothing per request: `gzip_static`
        serves the `.gz` to any browser that accepts gzip and the original to
        one that does not.

        Level 6, not 9: on this file 9 buys about 1% for roughly four times the
        CPU, and this runs on the GPU queue's single slot at the end of a
        reconstruction.

        Best-effort, like the copy above. A missing `.gz` means the uncompressed
        file is served, which is what happens today; a raised exception here
        would lose a run that has otherwise finished.
        """
        import gzip

        try:
            target = path.with_name(path.name + ".gz")
            with (
                path.open("rb") as raw,
                gzip.open(target, "wb", compresslevel=6) as out,
            ):
                shutil.copyfileobj(raw, out, length=1024 * 1024)
            # Kept only if it saves more than a tenth. Measured across the 60
            # published splats, 59 compress to 93-95% -- the format is already
            # quantised binary -- so this usually deletes what it just wrote,
            # which is the correct outcome and cheaper than a second file that
            # saves nothing.
            if target.stat().st_size > path.stat().st_size * 0.9:
                target.unlink(missing_ok=True)
        except OSError:
            return

    def _swap_in_published(self, staging: Path, final: Path) -> None:
        """
        Replace `final` with `staging` by rename, keeping the old set until the
        new one is in place.

        Two renames rather than one because `os.replace` will not put a
        directory over a non-empty directory. The old set is moved aside first,
        so a crash between the two leaves `published.previous` on disk and the
        next run cleans it up -- recoverable, and never a half-written mixture.
        """
        previous = final.with_name(final.name + ".previous")
        if previous.exists():
            shutil.rmtree(previous)
        if final.exists():
            os.replace(final, previous)
        os.replace(staging, final)
        if previous.exists():
            shutil.rmtree(previous)
        self.logger.info(f"Published set swapped into {final}.")
