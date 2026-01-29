"""
Django management command to generate database statistics for Project Tirtha.

Usage: `python manage.py generate_stats [--output-dir /path/to/dir]`

The command writes a JSON file named `tirtha_stats-<timestamp>.json` to the
configured output directory (or `/tmp/tirtha_stats` by default) and prints a
short summary to stdout.

"""

from __future__ import annotations

import json
import os
import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from django.db.models import Count, Avg, F, DurationField, ExpressionWrapper
from django.db.models.functions import TruncMonth
from django.utils import timezone

# Local imports
from tirtha.models import Mesh, Contributor, Contribution, Image, Run, ARK


def get_basic_counts():
    return {
        "meshes": Mesh.objects.count(),
        "contributors": Contributor.objects.count(),
        "contributions": Contribution.objects.count(),
        "images": Image.objects.count(),
        "runs": Run.objects.count(),
        "arks": ARK.objects.count(),
    }


def get_mesh_summary():
    total = Mesh.objects.count()
    completed = Mesh.objects.filter(completed=True).count()
    hidden = Mesh.objects.filter(hidden=True).count()
    by_country = list(
        Mesh.objects.values("country").annotate(count=Count("pk")).order_by("-count")
    )

    return {
        "total_meshes": total,
        "completed_meshes": completed,
        "hidden_meshes": hidden,
        "meshes_by_country": by_country,
    }


def get_runs_stats():
    total = Run.objects.count()
    by_status = list(
        Run.objects.values("status").annotate(count=Count("pk")).order_by("-count")
    )

    durations = (
        Run.objects.filter(ended_at__isnull=False)
        .annotate(
            duration=ExpressionWrapper(
                F("ended_at") - F("started_at"), output_field=DurationField()
            )
        )
        .values_list("duration", flat=True)
    )
    durations = [d.total_seconds() for d in durations if d is not None]
    avg_duration = sum(durations) / len(durations) if durations else None

    success = Run.objects.exclude(status__in=["Error", "Cancelled"]).count()

    return {
        "total_runs": total,
        "by_status": by_status,
        "avg_run_duration_seconds": avg_duration,
        "success_count": success,
        "success_percentage": (success / total * 100) if total else None,
    }


def get_image_stats():
    images_per_mesh = list(
        Mesh.objects.annotate(image_count=Count("contributions__images"))
        .values("ID", "name", "image_count")
        .order_by("-image_count")
    )
    img_label_dist = list(
        Image.objects.values("label").annotate(count=Count("pk")).order_by("-count")
    )

    return {
        "images_per_mesh_top": images_per_mesh,
        "image_label_distribution": img_label_dist,
        "total_images": Image.objects.count(),
    }


def get_contributor_stats(limit=50):
    top_contribs = list(
        Contributor.objects.annotate(image_count=Count("contributions__images"))
        .values("ID", "name", "email", "image_count")
        .order_by("-image_count")[:limit]
    )
    avg_contribs = Contributor.objects.annotate(cnt=Count("contributions")).aggregate(
        avg=Avg("cnt")
    )

    return {
        "top_contributors": top_contribs,
        "avg_contributions_per_contributor": avg_contribs.get("avg"),
    }


def export_all_stats():
    stats = {}
    stats.update(get_basic_counts())
    stats["mesh_summary"] = get_mesh_summary()
    stats["runs"] = get_runs_stats()
    stats["images"] = get_image_stats()
    stats["contributors"] = get_contributor_stats()

    # Time series: meshes and contributions by month (last 36 months)
    now = timezone.now()
    cutoff = now - timezone.timedelta(days=365 * 3)

    meshes_by_month = (
        Mesh.objects.filter(created_at__gte=cutoff)
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(count=Count("pk"))
        .order_by("month")
    )
    contributions_by_month = (
        Contribution.objects.filter(contributed_at__gte=cutoff)
        .annotate(month=TruncMonth("contributed_at"))
        .values("month")
        .annotate(count=Count("pk"))
        .order_by("month")
    )

    stats["meshes_by_month"] = list(meshes_by_month)
    stats["contributions_by_month"] = list(contributions_by_month)

    return stats


class Command(BaseCommand):
    help = "Generate DB statistics for Project Tirtha and write JSON output."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            dest="output_dir",
            help="Directory to write JSON output to (overrides settings.STATS_OUTPUT_DIR)",
            default=None,
        )

    def handle(self, *args, **options):
        output_dir = options.get("output_dir") or getattr(
            settings, "STATS_OUTPUT_DIR", None
        )
        if not output_dir:
            output_dir = "/tmp/tirtha_stats"

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"tirtha_stats-{now}.json"
        outpath = os.path.join(output_dir, filename)

        self.stdout.write("Computing statistics...")
        stats = export_all_stats()

        with open(outpath, "w", encoding="utf-8") as fh:
            json.dump(stats, fh, indent=2, default=str)

        self.stdout.write(self.style.SUCCESS(f"Statistics written to: {outpath}"))

        # Print a short human summary
        basic = stats.get("meshes"), stats.get("contributors"), stats.get("images")
        self.stdout.write(
            f"Meshes: {basic[0]} | Contributors: {basic[1]} | Images: {basic[2]}"
        )
