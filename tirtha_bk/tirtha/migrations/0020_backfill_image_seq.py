"""
Backfill `Image.seq` for existing images (internal design notes).

`seq` is a per-site monotonic counter that gives COLMAP's `sequential_matcher`
something meaningful to match consecutive images on. Sorted symlink order equals
`seq` order equals capture order, as closely as the surviving metadata allows.

**A column update only — no file moves.** That is the whole point of the
symlink design: applying ordered names to the media store would mean renaming
~19,300 archived files through the ORM, and `django_cleanup` deletes the old
file whenever a `FileField` changes.

**Ordered within a contribution, never globally by timestamp.** EXIF clocks are
not comparable across contributors: drift, timezones, and cameras whose clock
was never set. Contributions are blocked in arrival order; images are ordered
by capture time *inside* each block.
"""

from django.db import migrations


def backfill_seq(apps, schema_editor):
    Site = apps.get_model("tirtha", "Site")
    Contribution = apps.get_model("tirtha", "Contribution")
    Image = apps.get_model("tirtha", "Image")

    for site in Site.objects.all().iterator():
        seq = 0
        updates = []
        contributions = Contribution.objects.filter(site=site).order_by(
            "contributed_at", "ID"
        )
        for contribution in contributions:
            # date_taken first (EXIF, present on ~92% of the archive), then
            # upload order. Ties in created_at are common -- 88 of 118
            # multi-image contributions share a millisecond from bulk insert --
            # but a tie only swaps neighbours, which is invisible to sequential
            # matching's overlap window.
            images = Image.objects.filter(contribution=contribution).order_by(
                "date_taken", "created_at", "ID"
            )
            for img in images:
                if img.seq != seq:
                    img.seq = seq
                    updates.append(img)
                seq += 1
        if updates:
            Image.objects.bulk_update(updates, ["seq"], batch_size=500)


def clear_seq(apps, schema_editor):
    """Reversible: seq is derived, so dropping it loses nothing."""
    Image = apps.get_model("tirtha", "Image")
    Image.objects.update(seq=None)


class Migration(migrations.Migration):
    dependencies = [("tirtha", "0019_data_quality_fixes")]
    operations = [migrations.RunPython(backfill_seq, clear_seq)]
