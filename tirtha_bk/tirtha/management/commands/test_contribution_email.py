from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone


class Command(BaseCommand):
    help = "Test email configuration by sending a test new contribution notification"

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            help="Email address to send test notification to (defaults to admin emails)",
        )

    def handle(self, *args, **options):
        self.stdout.write("Testing new contribution email notification...")

        # Create mock objects for testing
        mock_mesh = type(
            "MockMesh",
            (),
            {
                "name": "Test Meditation Center",
                "ID": "test-mesh-12345",
                "district": "Test District",
                "state": "Test State",
                "country": "Test Country",
            },
        )()

        mock_contributor = type(
            "MockContributor",
            (),
            {
                "name": "Test Contributor",
                "email": "contributor@example.com",
            },
        )()

        mock_contribution = type(
            "MockContribution",
            (),
            {
                "ID": "test-contribution-uuid-12345",
                "mesh": mock_mesh,
                "contributor": mock_contributor,
                "contributed_at": timezone.now(),
                "processed": False,
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

        # Mock image count and totals
        image_count = 15
        total_images = 125
        total_contributions = 8

        # Prepare email content
        subject = f"New Contribution: {mock_mesh.name} - {image_count} images"

        # Template context
        context = {
            "contribution": mock_contribution,
            "image_count": image_count,
            "total_images": total_images,
            "total_contributions": total_contributions,
            "contribution_admin_url": f"{settings.BASE_URL}/admin/tirtha/contribution/{mock_contribution.ID}/change/",
            "mesh_admin_url": f"{settings.BASE_URL}/admin/tirtha/mesh/{mock_mesh.ID}/change/",
            "base_admin_url": f"{settings.BASE_URL}/admin/",
        }

        try:
            # Render email templates
            html_message = render_to_string(
                "tirtha/emails/new_contribution_notification.html", context
            )
            text_message = render_to_string(
                "tirtha/emails/new_contribution_notification.txt", context
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
                    f"Test contribution notification email sent successfully to: {', '.join(recipient_emails)}"
                )
            )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to send test email: {e}"))
            self.stdout.write("Please check your email configuration in settings.py")

        # Test ignore list functionality
        ignore_list = getattr(settings, "CONTRIB_IGNORE_LIST", [])
        if ignore_list:
            self.stdout.write(f"CONTRIB_IGNORE_LIST contains: {ignore_list}")
            if mock_contributor.email in ignore_list:
                self.stdout.write(
                    self.style.WARNING(
                        f"Note: {mock_contributor.email} would be ignored in real scenario"
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{mock_contributor.email} would trigger notifications"
                    )
                )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "CONTRIB_IGNORE_LIST not configured - all contributions will trigger notifications"
                )
            )
