"""
Give sites a coordinate, from the evidence we already hold (internal design notes).

Two sources, in order of how much they are worth trusting.

**EXIF first.** The strongest evidence of where a site is, is where the people
who photographed it were standing. `backfill_exif` already read GPS out of the
archive; this reduces those points to one coordinate per site. It beats OSM
because it is first-party -- no name matching, no transliteration, nothing to
mismatch -- and because it is ours rather than ODbL.

**Overpass second**, for sites nobody's camera recorded a fix for. Only matches
the `osm_candidates` report calls *clear* are written by default: an exact
phonetic name match, **found inside the site's own district**, well clear of the
runner-up. A site whose name is ambiguous stays uncoordinated rather than being
pinned somewhere plausible and wrong.

The district condition is not decoration. `find_site` widens to a state-wide
search when the district turns up nothing, and a state-wide hit scores exactly
as well on the name while having no locality evidence behind it at all -- the
first run of this command put Manokamna Temple, whose record says Darbhanga, on
a same-named temple near Gaya, three hundred kilometres away. Those now need
`--accept-ambiguous` and a human reading the output.

**A district centroid is not a coordinate.** A marker in the middle of Khordha
for a temple in Bhubaneswar reads to a visitor exactly like a real location and
is simply false, so this command would rather leave the field null. "As many as
possible" means as many as the evidence supports.

Re-runnable. Skips sites that already have a coordinate unless `--refresh` is
given, and never overwrites a `manual` one -- an operator who typed a
coordinate in the admin outranks both of these sources.

    python manage.py backfill_geo --dry-run
    python manage.py backfill_geo
    python manage.py backfill_geo --exif-only
    python manage.py backfill_geo --accept-ambiguous   # lower the OSM bar
"""

from statistics import median

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from tirtha.models import Image, Site
from tirtha.overpass import OverpassClient, OverpassUnavailable, _phonetic_key

# Same bar as the `osm_candidates` report, so "clear" means one thing in this
# codebase and the report is a faithful preview of what this writes.
CLEAR_MARGIN = 30

# How far from the median a photo may be and still count towards it. Roughly
# 500 m at Indian latitudes -- generous enough for a temple complex photographed
# from every side and its car park, tight enough that one image tagged in the
# contributor's home city cannot drag the answer.
CLUSTER_DEGREES = 0.005

# One stray fix is not a location. Two agreeing photographs is the smallest
# thing worth calling evidence.
MIN_FIXES = 2

# Anything nearer than this to (0, 0) is a camera that wrote a null island fix
# rather than a site in the Gulf of Guinea.
NULL_ISLAND = 0.01


class Command(BaseCommand):
    help = "Fill Site.latitude/longitude from image EXIF, then from OpenStreetMap."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--refresh",
            action="store_true",
            help="Reconsider sites that already have a non-manual coordinate.",
        )
        parser.add_argument("--exif-only", action="store_true")
        parser.add_argument("--osm-only", action="store_true")
        parser.add_argument(
            "--accept-ambiguous",
            action="store_true",
            help="Also write the top OSM match when it is not a clear winner.",
        )
        parser.add_argument("--site", help="Restrict to one site ID.")
        parser.add_argument(
            "--drop-unsupported",
            action="store_true",
            help=(
                "With --refresh: clear a derived coordinate the current rules "
                "would no longer write. Never touches a manual one."
            ),
        )

    def handle(self, *args, **opts):
        sites = Site.objects.all()
        if opts["site"]:
            sites = sites.filter(pk=opts["site"])
        if not opts["refresh"]:
            sites = sites.filter(latitude__isnull=True)
        else:
            # A coordinate someone typed by hand is a decision, not a guess.
            sites = sites.exclude(location_source="manual")

        sites = list(sites.order_by("state", "district", "name"))
        if not sites:
            self.stdout.write("Every site already has a coordinate.")
            return

        exif_points = {} if opts["osm_only"] else self._exif_points(sites)
        client = None
        if not opts["exif_only"]:
            client = OverpassClient()
            if not client.configured:
                self.stderr.write(
                    "OVERPASS_URL is not set -- EXIF only. See build/setup-overpass.sh."
                )
                client = None

        tally = {"exif": 0, "osm": 0, "none": 0}
        for site in sites:
            resolved = exif_points.get(site.pk)
            if resolved:
                self._apply(site, *resolved, opts)
                tally["exif"] += 1
                continue

            if client is None:
                tally["none"] += 1
                self.stdout.write(f"  --   {site.name}: no EXIF fix")
                continue

            try:
                candidates = client.find_site(site)
            except OverpassUnavailable as exc:
                self.stderr.write(f"{site.name}: {exc}")
                break

            picked = self._pick(site, candidates, opts["accept_ambiguous"])
            if picked is None:
                tally["none"] += 1
                note = ""
                if site.latitude is not None:
                    # It already has a coordinate this run would not have
                    # written. Say so loudly: silence would read as "checked,
                    # still fine" when it means the opposite.
                    note = " -- EXISTING COORDINATE IS NOT SUPPORTED BY EVIDENCE"
                    if opts["drop_unsupported"] and not opts["dry_run"]:
                        self._clear(site)
                        note += " (cleared)"
                self.stdout.write(f"  --   {site.name}: no confident match{note}")
                continue

            self._apply(site, picked.latitude, picked.longitude, opts, candidate=picked)
            tally["osm"] += 1

        verb = "would set" if opts["dry_run"] else "set"
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{verb} {tally['exif']} from EXIF and {tally['osm']} from OSM; "
                f"{tally['none']} still have no coordinate."
            )
        )
        if opts["dry_run"]:
            self.stdout.write("Dry run -- nothing was written.")

    def _exif_points(self, sites):
        """
        One coordinate per site, from the GPS fixes on its images.

        Median rather than mean, and clustered rather than averaged outright: a
        mean is dragged by a single wrong fix, and these archives do contain
        images tagged where the contributor edited them rather than where they
        shot them.
        """
        wanted = {s.pk for s in sites}
        rows = Image.objects.filter(
            contribution__site__in=wanted,
            gps_latitude__isnull=False,
            gps_longitude__isnull=False,
        ).values_list("contribution__site", "gps_latitude", "gps_longitude")

        by_site = {}
        for site_id, lat, lon in rows:
            if abs(lat) < NULL_ISLAND and abs(lon) < NULL_ISLAND:
                continue
            if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                continue
            by_site.setdefault(site_id, []).append((lat, lon))

        points = {}
        for site_id, fixes in by_site.items():
            lat0, lon0 = median(f[0] for f in fixes), median(f[1] for f in fixes)
            kept = [
                f
                for f in fixes
                if abs(f[0] - lat0) <= CLUSTER_DEGREES
                and abs(f[1] - lon0) <= CLUSTER_DEGREES
            ]
            if len(kept) < MIN_FIXES:
                continue
            points[site_id] = (
                round(median(f[0] for f in kept), 7),
                round(median(f[1] for f in kept), 7),
            )
        return points

    @staticmethod
    def _pick(site, candidates, accept_ambiguous):
        """The same judgement `osm_candidates` reports, applied rather than printed."""
        if not candidates:
            return None
        top = candidates[0]
        runner_up = candidates[1].score if len(candidates) > 1 else 0
        clear = (
            _phonetic_key(top.name) == _phonetic_key(site.name)
            and top.score - runner_up >= CLEAR_MARGIN
            # Locality evidence, not just name evidence. See the module
            # docstring: a state-scoped hit has none.
            and top.scope == "district"
        )
        if clear or accept_ambiguous:
            return top
        return None

    @staticmethod
    def _clear(site):
        """Drop a derived coordinate the evidence no longer supports."""
        site.latitude = site.longitude = None
        site.location_source = ""
        site.location_verified_at = None
        site.save(
            update_fields=[
                "latitude",
                "longitude",
                "location_source",
                "location_verified_at",
            ]
        )

    def _apply(self, site, lat, lon, opts, candidate=None):
        source = "osm" if candidate else "exif"
        label = f"{lat:9.5f}, {lon:9.5f}"
        detail = f" {candidate.scope:8} {candidate.osm_url}" if candidate else ""
        self.stdout.write(f"  {source:4} {site.name}: {label}{detail}")
        if opts["dry_run"]:
            return

        site.latitude, site.longitude = lat, lon
        site.location_source = source
        site.location_verified_at = timezone.now()
        fields = [
            "latitude",
            "longitude",
            "location_source",
            "location_verified_at",
        ]
        if candidate:
            # Keep the cross-walk with the coordinate. Without osm_id there is
            # nothing for `check_osm_drift` to compare against later.
            site.osm_type, site.osm_id = candidate.osm_type, candidate.osm_id
            site.osm_version = candidate.version
            site.osm_tags = candidate.tags
            fields += ["osm_type", "osm_id", "osm_version", "osm_tags"]
            if candidate.wikidata_qid and not site.wikidata_qid:
                site.wikidata_qid = candidate.wikidata_qid
                fields.append("wikidata_qid")
        with transaction.atomic():
            site.save(update_fields=fields)
