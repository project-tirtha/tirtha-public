"""
Rename Mesh -> Site, and the FK field mesh -> site (internal design notes).

Written by hand rather than generated. `makemigrations` did not recognise this
as a rename: it began asking for a default for a new non-null field, i.e. it was
about to emit CreateModel + DeleteModel, which would drop the table and every
row in it. RenameModel and RenameField preserve the data and, in PostgreSQL,
compile to ALTER TABLE ... RENAME rather than a copy.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tirtha", "0008_mesh_description_en_mesh_description_hi_and_more"),
    ]

    operations = [
        migrations.RenameModel(old_name="Mesh", new_name="Site"),
        # Django rewrites the related descriptors, but the concrete column on
        # each child table still has to be renamed explicitly.
        migrations.RenameField(
            model_name="contribution", old_name="mesh", new_name="site"
        ),
        migrations.RenameField(model_name="run", old_name="mesh", new_name="site"),
        migrations.AlterModelOptions(
            name="site",
            options={"ordering": ["-updated_at"], "verbose_name_plural": "Sites"},
        ),
    ]
