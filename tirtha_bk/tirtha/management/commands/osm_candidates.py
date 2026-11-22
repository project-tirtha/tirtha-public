"""
Dry-run report of what Overpass would offer for each site (internal design notes).

Writes nothing, ever. Seeding is a human decision made in the admin; this exists
so the hit rate is knowable *before* anyone starts clicking, and so a bad match
pattern (a district name OSM does not use, a site whose name only exists in
Odia) shows up as a report rather than as sixty confusing confirmation pages.

    python manage.py osm_candidates                 # sites with no coordinate
    python manage.py osm_candidates --all
    python manage.py osm_candidates --json > candidates.json
"""

import json

from django.core.management.base import BaseCommand

from tirtha.models import Site
from tirtha.overpass import OverpassClient, OverpassUnavailable, _phonetic_key

# An exact name match, alone in its district, with nothing else scoring close.
# Reported as "clear" only to tell the operator where the easy confirmations
# are -- the command still writes nothing.
CLEAR_MARGIN = 30


class Command(BaseCommand):
    help = "Report Overpass candidates per site without writing anything."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Include sites that already have a coordinate.",
        )
        parser.add_argument(
            "--top", type=int, default=3, help="Candidates to show per site."
        )
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        client = OverpassClient()
        if not client.configured:
            self.stderr.write(
                "OVERPASS_URL is not set. Overpass is optional -- see "
                "build/setup-overpass.sh."
            )
            return

        sites = Site.objects.all()
        if not options["all"]:
            sites = sites.filter(latitude__isnull=True)

        rows, tally = [], {"clear": 0, "ambiguous": 0, "none": 0}
        for site in sites.order_by("state", "district", "name"):
            try:
                candidates = client.find_site(site)
            except OverpassUnavailable as exc:
                self.stderr.write(f"{site.name}: {exc}")
                break

            verdict = self._verdict(site, candidates)
            tally[verdict] += 1
            rows.append(
                {
                    "site_id": str(site.pk),
                    "name": site.name,
                    "district": site.district,
                    "state": site.state,
                    "verdict": verdict,
                    "candidates": [
                        {
                            "osm": f"{c.osm_type}/{c.osm_id}",
                            "scope": c.scope,
                            "name": c.name,
                            "score": c.score,
                            "version": c.version,
                            "lat": c.latitude,
                            "lon": c.longitude,
                            "tags": c.summary,
                            "wikidata": c.wikidata_qid,
                            "url": c.osm_url,
                        }
                        for c in candidates[: options["top"]]
                    ],
                }
            )

        if options["json"]:
            self.stdout.write(json.dumps(rows, indent=2))
            return

        for row in rows:
            head = f"{row['name']}  ({row['district']}, {row['state']})"
            style = {
                "clear": self.style.SUCCESS,
                "ambiguous": self.style.WARNING,
                "none": self.style.ERROR,
            }[row["verdict"]]
            self.stdout.write(style(f"[{row['verdict']:9}] ") + head)
            for c in row["candidates"]:
                self.stdout.write(
                    f"      {c['score']:6.1f}  {c['scope']:8} {c['osm']:20} "
                    f"{c['name'][:40]:40} {c['tags'][:30]}"
                )
            if not row["candidates"]:
                self.stdout.write("      (nothing found)")

        self.stdout.write(
            f"\n{len(rows)} site(s): {tally['clear']} clear, "
            f"{tally['ambiguous']} need a judgement call, {tally['none']} no match."
        )
        self.stdout.write(
            "Nothing was written. Confirm in the Site admin -- select sites and "
            "run 'Find on OpenStreetMap'."
        )

    @staticmethod
    def _verdict(site, candidates):
        """
        Compare the way the matcher compares.

        Judging "clear" on raw string equality contradicted the whole point of
        the phonetic matcher: `Brahmeswara Temple` against OSM's `Brahmeswar
        Temple` was the only candidate returned, scored 103, and still read as
        a judgement call because the strings differ by one letter.
        """
        if not candidates:
            return "none"
        top = candidates[0]
        runner_up = candidates[1].score if len(candidates) > 1 else 0
        same_name = _phonetic_key(top.name) == _phonetic_key(site.name)
        # `scope` must be part of this, or the report stops being a faithful
        # preview of what `backfill_geo` writes: a state-scoped hit scores just
        # as well on the name and carries no locality evidence at all
        # (internal design notes).
        in_district = top.scope == "district"
        if same_name and in_district and top.score - runner_up >= CLEAR_MARGIN:
            return "clear"
        return "ambiguous"
