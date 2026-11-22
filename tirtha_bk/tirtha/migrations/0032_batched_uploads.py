"""
Batched uploads: open contributions and per-file client keys.

Schema
------
- `Contribution.finalized_at` -- NULL while a batched upload is still open.
  Nullable, defaulting to "now" both in Python and **in the database**
  (`db_default=Now()`, a column `DEFAULT now()` that stays). The database
  default is what makes the previous release safe next to this schema: its
  INSERT does not name the column, so the row it makes is finalized, not open.
  Only the batched upload writes NULL, explicitly. `now()` is STABLE, not
  volatile, so on Postgres >= 11 adding the column is catalogue-only, no table
  rewrite. Indexed, because the sweeps filter on it.
- `Contribution.last_batch_at` -- nullable, no default, no rewrite.
- `Contribution.confirmed_batches`, `Contribution.received_bytes` -- NOT NULL
  with a constant database default of 0 (catalogue-only), again so an INSERT
  from code that predates them succeeds.
- `Image.client_key`, `SourceVideo.client_key` -- nullable varchar(64).
- A unique constraint on `(contribution, client_key)` for both. NULLs are
  distinct in Postgres and SQLite, so the ~19,600 existing rows (all NULL)
  cannot collide, and the index also serves the "which keys does this
  contribution already hold" lookup.

Data
----
Existing rows count as finalized: `finalized_at = contributed_at`. Run *after*
every schema operation, so the UPDATE is the last thing in the transaction and
no later ALTER meets pending trigger events on Postgres. It touches only a
non-key column, so no foreign-key trigger is queued at all.

Reversible: the data step reverses to a no-op, and reversing each `AddField`
drops its column (and with it the database default). Reverse it *before*
restoring the previous code: see DEPLOY.md.
"""

import django.db.models.functions.datetime
import django.utils.timezone
from django.db import migrations, models


def backfill_finalized_at(apps, schema_editor):
    Contribution = apps.get_model("tirtha", "Contribution")
    Contribution.objects.update(finalized_at=models.F("contributed_at"))


class Migration(migrations.Migration):
    dependencies = [
        ("tirtha", "0031_admin_action_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="contribution",
            name="finalized_at",
            field=models.DateTimeField(
                blank=True,
                db_default=django.db.models.functions.datetime.Now(),
                db_index=True,
                default=django.utils.timezone.now,
                null=True,
                verbose_name="Upload finalized at",
            ),
        ),
        migrations.AddField(
            model_name="contribution",
            name="last_batch_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="Last upload batch at"
            ),
        ),
        migrations.AddField(
            model_name="contribution",
            name="confirmed_batches",
            field=models.PositiveIntegerField(
                db_default=0,
                default=0,
                verbose_name="Batches acknowledged by the client",
            ),
        ),
        migrations.AddField(
            model_name="contribution",
            name="received_bytes",
            field=models.BigIntegerField(
                db_default=0, default=0, verbose_name="Bytes received"
            ),
        ),
        migrations.AddField(
            model_name="image",
            name="client_key",
            field=models.CharField(
                blank=True, max_length=64, null=True, verbose_name="Client file key"
            ),
        ),
        migrations.AddField(
            model_name="sourcevideo",
            name="client_key",
            field=models.CharField(
                blank=True, max_length=64, null=True, verbose_name="Client file key"
            ),
        ),
        migrations.AddConstraint(
            model_name="image",
            constraint=models.UniqueConstraint(
                fields=("contribution", "client_key"), name="image_unique_client_key"
            ),
        ),
        migrations.AddConstraint(
            model_name="sourcevideo",
            constraint=models.UniqueConstraint(
                fields=("contribution", "client_key"),
                name="sourcevideo_unique_client_key",
            ),
        ),
        migrations.RunPython(backfill_finalized_at, migrations.RunPython.noop),
    ]
