"""
Tests for model save-time behaviour.

These cover the derived values and invariants the pipeline and the ARK
commitment depend on -- the parts that would fail silently or corrupt
identifiers if they regressed.
"""

from django.test import TestCase

from tirtha.models import ARK, Run, Site


def _site(**kwargs):
    defaults = {
        "name": "Mukteswar Temple",
        "country": "India",
        "state": "Odisha",
        "district": "Khordha",
    }
    defaults.update(kwargs)
    return Site.objects.create(**defaults)


class MeshVerboseIDTests(TestCase):
    def test_built_from_location_and_name(self):
        site = _site()
        self.assertEqual(site.verbose_id, "India__Odisha__Khordha__Mukteswar_Temple")

    def test_spaces_become_underscores_in_every_component(self):
        site = _site(
            name="Stone Chariot", state="Tamil Nadu", district="Sri Sathya Sai"
        )
        self.assertNotIn(" ", site.verbose_id)
        self.assertEqual(
            site.verbose_id, "India__Tamil_Nadu__Sri_Sathya_Sai__Stone_Chariot"
        )

    def test_recomputed_on_rename(self):
        # internal design notes: verbose_id is derived, which is exactly why it must not
        # be used as a routing key.
        site = _site()
        site.name = "Mukteswar"
        site.save()
        self.assertEqual(site.verbose_id, "India__Odisha__Khordha__Mukteswar")

    def test_same_name_in_different_districts_stays_distinct(self):
        # The two real "Gopinath Temple" rows (internal design notes) differ only by district.
        a = _site(name="Gopinath Temple", district="Khordha")
        b = _site(name="Gopinath Temple", district="Puri")
        self.assertNotEqual(a.verbose_id, b.verbose_id)


class RunDirectoryTests(TestCase):
    def test_directory_encodes_site_kind_and_id(self):
        site = _site()
        run = Run.objects.create(site=site, kind="GS")
        self.assertTrue(run.directory.startswith(f"{site.ID}/gscache/"))
        self.assertTrue(run.directory.endswith(f"__{run.ID}"))

    def test_kind_is_lowercased_into_the_cache_dir(self):
        site = _site()
        self.assertIn("/avcache/", Run.objects.create(site=site, kind="aV").directory)
        self.assertIn("/gscache/", Run.objects.create(site=site, kind="GS").directory)

    def test_existing_directory_is_not_overwritten(self):
        site = _site()
        run = Run.objects.create(site=site, kind="GS")
        original = run.directory
        run.save()
        run.refresh_from_db()
        self.assertEqual(run.directory, original)


class ARKValidationTests(TestCase):
    """
    ARKs are publicly resolvable identifiers under a persistence commitment
    (internal design notes), so the save-time checks are load-bearing.
    """

    def _ark(self, **kwargs):
        defaults = {
            "naan": "74218",
            "shoulder": "/t1",
            "assigned_name": "abc123",
            "url": "https://example.org/model.glb",
            "metadata": {},
        }
        defaults.update(kwargs)
        defaults.setdefault(
            "ark",
            f"{defaults['naan']}{defaults['shoulder']}{defaults['assigned_name']}",
        )
        return ARK(**defaults)

    def test_valid_ark_saves(self):
        ark = self._ark()
        ark.save()
        self.assertEqual(str(ark), "ark:/74218/t1abc123")

    def test_shoulder_must_start_with_slash(self):
        with self.assertRaises(ValueError):
            self._ark(shoulder="t1", ark="74218t1abc123").save()

    def test_ark_must_equal_its_components(self):
        with self.assertRaises(ValueError):
            self._ark(ark="74218/t1MISMATCH").save()


class TranslationTests(TestCase):
    """
    Translated content must not leak into identifiers.

    modeltranslation turns `name` into a descriptor returning the active
    language's value. `verbose_id` is built from `name`, so without care it
    would differ per request language -- and it is a URL key and appears in ARK
    metadata (internal design notes).
    """

    def test_registered_fields_have_per_language_columns(self):
        site = _site()
        for field in ("name", "description"):
            for lang in ("en", "or", "hi"):
                self.assertTrue(
                    hasattr(site, f"{field}_{lang}"), f"missing {field}_{lang}"
                )

    def test_writing_the_base_field_populates_english(self):
        site = _site(name="Mukteswar Temple")
        self.assertEqual(site.name_en, "Mukteswar Temple")

    def test_verbose_id_is_the_same_in_every_language(self):
        from django.utils import translation

        site = _site(name="Mukteswar Temple")
        site.name_or = "ମୁକ୍ତେଶ୍ୱର ମନ୍ଦିର"
        site.name_hi = "मुक्तेश्वर मंदिर"
        site.save()

        ids = set()
        for lang in ("en", "or", "hi"):
            with translation.override(lang):
                site.refresh_from_db()
                site.save()
                site.refresh_from_db()
                ids.add(site.verbose_id)
        self.assertEqual(
            ids,
            {"India__Odisha__Khordha__Mukteswar_Temple"},
            "verbose_id must not vary with the active language",
        )

    def test_reading_falls_back_to_english_when_untranslated(self):
        from django.utils import translation

        site = _site(name="Rajarani Temple")
        with translation.override("or"):
            site.refresh_from_db()
            self.assertEqual(site.name, "Rajarani Temple")

    def test_translated_value_is_returned_when_present(self):
        from django.utils import translation

        site = _site(name="Rajarani Temple")
        site.name_hi = "राजारानी मंदिर"
        site.save()
        with translation.override("hi"):
            site.refresh_from_db()
            self.assertEqual(site.name, "राजारानी मंदिर")

    def test_administrative_fields_are_not_translated(self):
        # country/state/district feed verbose_id and are matched against OSM and
        # Wikidata, so they stay single-valued (internal design notes).
        site = _site()
        for field in ("country", "state", "district"):
            self.assertFalse(
                hasattr(site, f"{field}_or"), f"{field} should not be translated"
            )


class DefaultLanguageBackfillTests(TestCase):
    """
    Migration 0026 (internal design notes).

    modeltranslation makes `Site.name` a descriptor over `name_en`. Migration
    0008 created those columns but copied nothing into them, so a row written
    *outside* the ORM -- which is what `pg_restore` does, and what cutover does
    -- reads back empty: no name, no search matches, blank unfurl titles.

    Invisible on SQLite, because that beta was seeded with `loaddata`, which
    writes through the ORM and populates `name_en` on save. Only rehearsing the
    chain on PostgreSQL against a real dump surfaced it.

    These tests use **historical** models from the migration executor rather
    than the concrete ones. That is not incidental: the concrete `Site.name` is
    the descriptor, so a test written against it copies empty to empty and
    passes for the wrong reason -- which is exactly what the first version of
    this test did.
    """

    def setUp(self):
        import importlib

        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        self.mig = importlib.import_module(
            "tirtha.migrations.0026_populate_default_language"
        )
        executor = MigrationExecutor(connection)
        # State just before 0026: the columns exist, the descriptor does not.
        self.apps = executor.loader.project_state(("tirtha", "0025_leaderboard")).apps
        self.HistoricalSite = self.apps.get_model("tirtha", "Site")

    def _bare_site(self, name="Raw Row"):
        """A row as `pg_restore` leaves it: base column set, translation empty."""
        return self.HistoricalSite.objects.create(
            name=name,
            name_en=None,
            country="India",
            state="Odisha",
            district="Khordha",
            verbose_id=f"India__Odisha__Khordha__{name.replace(' ', '_')}",
        )

    def test_a_row_written_outside_the_orm_reads_empty_before_the_fix(self):
        site = self._bare_site()
        self.assertEqual(Site.objects.get(pk=site.pk).name, "")

    def test_the_backfill_makes_the_name_readable(self):
        site = self._bare_site("Konark Sun Temple")
        self.mig.populate(self.apps, None)
        self.assertEqual(Site.objects.get(pk=site.pk).name, "Konark Sun Temple")

    def test_search_works_after_the_backfill(self):
        self._bare_site("Konark Sun Temple")
        self.assertEqual(Site.objects.filter(name__icontains="konark").count(), 0)
        self.mig.populate(self.apps, None)
        self.assertEqual(Site.objects.filter(name__icontains="konark").count(), 1)

    def test_an_existing_translation_is_never_overwritten(self):
        site = self._bare_site("English Name")
        self.HistoricalSite.objects.filter(pk=site.pk).update(name_en="Curated Name")
        self.mig.populate(self.apps, None)
        self.assertEqual(Site.objects.get(pk=site.pk).name, "Curated Name")

    def test_running_twice_is_harmless(self):
        site = self._bare_site("Twice")
        self.mig.populate(self.apps, None)
        self.mig.populate(self.apps, None)
        self.assertEqual(Site.objects.get(pk=site.pk).name, "Twice")

    def test_descriptions_are_backfilled_too(self):
        site = self.HistoricalSite.objects.create(
            name="D",
            description="A 10th-century temple.",
            description_en=None,
            country="India",
            state="Odisha",
            district="Khordha",
            verbose_id="India__Odisha__Khordha__D",
        )
        self.mig.populate(self.apps, None)
        self.assertEqual(
            Site.objects.get(pk=site.pk).description, "A 10th-century temple."
        )
