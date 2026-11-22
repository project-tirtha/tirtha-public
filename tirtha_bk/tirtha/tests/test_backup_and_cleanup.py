"""
Two scheduled tasks that reported success without doing their job.

`backup_task` kept every backup for ever -- a full 13 GB media tar per night,
onto the disk serving the site. `db_cleanup_task` was a single log line: a
privacy control that had never looked at a row.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import call, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from tirtha.models import Contribution, Contributor, Site
from tirtha.tasks import backup_task, db_cleanup_task


class BackupsAreRotated(TestCase):
    def test_both_backups_are_asked_to_clean(self):
        with patch("django.core.management.call_command") as run:
            backup_task()
        self.assertIn(call("dbbackup", clean=True), run.call_args_list)
        self.assertIn(call("mediabackup", clean=True), run.call_args_list)

    def _warnings_for(self, media_root, backup_location):
        with (
            override_settings(
                MEDIA_ROOT=str(media_root),
                DBBACKUP_STORAGE_OPTIONS={"location": str(backup_location)},
            ),
            patch("django.core.management.call_command"),
            patch("tirtha.tasks.Logger") as logger,
        ):
            backup_task()
        return [c.args[0] for c in logger.return_value.warning.call_args_list if c.args]

    def test_a_same_filesystem_target_is_reported(self):
        """
        `NFS_DIR` reads like a network mount. When it is not one, the log has to
        say so -- the name otherwise implies a guarantee that is not there, and
        on this deployment it is the same LVM volume as the live media.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "media").mkdir()
            (root / "archive" / "db_backups").mkdir(parents=True)
            warnings = self._warnings_for(
                root / "media", root / "archive" / "db_backups"
            )
        self.assertTrue(
            any("SAME filesystem" in w for w in warnings),
            f"expected a same-filesystem warning, got {warnings}",
        )

    def test_a_target_that_does_not_exist_yet_is_still_answerable(self):
        """The backup directory is created on first write."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "media").mkdir()
            warnings = self._warnings_for(
                root / "media", root / "archive" / "not-created-yet"
            )
        self.assertTrue(any("SAME filesystem" in w for w in warnings))

    def test_a_genuinely_separate_filesystem_is_not_warned_about(self):
        """A real off-machine target must not produce a nightly false alarm."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "media").mkdir()
            warnings = self._warnings_for(root / "media", "/proc")
        self.assertFalse([w for w in warnings if "SAME filesystem" in w])


class ContributorCleanupIsRealAndCautious(TestCase):
    def setUp(self):
        self.site = Site.objects.create(
            name="S", country="India", state="Odisha", district="Khordha"
        )

    def _contributor(self, email, *, age_days, contributed=False):
        person = Contributor.objects.create(name="X", email=email, active=True)
        Contributor.objects.filter(pk=person.pk).update(
            created_at=timezone.now() - timedelta(days=age_days)
        )
        if contributed:
            Contribution.objects.create(site=self.site, contributor=person)
        return person

    @override_settings(CONTRIBUTOR_CLEANUP_ENABLED=False)
    def test_it_reports_without_deleting_by_default(self):
        self._contributor("stale@example.org", age_days=90)
        db_cleanup_task()
        self.assertTrue(Contributor.objects.filter(email="stale@example.org").exists())

    @override_settings(CONTRIBUTOR_CLEANUP_ENABLED=True)
    def test_it_removes_only_the_ones_that_qualify(self):
        self._contributor("stale@example.org", age_days=90)
        self._contributor("recent@example.org", age_days=1)
        self._contributor("gave@example.org", age_days=90, contributed=True)

        db_cleanup_task()

        self.assertFalse(Contributor.objects.filter(email="stale@example.org").exists())
        # Signed in minutes ago and part-way through choosing files.
        self.assertTrue(Contributor.objects.filter(email="recent@example.org").exists())
        # Has contributions: nothing to clear.
        self.assertTrue(Contributor.objects.filter(email="gave@example.org").exists())

    @override_settings(CONTRIBUTOR_CLEANUP_ENABLED=True, ADMIN_MAIL="admin@example.org")
    def test_the_admin_account_is_never_removed(self):
        self._contributor("admin@example.org", age_days=900)
        db_cleanup_task()
        self.assertTrue(Contributor.objects.filter(email="admin@example.org").exists())

    @override_settings(
        CONTRIBUTOR_CLEANUP_ENABLED=True, CONTRIBUTOR_CLEANUP_MIN_AGE_DAYS=365
    )
    def test_the_age_floor_is_configurable(self):
        self._contributor("ninety@example.org", age_days=90)
        db_cleanup_task()
        self.assertTrue(Contributor.objects.filter(email="ninety@example.org").exists())
