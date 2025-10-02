from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings


class Command(BaseCommand):
    help = (
        "Test email configuration by sending a test admin run completion notification"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            help="Email address to send test notification to (defaults to admin emails)",
        )

    def handle(self, *args, **options):
        self.stdout.write("Testing admin run completion email notification...")

        # Create mock objects for testing
        mock_mesh = type(
            "MockMesh",
            (),
            {
                "name": "Test Heritage Temple",
                "ID": "test-mesh-admin-12345",
            },
        )()

        mock_contribution = type(
            "MockContribution",
            (),
            {
                "ID": "test-contrib-admin-uuid-12345",
                "mesh": mock_mesh,
            },
        )()

        # Get recipient email
        if options["email"]:
            recipient_emails = [options["email"]]
            self.stdout.write(f"Sending test email to: {options['email']}")
        else:
            admin_emails = [email for name, email in getattr(settings, "ADMINS", [])]
            if not admin_emails:
                admin_emails = [settings.ADMIN_MAIL]
            recipient_emails = admin_emails
            self.stdout.write(
                f"Sending test email to admin addresses: {', '.join(recipient_emails)}"
            )

        # Prepare email content
        subject = f"Run Completed Successfully: {mock_mesh.name} - MeshOps"

        # Template context
        context = {
            "contribution_id": mock_contribution.ID,
            "mesh_id": mock_mesh.ID,
            "mesh_name": mock_mesh.name,
            "contributor_email": "test.contributor@example.com",
            "contributor_name": "Test Contributor",
            "operation_type": "aV",  # Will be displayed as "Photogrammetry" in template
            "run_id": "test-run-admin-11223344",
            "processing_duration": "2:15:30",  # Mock 2 hours 15 minutes 30 seconds
            "ark_url": f"{settings.BASE_URL}/ark:/999999/a1b2c3d4e5",
            "ark_id": "999999/a1b2c3d4e5",
            "arks_info_url": "https://arks.org/",
            "base_url": settings.BASE_URL,
            "contribution_admin_url": f"{settings.BASE_URL}/admin/tirtha/contribution/{mock_contribution.ID}/change/",
            "mesh_admin_url": f"{settings.BASE_URL}/admin/tirtha/mesh/{mock_mesh.ID}/change/",
            "base_admin_url": f"{settings.BASE_URL}/admin/",
        }

        try:
            # Render email templates
            html_message = render_to_string(
                "tirtha/emails/admin_run_completion.html", context
            )
            text_message = render_to_string(
                "tirtha/emails/admin_run_completion.txt", context
            )

            self.stdout.write(
                self.style.SUCCESS("Email templates rendered successfully")
            )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"Failed to render email templates: {e}")
            )
            return

        # Test email settings
        try:
            from_email = getattr(settings, "DEFAULT_FROM_EMAIL", settings.ADMIN_MAIL)
            self.stdout.write(f"From email: {from_email}")
            self.stdout.write(f"Email backend: {settings.EMAIL_BACKEND}")

            if hasattr(settings, "EMAIL_HOST"):
                self.stdout.write(f"SMTP Host: {settings.EMAIL_HOST}")
                self.stdout.write(f"SMTP Port: {settings.EMAIL_PORT}")
                self.stdout.write(
                    f"TLS Enabled: {getattr(settings, 'EMAIL_USE_TLS', False)}"
                )

        except AttributeError as e:
            self.stdout.write(self.style.WARNING(f"Email setting missing: {e}"))

        # Send test email
        try:
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_message,
                from_email=from_email,
                to=recipient_emails,
            )

            email.attach_alternative(html_message, "text/html")
            email.send(fail_silently=False)

            self.stdout.write(
                self.style.SUCCESS(
                    f"Test admin run completion email sent successfully to: {', '.join(recipient_emails)}"
                )
            )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to send test email: {e}"))
            self.stdout.write("Please check your email configuration in settings.py")
