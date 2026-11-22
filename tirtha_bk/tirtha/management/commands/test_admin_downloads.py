from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test import Client

from tirtha.models import Contribution, Run, Site


class Command(BaseCommand):
    help = "Test admin download endpoints (Run full, Run output, Site images, Contribution images)"

    def handle(self, *args, **options):
        client = Client()
        User = get_user_model()
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            self.stdout.write(
                self.style.WARNING(
                    "No superuser found; creating temporary superuser 'admintest'"
                )
            )
            admin_user = User.objects.create_superuser(
                username="admintest", email="", password="admintestpass"
            )

        client.force_login(admin_user)

        # Test Run full
        run = Run.objects.first()
        if run:
            try:
                resp = client.get(f"/admin/tirtha/run/{run.ID}/download_run_full/")
                size = len(resp.content) if hasattr(resp, "content") else 0
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Run full download OK: run={run.ID} bytes={size}"
                    )
                )
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Run full download FAILED: {e}"))
        else:
            self.stdout.write(self.style.WARNING("No Run objects to test"))

        # Test Run output
        if run:
            try:
                resp = client.get(f"/admin/tirtha/run/{run.ID}/download_output/")
                size = len(resp.content) if hasattr(resp, "content") else 0
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Run output download OK: run={run.ID} bytes={size}"
                    )
                )
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Run output download FAILED: {e}"))

        # Test Site
        site = Site.objects.first()
        if site:
            try:
                resp = client.get(f"/admin/tirtha/site/{site.ID}/download_images/")
                size = len(resp.content) if hasattr(resp, "content") else 0
                self.stdout.write(
                    self.style.SUCCESS(f"Site download OK: site={site.ID} bytes={size}")
                )
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Site download FAILED: {e}"))
        else:
            self.stdout.write(self.style.WARNING("No Site objects to test"))

        # Test Contribution
        contrib = Contribution.objects.first()
        if contrib:
            try:
                resp = client.get(
                    f"/admin/tirtha/contribution/{contrib.ID}/download_images/"
                )
                size = len(resp.content) if hasattr(resp, "content") else 0
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Contribution download OK: contrib={contrib.ID} bytes={size}"
                    )
                )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"Contribution download FAILED: {e}")
                )
        else:
            self.stdout.write(self.style.WARNING("No Contribution objects to test"))

        self.stdout.write(self.style.NOTICE("Admin download tests completed."))
