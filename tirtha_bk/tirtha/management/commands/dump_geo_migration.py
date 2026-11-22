"""
Emit the confirmed coordinates as a data migration (internal design notes).

**This has to exist before the backfill session, not after.** The cutover
procedure (§23.1) takes a final production dump, restores it into the upgrade
environment and re-runs the migration chain -- so coordinates confirmed by hand
on beta are overwritten at the restore step. Beta is a copy, not a staging
area that gets promoted.

The fix is the pattern migration 0019 already uses: read what was confirmed,
write a migration that reapplies it by site ID, and let it replay at cutover
like every other change.

    python manage.py dump_geo_migration > tirtha/migrations/00XX_seed_geo.py

Guarded on the way in: each site is matched by ID, and the generated migration
skips any row whose coordinates have since been set by something else, so
replaying it cannot clobber later work.
"""

import json
from collections import Counter
from datetime import datetime, timezone

from django.core.management.base import BaseCommand

HEADER = '''"""
Reapply site coordinates across a restore (internal design notes).

**Generated** by `manage.py dump_geo_migration` on {generated}. Do not edit by
hand -- re-run the command instead.

Provenance of the {count} coordinates below: {provenance}. `location_source`
carries it per row, and the three values mean different things. `osm` is a match
against the Overpass mirror -- automatic when unambiguous, an operator's pick
from ranked candidates otherwise. `exif` is a median over the GPS cluster in the
site's own photographs, which internal design notes rates the better evidence: it is
first-party, with no name matching to get wrong. `manual` is a coordinate typed
in by hand and outranks both.

They live here because the cutover restores a fresh production dump and re-runs
the chain (§23.1), so anything entered directly into a database is lost at that
point.

Coordinates sourced from OpenStreetMap are **ODbL**; `location_source` records
that, and share-alike attaches to any dataset published containing them (§5).
"""

from django.db import migrations

# site_id -> the confirmed location
SEEDED = {seeded}


def apply_geo(apps, schema_editor):
    Site = apps.get_model("tirtha", "Site")
    for site_id, data in SEEDED.items():
        site = Site.objects.filter(ID=site_id).first()
        if site is None:
            # A site removed since the confirmations were made. Skipping is
            # correct; failing the migration over it is not.
            continue
        if site.latitude is not None and site.location_source not in ("", "osm"):
            # Something more authoritative than this seeding set the location
            # afterwards -- a manual correction, say. Do not clobber it.
            continue
        for field, value in data.items():
            setattr(site, field, value)
        site.save()


def unapply_geo(apps, schema_editor):
    """Clear only what this migration set, and only if it is still ours."""
    Site = apps.get_model("tirtha", "Site")
    for site_id in SEEDED:
        # Reverse only what this migration set. Matching on the recorded
        # source is what makes that true: a row someone has since corrected by
        # hand reads `manual` and is left alone.
        Site.objects.filter(
            ID=site_id, location_source=SEEDED[site_id].get("location_source", "osm")
        ).update(
            latitude=None,
            longitude=None,
            osm_type="",
            osm_id=None,
            osm_version=None,
            location_source="",
            location_verified_at=None,
        )


class Migration(migrations.Migration):
    dependencies = [("tirtha", "{depends_on}")]
    operations = [migrations.RunPython(apply_geo, unapply_geo)]
'''

FIELDS = (
    "latitude",
    "longitude",
    "osm_type",
    "osm_id",
    "osm_version",
    "wikidata_qid",
    "location_source",
    "location_verified_at",
)


class Command(BaseCommand):
    help = "Emit confirmed site coordinates as a replayable data migration."

    def add_arguments(self, parser):
        parser.add_argument(
            "--depends-on",
            default="0022_derive_run_camera",
            help="The migration this one should follow.",
        )
        parser.add_argument(
            "--source",
            default="",
            help=(
                "Only dump sites whose location_source matches. "
                "Default: every source, which is almost always what is wanted -- "
                "this defaulted to 'osm' and silently dropped every "
                "EXIF-derived coordinate, two thirds of them on the 2026-08-29 "
                "data."
            ),
        )

    def handle(self, *args, **options):
        from tirtha.models import Site

        queryset = Site.objects.exclude(latitude__isnull=True)
        if options["source"]:
            queryset = queryset.filter(location_source=options["source"])

        seeded = {}
        for site in queryset.order_by("name"):
            row = {}
            for field in FIELDS:
                value = getattr(site, field)
                if value in (None, ""):
                    continue
                row[field] = value.isoformat() if hasattr(value, "isoformat") else value
            seeded[site.ID] = row

        if not seeded:
            self.stderr.write(
                "No confirmed coordinates found. Seed them in the Site admin "
                "first (Find on OpenStreetMap)."
            )
            return

        # Describe what was actually dumped rather than asserting a
        # provenance. The first version of this header called every row a human
        # decision; two thirds of them were derived from photograph EXIF, and a
        # migration that misdescribes its own data is worse than one with no
        # docstring at all.
        counts = Counter(
            row.get("location_source", "unknown") for row in seeded.values()
        )
        provenance = ", ".join(f"{n} {src}" for src, n in sorted(counts.items()))

        self.stdout.write(
            HEADER.format(
                generated=datetime.now(timezone.utc).date().isoformat(),
                seeded=json.dumps(seeded, indent=4, default=str),
                depends_on=options["depends_on"],
                count=len(seeded),
                provenance=provenance,
            )
        )
        self.stderr.write(f"{len(seeded)} site(s) dumped.")
