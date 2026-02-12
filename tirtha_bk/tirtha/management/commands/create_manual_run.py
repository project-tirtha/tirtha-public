import json
import shutil
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils import timezone
from django.db import IntegrityError

# Local imports
from tirtha.models import Mesh, Run, ARK, Contributor
from tirtha.utilsark import generate_noid, noid_check_digit


ADMIN_MAIL = getattr(settings, "ADMIN_MAIL", None)


class Command(BaseCommand):
    help = "Create a manual Run for a Mesh and publish a provided .glb/.splat file with an ARK."

    def add_arguments(self, parser):
        parser.add_argument("mesh", help="Mesh ID or Mesh verbose_id (VID)")
        parser.add_argument(
            "file", help="Path to .glb or .splat file to publish for the Run"
        )
        parser.add_argument(
            "--contrib-email",
            dest="contrib_email",
            help="Email of Contributor to associate with the Run (defaults to ADMIN_MAIL)",
            default=ADMIN_MAIL,
        )
        parser.add_argument(
            "--ark-len",
            dest="ark_len",
            type=int,
            default=16,
            help="Length of generated NOID for ARK (default: 16)",
        )

    def handle(self, *args, **options):
        mesh_ident = options["mesh"]
        file_path = Path(options["file"]).expanduser()

        if not file_path.exists():
            raise CommandError(f"File not found: {file_path}")

        # Resolve mesh by ID or verbose_id
        mesh = None
        try:
            mesh = Mesh.objects.get(ID=mesh_ident)
        except Mesh.DoesNotExist:
            try:
                mesh = Mesh.objects.get(verbose_id=mesh_ident)
            except Mesh.DoesNotExist:
                raise CommandError(f"Mesh not found for identifier: {mesh_ident}")

        suffix = file_path.suffix.lower().lstrip(".")
        if suffix == "glb":
            kind = "aV"
        elif suffix == "splat":
            kind = "GS"
        else:
            raise CommandError("Unsupported file type. Provide a .glb or .splat file.")

        # Create Run (status Manual)
        run = Run.objects.create(mesh=mesh, kind=kind, status="Manual")

        # Associate contributor: provided email or admin user (ADMIN_MAIL / ADMIN_NAME)
        admin_email = getattr(settings, "ADMIN_MAIL", None)
        admin_name = getattr(settings, "ADMIN_NAME", None)

        contrib_email = options.get("contrib_email") or admin_email
        contrib = None
        if contrib_email:
            # Prefer lookup by email
            try:
                contrib = Contributor.objects.get(email=contrib_email)
                run.contributors.add(contrib)
            except Contributor.DoesNotExist:
                raise CommandError(
                    f"Contributor not found with email: {contrib_email} or name: {admin_name}"
                )

        # Ensure published directory exists and copy file
        static_root = Path(settings.STATIC_ROOT)
        pub_dir = static_root / f"models/{mesh.ID}/published"
        pub_dir.mkdir(parents=True, exist_ok=True)

        dest_fname = f"{mesh.ID}_{run.ID}.{suffix}"
        dest_path = pub_dir / dest_fname
        shutil.copy2(file_path, dest_path)

        # Prepare and generate ARK using utilities
        naan = getattr(settings, "ARK_NAAN", "")
        shoulder = getattr(settings, "ARK_SHOULDER", "")
        base_url = getattr(settings, "BASE_URL", "")

        run.ended_at = timezone.now()

        metadata = {
            "monument": {
                "name": str(mesh.name),
                "location": f"{mesh.district}, {mesh.state}, {mesh.country}",
                "verbose_id": str(mesh.verbose_id),
                "thumbnail": f"{base_url}{mesh.thumbnail.url}"
                if mesh.thumbnail
                else "",
                "description": str(mesh.description),
                "completed": True if mesh.completed else False,
            },
            "run": {
                "ID": str(run.ID),
                "ended_at": str(run.ended_at),
                "contributors": list(run.contributors.values_list("name", flat=True)),
                "images": int(run.images.count()),
            },
            "notice": (
                "This ARK was generated & is managed by Project Tirtha (https://smlab.niser.ac.in/project/tirtha/)."
            ),
        }

        metadata_json = json.dumps(metadata)

        # Generate unique ARK
        collisions = 0
        ark_obj = None
        while True:
            noid = generate_noid(options.get("ark_len", 16))
            base_ark_string = f"{naan}{shoulder}{noid}"
            check_digit = noid_check_digit(base_ark_string)
            ark_string = f"{base_ark_string}{check_digit}"
            try:
                ark_obj = ARK.objects.create(
                    ark=ark_string,
                    naan=naan,
                    shoulder=shoulder,
                    assigned_name=f"{noid}{check_digit}",
                    url=f"{base_url}/static/models/{mesh.ID}/published/{dest_fname}",
                    metadata=metadata_json,
                )
                break
            except IntegrityError:
                collisions += 1
                continue

        run.ark = ark_obj
        run.save()

        self.stdout.write(
            self.style.SUCCESS(f"Created Manual Run {run.ID} for Mesh {mesh.ID}")
        )
        self.stdout.write(self.style.SUCCESS(f"Published file: {dest_path}"))
        self.stdout.write(
            self.style.SUCCESS(f"ARK: ark:/{ark_obj.ark} (collisions: {collisions})")
        )
