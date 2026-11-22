"""`MeshOps`: the AliceVision photogrammetry path (kind "aV")."""

import shlex
import shutil
from pathlib import Path
from typing import Optional

from tirtha_recon import AliceVision, Logger

# Local imports
from tirtha.models import Site

from .base import BaseOps
from .common import ALICEVISION_DIRPATH, GLTFPACK_PATH, OBJ2GLTF_PATH


class MeshOps(BaseOps):
    """
    Site processing pipeline. Does the following:
    - Runs the aliceVision pipeline on a given `models.Site`
    - Runs `obj2gltf` to convert the obj file to gltf
    - Runs `gltfpack` (meshopt) to optimize the gltf file
    - Publishes the final output to the Tirtha site

    """

    def __init__(self, siteID: str, contrib_id: str) -> None:
        super().__init__(siteID=siteID, kind="aV", contrib_id=contrib_id)

        # Check if executables exist
        self.aV_exec = Path(ALICEVISION_DIRPATH)
        self.obj2gltf_exec = Path(OBJ2GLTF_PATH)
        self.gltfpack_exec = Path(GLTFPACK_PATH)
        self._check_exec(path=self.aV_exec)
        self._check_exec(exe=self.obj2gltf_exec)
        self._check_exec(exe=self.gltfpack_exec)

        # Logger setup for `AliceVision`
        MeshOps.av_logger = Logger(
            log_path=self.runDir,
            name=f"alicevision__{self.runID}",
        )

        # Specify the run order (else alphabetical order)
        self._run_order = [
            "run_aliceVision",
            "run_obj2gltf",
            "run_meshopt",
        ] + self._run_order_suffix

    def _check_exec(self, exe: str = None, path: Path = None) -> None:
        """
        Checks if the executables exist and are valid

        Parameters
        ----------
        exe : str, optional
            Name of the executable, by default None
        path : Path, optional
            Path to the executable, by default None

        Raises
        ------
        FileNotFoundError
            If the path to any of the executables is not found

        """
        if path is not None and not path.exists():
            self.logger.error(f"aliceVision executable not found: {path}")
            raise FileNotFoundError(f"aliceVision executable not found: {path}")

        if exe is not None:
            exe = shutil.which(exe)
            if not exe:
                self.logger.error(f"Executable not found: {exe}")
                raise FileNotFoundError(f"Executable not found: {exe}")

    def run_aliceVision(self) -> None:
        """
        Runs the aliceVision pipeline

        """
        from tirtha.workspace import materialise_site

        site = self.site
        siteStr = self.siteStr
        self.logger.info(f"Creating aliceVision worker for site {siteStr}...")

        """
        The materialised workspace, not the raw media directory.

        Unit 5.5 replaced the directory glob with a database-driven symlink
        farm -- but only on the GS path. `run_colmap` calls this; AliceVision
        was still handed `MEDIA/models/<id>/images` whole, so **every filter the
        moderation tools apply was advisory here**: an image a moderator marked
        Bad, and the 19,310 rows still sitting at `safety_status="pending"`,
        went into the photogrammetry reconstruction anyway. The quarantine held,
        because rejected files are physically moved -- but "rejected" and "the
        moderator said no" are not the same thing, and only the first was
        enforced.

        `reconstructable_images` is the single gate now, on both paths.
        """
        workspace, materialised = materialise_site(site)
        self.workspace = workspace
        self.logger.info(
            f"Workspace {workspace.root}: {materialised.linked} new link(s), "
            f"{materialised.already_present} already present."
        )

        try:
            aV = AliceVision(
                exec_path=self.aV_exec,
                input_dir=workspace.images,
                cache_dir=self.runDir,
                # If a prior run had errored out, set the current run to produce full tracebacks
                verboseLevel="trace" if site.status == "Error" else "info",
                logger=MeshOps.av_logger,
            )
        except Exception:
            self._handle_error(
                Exception(f"aliceVision worker failed for site {siteStr}."),
                "aliceVision",
            )

        try:
            self.logger.info(
                f"Running the aliceVision pipeline(s) for site {siteStr}..."
            )
            self.logger.info(f"Check log file: {aV.logger._log_file}")
            rotation = [site.rotaX, site.rotaY, site.rotaZ]
            orientMesh = site.orientMesh

            # Check if center_image exists
            center_image = site.center_image
            matches = list(self.imageDir.glob(f"{center_image}.*"))
            if not matches:
                center_image = None
                site.center_image = ""
                # One column, not a full save of a Site loaded at run start
                # (review 2026-11-22 §1 #8).
                Site.objects.filter(pk=site.pk).update(center_image="")

            aV._run_all(
                center_image=center_image,
                denoise=site.denoise,  # NOTE: Denoising smooths out too much at the moment
                rotation=rotation,
                orientMesh=orientMesh,
                estimateSpaceMinObservationAngle=site.minObsAng,
            )

            # Set for further processing
            self.textured_path = self.runDir / "15_texturing/"

            self.logger.info(
                f"Finished running aliceVision pipeline for site {siteStr}."
            )

        except Exception:
            self._handle_error(
                Exception(f"aliceVision pipeline failed for site {siteStr}."),
                "aliceVision",
            )

    def run_obj2gltf(self, options: Optional[dict] = {"secure": "true"}) -> None:
        """
        Runs obj2gltf to create .glb in `static_dir` from .obj in `textured_path`
        For decimated site only

        Parameters
        ----------
        options : Optional[dict]
            Additional key-val options to pass to the obj2gltf executable
            Check obj2gltf docs for more info.
            Default: {"secure": "true"}

        """
        out_path = self.runDir / "obj2gltf/"
        out_path.mkdir(parents=True, exist_ok=True)

        # Decimated site only
        inp = self.textured_path / "texturedDecimatedMesh/texturedMesh.obj"
        out = out_path / "decimatedGLB.glb"
        # An argv, not a shell string: the paths are not quoted by anyone.
        cmd = [str(self.obj2gltf_exec), "-i", str(inp), "-o", str(out)]
        for k, v in (options or {}).items():
            cmd += [f"--{k}"] + ([str(v)] if str(v) != "" else [])
        log_path = out_path / "obj2gltf.log"
        self.logger.info(f"Running obj2gltf for site {self.siteStr}.")
        self.logger.info(f"Command: {shlex.join(cmd)}")
        self._serialRunner(cmd, log_path)

        self._check_output(out, "obj2gltf")

        # Set for further processing
        self.glb_path = out_path

        self.logger.info(f"Finished running obj2gltf for site {self.siteStr}.")

    def run_meshopt(
        self,
        options: Optional[dict] = {
            "cc": "",  # No value needed
            "tc": "",  # No value needed
            "si": 0.6,  # Simplification factor
        },
    ) -> None:
        """
        Runs gltfpack's meshopt to compress and optimize glTF files
        For decimated site only

        Parameters
        ----------
        options : Optional[dict]
            Additional key-val options to pass to the gltfpack executable
            Check `gltfpack` docs for more info.
            Default: {'cc': '', 'tc': '', 'si': 0.6}

        """
        out_path = self.runDir / "meshopt/"
        out_path.mkdir(parents=True, exist_ok=True)

        if "si" in options.keys():
            if not (0 < options["si"] <= 1):
                self._handle_error(ValueError("'si' must be > 0 and <= 1"), "meshopt")

        # Decimated site only
        inp = self.glb_path / "decimatedGLB.glb"
        out = out_path / "decimatedOptGLB.glb"
        # An argv, not a shell string. Flags with an empty value ("cc", "tc")
        # take no argument.
        cmd = [str(self.gltfpack_exec), "-i", str(inp), "-o", str(out)]
        for k, v in (options or {}).items():
            cmd += [f"-{k}"] + ([str(v)] if str(v) != "" else [])
        log_path = out_path / "meshopt.log"
        self.logger.info(f"Running meshopt (gltfpack) for site {self.siteStr}.")
        self.logger.info(f"Commands: {shlex.join(cmd)}")
        self._serialRunner(cmd, log_path)

        self._check_output(out, "meshopt")

        # Set for further processing
        self.opt_path = out_path

        self.logger.info(
            f"Finished running meshopt (gltfpack) for site {self.siteStr}."
        )
