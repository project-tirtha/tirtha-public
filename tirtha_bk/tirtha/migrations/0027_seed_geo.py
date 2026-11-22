"""
Reapply site coordinates across a restore (internal design notes).

**Generated** by `manage.py dump_geo_migration` on 2026-09-01. Do not edit by
hand -- re-run the command instead.

Provenance of the 30 coordinates below: 20 exif, 10 osm. `location_source`
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
SEEDED = {
    "iuNb3Vnr8ZCKx8cC": {
        "latitude": 20.2445167,
        "longitude": 85.8528222,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:13.864905+00:00"
    },
    "AWwJTjoGPPnYQN8W": {
        "latitude": 23.2404944,
        "longitude": 87.8675167,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:46.985549+00:00"
    },
    "pvkj6ug9oA5RmsER": {
        "latitude": 20.2397687,
        "longitude": 85.8516219,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:13.930400+00:00"
    },
    "5MBW8NqsY6icRF4L": {
        "latitude": 20.2392743,
        "longitude": 85.8336098,
        "osm_type": "way",
        "osm_id": 489291799,
        "osm_version": 2,
        "wikidata_qid": "Q60400566",
        "location_source": "osm",
        "location_verified_at": "2026-09-01T06:19:14.940047+00:00"
    },
    "ezxJcqYxwwVbeQzC": {
        "latitude": 20.1417843,
        "longitude": 85.7615443,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:16.932717+00:00"
    },
    "qovMSNwwBKG3Ui2z": {
        "latitude": 19.928375,
        "longitude": 85.8276667,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:16.994679+00:00"
    },
    "icbxoSavkiDKNmyM": {
        "latitude": 15.338695,
        "longitude": 76.472115,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:18:48.608747+00:00"
    },
    "Wsqs4yrQ7eQ7xriy": {
        "latitude": 20.1727069,
        "longitude": 85.7878806,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:17.054477+00:00"
    },
    "hH5st4EossKwdg8Q": {
        "latitude": 15.4389972,
        "longitude": 74.2525819,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:18:47.200021+00:00"
    },
    "kehKA6PgySnckqMP": {
        "latitude": 8.510945,
        "longitude": 76.958064,
        "osm_type": "way",
        "osm_id": 383977076,
        "osm_version": 5,
        "location_source": "osm",
        "location_verified_at": "2026-09-01T06:18:50.507977+00:00"
    },
    "p4z6xZLyR5KrWCLc": {
        "latitude": 19.887564,
        "longitude": 86.0944894,
        "osm_type": "way",
        "osm_id": 168244384,
        "osm_version": 4,
        "wikidata_qid": "Q752091",
        "location_source": "osm",
        "location_verified_at": "2026-09-01T06:19:30.134064+00:00"
    },
    "hHqeWBhb92BDfJfo": {
        "latitude": 16.6158225,
        "longitude": 74.6855967,
        "osm_type": "way",
        "osm_id": 1173158431,
        "osm_version": 2,
        "location_source": "osm",
        "location_verified_at": "2026-09-01T06:18:53.674968+00:00"
    },
    "7suMqZosrMHCRXNs": {
        "latitude": 20.2533107,
        "longitude": 85.836431,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:17.102641+00:00"
    },
    "8r3G7ntuZpPk7kyD": {
        "latitude": 20.1956319,
        "longitude": 85.8593667,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:17.169951+00:00"
    },
    "9zpT9kVZwP9XxAbG": {
        "latitude": 20.171346,
        "longitude": 85.6823434,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:17.225327+00:00"
    },
    "JmNdcT6J3UFAxsNr": {
        "latitude": 20.2432795,
        "longitude": 85.8563427,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:17.231492+00:00"
    },
    "fd9dHggUKjqnHx5X": {
        "latitude": 18.6264444,
        "longitude": 73.77855,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:18:57.979068+00:00"
    },
    "E2ia7CzGz4FCB7dv": {
        "latitude": 20.2426972,
        "longitude": 85.8404611,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:17.301621+00:00"
    },
    "XUbRmpnE3iGzkVAG": {
        "latitude": 30.5563663,
        "longitude": 79.5658419,
        "osm_type": "way",
        "osm_id": 1323770204,
        "osm_version": 1,
        "location_source": "osm",
        "location_verified_at": "2026-09-01T06:19:44.732956+00:00"
    },
    "jhe5N87DRiBidvPM": {
        "latitude": 20.2431506,
        "longitude": 85.8390991,
        "osm_type": "node",
        "osm_id": 7403431290,
        "osm_version": 2,
        "wikidata_qid": "Q7140073",
        "location_source": "osm",
        "location_verified_at": "2026-09-01T06:19:19.273856+00:00"
    },
    "3M4Fmytaq8atZkmS": {
        "latitude": 18.4975989,
        "longitude": 73.8470689,
        "osm_type": "way",
        "osm_id": 264282504,
        "osm_version": 3,
        "location_source": "osm",
        "location_verified_at": "2026-09-01T06:18:58.992736+00:00"
    },
    "eBvdtPPywdVnrHsY": {
        "latitude": 20.2434676,
        "longitude": 85.8435445,
        "osm_type": "way",
        "osm_id": 1377168360,
        "osm_version": 1,
        "wikidata_qid": "Q6507638",
        "location_source": "osm",
        "location_verified_at": "2026-09-01T06:19:20.390011+00:00"
    },
    "hyKFQn9fDbMgstF2": {
        "latitude": 18.0749486,
        "longitude": 83.3696142,
        "osm_type": "way",
        "osm_id": 879376594,
        "osm_version": 1,
        "wikidata_qid": "Q25572681",
        "location_source": "osm",
        "location_verified_at": "2026-09-01T06:18:43.368213+00:00"
    },
    "PznuiTBubAoV3sAg": {
        "latitude": 23.2383056,
        "longitude": 87.8570653,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:47.083960+00:00"
    },
    "n6yCTQ5m2JxCjbqR": {
        "latitude": 22.2083893,
        "longitude": 88.1835556,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:44.810889+00:00"
    },
    "brrhEN43iweXQy9w": {
        "latitude": 18.4916295,
        "longitude": 73.8972726,
        "osm_type": "way",
        "osm_id": 53350109,
        "osm_version": 5,
        "wikidata_qid": "Q7497295",
        "location_source": "osm",
        "location_verified_at": "2026-09-01T06:19:00.932828+00:00"
    },
    "4fdxHbRSAHMxKgSz": {
        "latitude": 18.7380472,
        "longitude": 73.67295,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:00.992454+00:00"
    },
    "3CaNLnvF2Ej5hZXX": {
        "latitude": 17.6489826,
        "longitude": 75.8993903,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:09.632121+00:00"
    },
    "EpuSEUJTNJGSGcxz": {
        "latitude": 20.1446389,
        "longitude": 85.752822,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:22.453723+00:00"
    },
    "kMqVTfubfxiV4geZ": {
        "latitude": 22.5493139,
        "longitude": 88.3328528,
        "location_source": "exif",
        "location_verified_at": "2026-09-01T06:19:44.885062+00:00"
    }
}


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
    dependencies = [("tirtha", "0026_populate_default_language")]
    operations = [migrations.RunPython(apply_geo, unapply_geo)]
