"""
Re-resolve stored OSM refs and report drift (internal design notes).

Never relocates a marker. A stored coordinate is a decision someone made; an
upstream retag is a reason to *look*, not a reason to move a site on the public
map without anyone noticing. Output is a report for a human.

    python manage.py check_osm_drift
    python manage.py check_osm_drift --site <ID>
    python manage.py check_osm_drift --json
"""

import json

from django.core.management.base import BaseCommand

from tirtha.models import Site
from tirtha.overpass import OverpassClient, OverpassUnavailable

# Roughly 11 m at the equator. Below this, a coordinate change is a mapper
# nudging a node, not the site moving -- reporting those would bury the cases
# that matter.
COORD_EPSILON = 1e-4


class Command(BaseCommand):
    help = "Re-resolve stored OSM references and flag drift for review."

    def add_arguments(self, parser):
        parser.add_argument("--site", help="Limit to one Site ID.")
        parser.add_argument(
            "--json", action="store_true", help="Machine-readable output."
        )

    def handle(self, *args, **options):
        client = OverpassClient()
        if not client.configured:
            self.stderr.write(
                "OVERPASS_URL is not set. Overpass is optional -- see "
                "build/setup-overpass.sh."
            )
            return

        sites = Site.objects.filter(osm_id__isnull=False).exclude(osm_type="")
        if options["site"]:
            sites = sites.filter(ID=options["site"])

        findings = []
        for site in sites.order_by("name"):
            try:
                element = client.fetch_element(site.osm_type, site.osm_id)
            except OverpassUnavailable as exc:
                self.stderr.write(f"{site.name}: {exc}")
                break

            if element is None:
                # A deleted or replaced element. The stored coordinate is still
                # fine to render -- it just no longer has an upstream referent,
                # so the link needs re-establishing by hand.
                findings.append(
                    self._finding(site, "gone", "element no longer exists upstream")
                )
                continue

            if site.osm_version is None:
                findings.append(
                    self._finding(
                        site,
                        "unversioned",
                        f"no stored version to compare (mirror now says "
                        f"v{element.version}); re-seed to enable drift detection",
                    )
                )
            elif element.version and element.version > site.osm_version:
                moved = (
                    abs(element.latitude - (site.latitude or 0)) > COORD_EPSILON
                    or abs(element.longitude - (site.longitude or 0)) > COORD_EPSILON
                )
                findings.append(
                    self._finding(
                        site,
                        "moved" if moved else "retagged",
                        f"v{site.osm_version} -> v{element.version}"
                        + (
                            f"; upstream now at {element.latitude:.5f}, "
                            f"{element.longitude:.5f}"
                            if moved
                            else "; coordinate unchanged"
                        ),
                    )
                )

        if options["json"]:
            self.stdout.write(json.dumps(findings, indent=2))
            return

        if not findings:
            self.stdout.write(
                self.style.SUCCESS(f"No drift across {sites.count()} located site(s).")
            )
            return

        self.stdout.write(f"{len(findings)} site(s) need review:\n")
        for f in findings:
            style = self.style.WARNING if f["kind"] == "retagged" else self.style.ERROR
            self.stdout.write(
                style(f"  [{f['kind']:12}] {f['name']}") + f" -- {f['detail']}"
            )
        self.stdout.write(
            "\nNothing was changed. Review each and re-seed from the Site admin "
            "if the upstream element is still the right one."
        )

    @staticmethod
    def _finding(site, kind, detail):
        return {
            "site_id": str(site.pk),
            "name": site.name,
            "osm": f"{site.osm_type}/{site.osm_id}",
            "kind": kind,
            "detail": detail,
        }
