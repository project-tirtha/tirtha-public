"""
Translatable model content.

internal design notes: the schema for translated *content* has to land before the
models are renamed and backfilled, or that work is done twice. The translations
themselves ship last -- this only creates the columns to hold them.

modeltranslation adds one column per language per registered field, so
`Site.name` becomes `name_en`, `name_or`, `name_hi`. Reading `site.name`
returns the value for the active language, falling back to English.

Languages are English, Odia and Hindi (settings.LANGUAGES). Odia and Hindi
site names can be seeded from Wikidata, which carries vernacular labels
(internal design notes).
"""

from modeltranslation.translator import TranslationOptions, register

from tirtha.models import Category, Site


@register(Site)
class MeshTranslationOptions(TranslationOptions):
    # Only user-facing prose. country/state/district are administrative
    # identifiers that feed verbose_id and are matched against OSM and Wikidata
    # (internal design notes), so they stay single-valued.
    fields = ("name", "description")


@register(Category)
class CategoryTranslationOptions(TranslationOptions):
    # `slug` stays single-valued: it is an identifier used in filters and URLs.
    fields = ("name", "description")
