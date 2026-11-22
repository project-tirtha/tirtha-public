"""
This is intended to be used as a CLI tool
to get the verbose ID of a model (site), given its ID.

"""

import os

import django

# Setup Django pre-run to access the DB layer
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tirtha_bk.settings")
django.setup()

from tirtha.models import Site


def _get_site_details(siteID: str) -> dict:
    try:
        site = Site.objects.get(ID=siteID)
        return {
            "ID": site.ID,
            "verbose_id": site.verbose_id,
            "status": site.status,
            "completed": site.completed,
            "updated_at": site.updated_at.astimezone().strftime(
                "%d %b %Y, %H:%M:%S %Z"
            ),
            "reconstructed_at": site.reconstructed_at.astimezone().strftime(
                "%d %b %Y, %H:%M:%S %Z"
            )
            if site.reconstructed_at
            else None,
        }
    except Site.DoesNotExist:
        return "Corresponding site not found."


if __name__ == "__main__":
    import argparse

    from rich.console import Console
    from rich.table import Table

    parser = argparse.ArgumentParser()
    parser.add_argument("siteID", help="Site ID")
    args = parser.parse_args()

    cons = Console()
    cons.rule("Site Details")

    dct = _get_site_details(args.siteID)

    tab = Table(show_header=False)
    for key, value in dct.items():
        tab.add_row(key, str(value))

    cons.print(tab)
    cons.rule()
