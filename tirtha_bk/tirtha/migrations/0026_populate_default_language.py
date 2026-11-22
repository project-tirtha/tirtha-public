"""
Copy the original columns into the default-language columns (internal design notes).

**Without this, every site name reads empty after a production restore.**
Migration 0008 added `name_en` / `name_or` / `name_hi` but copied nothing into
them, and modeltranslation makes `Site.name` a descriptor that reads the active
language's column. So `site.name` returns `""`, `filter(name__icontains=...)`
matches nothing, and the site list, search and every unfurl title come up blank.

It did not show up on SQLite because that beta was seeded with `loaddata`,
which writes through the ORM -- and the descriptor populates `name_en` on save.
**`pg_restore` bypasses the ORM entirely**, which is exactly what cutover does
(§23.1 step 2). The bug was therefore invisible until the chain was rehearsed
on PostgreSQL against a real dump.

Idempotent, and never overwrites an existing translation: only rows whose
default-language column is null or empty are touched.

> **Coupled to `translation.py` by hand.** A field registered there later also
> needs adding here, or its legacy rows will read empty the same way.
> `manage.py update_translation_fields` does the same job, but a management
> command can be forgotten at cutover; a migration cannot.
"""

from django.db import migrations

# (model, [fields]) -- must mirror tirtha/translation.py.
TRANSLATED = [
    ("Site", ["name", "description"]),
    ("Category", ["name", "description"]),
]

DEFAULT_LANGUAGE = "en"


def populate(apps, schema_editor):
    for model_name, fields in TRANSLATED:
        model = apps.get_model("tirtha", model_name)
        updates = []
        columns = [f"{field}_{DEFAULT_LANGUAGE}" for field in fields]
        for row in model.objects.all().iterator(chunk_size=500):
            touched = False
            for field, column in zip(fields, columns):
                if not getattr(row, column, None) and getattr(row, field, None):
                    setattr(row, column, getattr(row, field))
                    touched = True
            if touched:
                updates.append(row)
        if updates:
            model.objects.bulk_update(updates, columns, batch_size=500)


def clear(apps, schema_editor):
    """
    Reversible: the original columns still hold the same values, so clearing
    the English copies loses nothing.
    """
    for model_name, fields in TRANSLATED:
        model = apps.get_model("tirtha", model_name)
        model.objects.update(
            **{f"{field}_{DEFAULT_LANGUAGE}": None for field in fields}
        )


class Migration(migrations.Migration):
    dependencies = [("tirtha", "0025_leaderboard")]
    operations = [migrations.RunPython(populate, clear)]
