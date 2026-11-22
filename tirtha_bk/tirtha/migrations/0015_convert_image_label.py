"""
Convert Image.label into evidence-shaped fields (internal design notes).

The old field stored a single verdict on two orthogonal axes -- safety and
quality -- so it could not express "safe but blurry", and it carried no record
of who decided or on what basis.

The production values were applied **manually**: 19,310 of 19,321 images were
tagged "good" by an operator. That is a real human decision, so it is preserved
as admin_verdict="accept" rather than discarded.

What is *not* preserved is any implication that those images passed a check.
The automated path never ran -- 14,607 rows carry the remark "PASS -- SKIPPED",
and 1,853 of the manually-accepted images carry a remark beginning "FAIL" from
an earlier density-ratio check. So safety_status stays "pending" and the quality
scores stay null: they have genuinely never been assessed, and the upgrade
re-evaluates them (internal design notes).

`remark` is untouched, so the FAIL measurements survive as forensic detail.
"""

from django.db import migrations


def convert_labels(apps, schema_editor):
    Image = apps.get_model("tirtha", "Image")
    # Manual operator decisions, preserved as such.
    Image.objects.filter(label="good").update(admin_verdict="accept")
    Image.objects.filter(label="bad").update(admin_verdict="reject")
    # A moderation outcome rather than a quality one.
    Image.objects.filter(label="nsfw").update(safety_status="rejected")
    # safety_status stays "pending" for everything else: never actually checked.


def restore_labels(apps, schema_editor):
    Image = apps.get_model("tirtha", "Image")
    Image.objects.filter(admin_verdict="accept").update(label="good")
    Image.objects.filter(admin_verdict="reject").update(label="bad")
    Image.objects.filter(safety_status="rejected").update(label="nsfw")


class Migration(migrations.Migration):
    dependencies = [("tirtha", "0014_image_evidence_fields")]
    operations = [migrations.RunPython(convert_labels, restore_labels)]
