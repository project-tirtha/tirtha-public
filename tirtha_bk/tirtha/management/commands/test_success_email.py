from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings


class Command(BaseCommand):
    help = (
        "Test email configuration by sending a test contribution success notification"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            help="Email address to send test notification to (defaults to test email)",
        )

    def handle(self, *args, **options):
        self.stdout.write(
            "Testing contribution processing success email notification..."
        )

        # Create mock objects for testing
        mock_mesh = type(
            "MockMesh",
            (),
            {
                "name": "Test Heritage Temple",
                "ID": "test-mesh-success-12345",
            },
        )()

        mock_contribution = type(
            "MockContribution",
            (),
            {
                "ID": "test-contrib-success-uuid-12345",
                "mesh": mock_mesh,
            },
        )()

        # Get recipient email
        if options["email"]:
            recipient_email = options["email"]
            contributor_name = "Test User"
            self.stdout.write(f"Sending test email to: {options['email']}")
        else:
            recipient_email = settings.ADMIN_MAIL
            contributor_name = "Test Contributor"
            self.stdout.write(f"Sending test email to: {recipient_email}")

        # Prepare email content
        subject = f"Your Contribution to {mock_mesh.name} is Ready!"

        # Template context
        context = {
            "contribution_id": mock_contribution.ID,
            "mesh_id": mock_mesh.ID,
            "mesh_name": mock_mesh.name,
            "contributor_email": recipient_email,
            "contributor_name": contributor_name,
            "operation_type": "aV",  # Will be displayed as "Photogrammetry" in template
            "mesh_url": f"{settings.BASE_URL}/mesh/{mock_mesh.ID}/",
            "processing_duration": "2:15:30",  # Mock 2 hours 15 minutes 30 seconds
            "ark_url": f"{settings.BASE_URL}/ark:/999999/a1b2c3d4e5",
            "ark_id": "999999/a1b2c3d4e5",
            "arks_info_url": "https://arks.org/",
            "base_url": settings.BASE_URL,
        }

        try:
            # Render email templates
            html_message = render_to_string(
                "tirtha/emails/contribution_processing_success.html", context
            )
            text_message = render_to_string(
                "tirtha/emails/contribution_processing_success.txt", context
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

        # Check if contributor emails are enabled
        mail_contrib_toggle = getattr(settings, "MAIL_CONTRIB_TOGGLE", True)
        self.stdout.write(f"MAIL_CONTRIB_TOGGLE: {mail_contrib_toggle}")

        if not mail_contrib_toggle:
            self.stdout.write(
                self.style.WARNING(
                    "MAIL_CONTRIB_TOGGLE is disabled - success emails would not be sent in real scenario"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "MAIL_CONTRIB_TOGGLE is enabled - success emails will be sent"
                )
            )

        # Send test email
        try:
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_message,
                from_email=from_email,
                to=[recipient_email],
            )

            email.attach_alternative(html_message, "text/html")
            email.send(fail_silently=False)

            self.stdout.write(
                self.style.SUCCESS(
                    f"Test success notification email sent successfully to: {recipient_email}"
                )
            )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to send test email: {e}"))
            self.stdout.write("Please check your email configuration in settings.py")
