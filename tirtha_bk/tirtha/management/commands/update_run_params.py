from typing import Tuple, List

from django.core.management.base import BaseCommand, CommandError

from tirtha.models import Run


def _parse_triplet(val: str) -> Tuple[float, float, float]:
    try:
        parts = [float(x.strip()) for x in val.split(",")]
        if len(parts) != 3:
            raise ValueError()
        return (parts[0], parts[1], parts[2])
    except Exception:
        raise CommandError(f"Invalid triplet: '{val}'. Expected format: x,y,z")


class Command(BaseCommand):
    help = (
        "Update run parameters for GS or aV runs. "
        "Examples: --cam-pos "
        "-15, -5, -16"
        " --cam-lookat "
        "0.48,-1.47,-0.27"
        " --cam-up "
        "0,1,0"
        ""
    )

    def add_arguments(self, parser):
        parser.add_argument("run_id", help="Run ID to update")
        parser.add_argument(
            "--cam-pos",
            dest="cam_pos",
            help="Camera position as comma-separated triplet: x,y,z",
        )
        parser.add_argument(
            "--cam-lookat",
            dest="cam_lookat",
            help="Camera look-at as comma-separated triplet: x,y,z",
        )
        parser.add_argument(
            "--cam-up",
            dest="cam_up",
            help="Camera up vector as comma-separated triplet: x,y,z",
        )
        parser.add_argument(
            "--rota",
            dest="rota",
            help="Rotation angles as comma-separated triplet (Z,X,Y) in degrees",
        )
        parser.add_argument(
            "--focal-adjustment",
            dest="focal_adjustment",
            type=float,
            help="Focal length adjustment (float)",
        )
        parser.add_argument(
            "--antialiased",
            dest="antialiased",
            choices=("true", "false"),
            help="Set antialiased (true/false)",
        )

    def handle(self, *args, **options):
        run_id = options["run_id"]

        try:
            run = Run.objects.get(ID=run_id)
        except Run.DoesNotExist:
            raise CommandError(f"Run not found: {run_id}")

        updated_fields: List[str] = []

        # GS params
        if options.get("cam_pos"):
            x, y, z = _parse_triplet(options["cam_pos"])
            run.initCamPosX = x
            run.initCamPosY = y
            run.initCamPosZ = z
            updated_fields.extend(["initCamPosX", "initCamPosY", "initCamPosZ"])

        if options.get("cam_lookat"):
            x, y, z = _parse_triplet(options["cam_lookat"])
            run.initCamLookAtX = x
            run.initCamLookAtY = y
            run.initCamLookAtZ = z
            updated_fields.extend(
                [
                    "initCamLookAtX",
                    "initCamLookAtY",
                    "initCamLookAtZ",
                ]
            )

        if options.get("cam_up"):
            x, y, z = _parse_triplet(options["cam_up"])
            run.camUpX = x
            run.camUpY = y
            run.camUpZ = z
            updated_fields.extend(["camUpX", "camUpY", "camUpZ"])

        # aV params
        if options.get("rota"):
            z, x, y = _parse_triplet(options["rota"])  # order: Z,X,Y
            run.rotaZ = int(round(z))
            run.rotaX = int(round(x))
            run.rotaY = int(round(y))
            updated_fields.extend(["rotaZ", "rotaX", "rotaY"])

        if options.get("focal_adjustment") is not None:
            run.focalAdjustment = options.get("focal_adjustment")
            updated_fields.append("focalAdjustment")

        if options.get("antialiased") is not None:
            run.antialiased = True if options.get("antialiased") == "true" else False
            updated_fields.append("antialiased")

        if not updated_fields:
            raise CommandError("No parameters provided to update.")

        run.save(update_fields=updated_fields)

        self.stdout.write(
            self.style.SUCCESS(f"Updated Run {run.ID}: {', '.join(updated_fields)}")
        )
