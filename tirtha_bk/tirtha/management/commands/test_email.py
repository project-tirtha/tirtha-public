from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings


class Command(BaseCommand):
    help = "Test email configuration by sending a test new contributor notification"

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            help="Email address to send test notification to (defaults to admin emails)",
        )

    def handle(self, *args, **options):
        self.stdout.write("Testing email configuration...")

        # Create a mock contributor for testing
        mock_contributor = type(
            "MockContributor",
            (),
            {
                "name": "Test User",
                "email": "test.user@example.com",
                "ID": "test-uuid-12345",
                "created_at": None,
            },
        )()

        # Set created_at to current time
        from django.utils import timezone

        mock_contributor.created_at = timezone.now()

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
        subject = "TEST: New Contributor Sign-up Notification"

        context = {
            "contributor": mock_contributor,
            "admin_url": f"{settings.BASE_URL}/admin/tirtha/contributor/{mock_contributor.ID}/change/",
            "base_admin_url": f"{settings.BASE_URL}/admin/",
        }

        try:
            # Render email templates
            html_message = render_to_string(
                "tirtha/emails/new_contributor_notification.html", context
            )
            text_message = render_to_string(
                "tirtha/emails/new_contributor_notification.txt", context
            )

            # Create email message
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_message,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", settings.ADMIN_MAIL),
                to=recipient_emails,
            )

            # Add HTML version
            email.attach_alternative(html_message, "text/html")

            # Check email configuration first
            self.stdout.write("Email configuration:")
            self.stdout.write(f"  Backend: {settings.EMAIL_BACKEND}")
            self.stdout.write(
                f"  Host: {getattr(settings, 'EMAIL_HOST', 'Not configured')}"
            )
            self.stdout.write(
                f"  Port: {getattr(settings, 'EMAIL_PORT', 'Not configured')}"
            )
            self.stdout.write(
                f"  TLS: {getattr(settings, 'EMAIL_USE_TLS', 'Not configured')}"
            )
            self.stdout.write(
                f"  From: {getattr(settings, 'DEFAULT_FROM_EMAIL', 'Not configured')}"
            )

            if not getattr(settings, "EMAIL_HOST_USER", ""):
                self.stdout.write(
                    self.style.WARNING(
                        "Warning: EMAIL_HOST_USER is not configured. "
                        "Email sending may fail without proper SMTP authentication."
                    )
                )

            if not getattr(settings, "EMAIL_HOST_PASSWORD", ""):
                self.stdout.write(
                    self.style.WARNING(
                        "Warning: EMAIL_HOST_PASSWORD is not configured. "
                        "Email sending may fail without proper SMTP authentication."
                    )
                )

            # Send the email
            email.send(fail_silently=False)

            self.stdout.write(
                self.style.SUCCESS(
                    f"Test email sent successfully to {', '.join(recipient_emails)}"
                )
            )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to send test email: {e}"))

            # Provide troubleshooting help
            self.stdout.write("\nTroubleshooting tips:")
            self.stdout.write(
                "1. Ensure EMAIL_HOST_USER and EMAIL_HOST_PASSWORD are configured"
            )
            self.stdout.write(
                "2. For Gmail, use App Passwords instead of regular passwords"
            )
            self.stdout.write("3. Check firewall/network settings for SMTP access")
            self.stdout.write("4. Verify SMTP server settings are correct")
