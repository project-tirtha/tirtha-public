"""
review 2026-11-22: moderation provenance, scheduler state, contributor PROTECT.

* `Image.safety_moderated_at` -- set when a person decides `safety_status`, so
  `ImageOps` never overwrites that decision (§1 #6). Backfilled for rows whose
  status cannot have come from the classifier as it stands: a status with no
  `safety_checked_at`, `flagged` (the classifier never writes it), or a status
  that disagrees with the stored score at the configured threshold.
* `Site.recon_queued_at` / `Site.recon_failures_reset_at` -- scheduler state (§2).
  Every existing site gets `recon_failures_reset_at = now()`, so Error runs from
  before the back-off existed do not cool a site down on the first pass.
* `Contribution.imageops_enqueued_at` -- when ingest was last enqueued, so the
  stuck-ingest sweep does not enqueue duplicates.
* `Contribution.contributor` CASCADE -> PROTECT (§4). Django enforces
  `on_delete` in Python; on Postgres this emits no SQL.

All four column additions are nullable with no default, so on Postgres they are
catalogue-only changes: no table rewrite and only a brief ACCESS EXCLUSIVE lock.
The backfill is one UPDATE over `tirtha_image` touching only the rows that
match (a handful, not the ~19k table), and one over `tirtha_site` (a few
hundred rows). Both data steps reverse as no-ops.
"""

from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


def backfill_moderated(apps, schema_editor):
    from django.utils import timezone

    Image = apps.get_model("tirtha", "Image")
    threshold = float(getattr(settings, "NSFW_THRESHOLD", 0.5))
    human = (
        Q(safety_status="flagged")
        | (~Q(safety_status="pending") & Q(safety_checked_at__isnull=True))
        | Q(safety_status="rejected", safety_score__lt=threshold)
        | Q(safety_status="safe", safety_score__gte=threshold)
    )
    Image.objects.filter(human, safety_moderated_at__isnull=True).update(
        safety_moderated_at=timezone.now()
    )


def start_backoff_afresh(apps, schema_editor):
    """Historical Error runs predate the back-off; do not count them."""
    from django.utils import timezone

    Site = apps.get_model("tirtha", "Site")
    Site.objects.update(recon_failures_reset_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [
        ("tirtha", "0029_seed_geo_manual"),
    ]

    operations = [
        migrations.AddField(
            model_name="image",
            name="safety_moderated_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="Safety decided by a moderator at"
            ),
        ),
        migrations.AddField(
            model_name="site",
            name="recon_queued_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Set by the scheduler when a reconstruction is enqueued; "
                "cleared when it starts. Clear it by hand only if the queued task "
                "was lost.",
                null=True,
                verbose_name="Reconstruction queued at",
            ),
        ),
        migrations.AddField(
            model_name="site",
            name="recon_failures_reset_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Set to now to let the scheduler retry a site now, "
                "rather than when its cool-down after repeated failed "
                "reconstructions ends.",
                null=True,
                verbose_name="Failure back-off reset at",
            ),
        ),
        migrations.AlterField(
            model_name="contribution",
            name="contributor",
            field=models.ForeignKey(
                on_delete=models.deletion.PROTECT,
                related_name="contributions",
                to="tirtha.contributor",
                verbose_name="Contributor ID",
            ),
        ),
        migrations.AddField(
            model_name="contribution",
            name="imageops_enqueued_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="Ingest last enqueued at"
            ),
        ),
        migrations.RunPython(backfill_moderated, migrations.RunPython.noop),
        migrations.RunPython(start_backoff_afresh, migrations.RunPython.noop),
    ]
