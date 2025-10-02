from django.core.management.base import BaseCommand
from django.conf import settings
from tirtha.email_utils import send_contribution_processing_failure_email


class Command(BaseCommand):
    help = "Test email configuration by sending a test contribution processing failure notification"

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            help="Email address to send test notification to (defaults to admin emails)",
        )

    def handle(self, *args, **options):
        self.stdout.write(
            "Testing contribution processing failure email notification..."
        )

        # Mock test data
        test_contribution_id = "test-contrib-12345678"
        test_mesh_id = "test-mesh-87654321"
        test_mesh_name = "Test Heritage Site"
        test_contributor_email = "test.contributor@example.com"
        test_processing_step = "Image Quality Check"
        test_error_message = """
ValueError: Image quality assessment failed
    at check_images() line 156
    MANIQA model inference error: CUDA out of memory
    
This is a test error message to verify email notifications are working correctly.
        """.strip()
        test_log_file = "/var/www/tirtha/prod/logs/ImageOps/ImageOps_test.log"
        test_run_id = "test-run-11223344"
        test_operation_type = "ImageOps"

        try:
            # Send test email
            success = send_contribution_processing_failure_email(
                contribution_id=test_contribution_id,
                mesh_id=test_mesh_id,
                mesh_name=test_mesh_name,
                contributor_email=test_contributor_email,
                processing_step=test_processing_step,
                error_message=test_error_message,
                log_file_path=test_log_file,
                run_id=test_run_id,
                operation_type=test_operation_type,
            )

            if success:
                self.stdout.write(
                    self.style.SUCCESS("✅ Test email sent successfully!")
                )
            else:
                self.stdout.write(
                    self.style.ERROR(
                        "❌ Test email sending failed (check logs for details)"
                    )
                )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Failed to send test email: {e}"))

        # Display configuration info
        self.stdout.write("\n" + "=" * 50)
        self.stdout.write("Email Configuration:")
        self.stdout.write(
            f"  Backend: {getattr(settings, 'EMAIL_BACKEND', 'Not configured')}"
        )
        self.stdout.write(
            f"  Host: {getattr(settings, 'EMAIL_HOST', 'Not configured')}"
        )
        self.stdout.write(
            f"  Port: {getattr(settings, 'EMAIL_PORT', 'Not configured')}"
        )
        self.stdout.write(
            f"  Use TLS: {getattr(settings, 'EMAIL_USE_TLS', 'Not configured')}"
        )
        self.stdout.write(
            f"  From Email: {getattr(settings, 'DEFAULT_FROM_EMAIL', 'Not configured')}"
        )

        admin_emails = [email for name, email in getattr(settings, "ADMINS", [])]
        if not admin_emails:
            admin_emails = [getattr(settings, "ADMIN_MAIL", "Not configured")]

        self.stdout.write(f"  Admin Emails: {', '.join(admin_emails)}")
        self.stdout.write("=" * 50)
