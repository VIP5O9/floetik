"""
Index GIN trigrammes sur titre et corps.

`pg_trgm` est activé depuis la migration 0002, et `cheche()` (corpus/api.py)
interroge déjà `Text.title`/`Text.body` avec des lookups
`__unaccent__trigram_word_similar`. Sans index GIN dédié, PostgreSQL n'a pas
d'autre choix que de scanner toute la table à chaque recherche floue — ces
deux index comblent ce trou.

`GinIndex` avec `USING gin` n'a de sens que sous PostgreSQL : la suite de
tests tourne en local sur SQLite en mémoire (aucun serveur PostgreSQL
disponible ici), et le schema editor SQLite ne sait pas honorer `USING gin`.
`AddTrigramGinIndex` se neutralise donc hors PostgreSQL, exactement comme
`TrigramExtension`/`UnaccentExtension` le font déjà dans la migration 0002.
"""

from django.contrib.postgres.indexes import GinIndex
from django.db import migrations


class AddTrigramGinIndex(migrations.AddIndex):
    """`AddIndex` no-op hors PostgreSQL (index GIN trigrammes uniquement)."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)


class Migration(migrations.Migration):
    dependencies = [("corpus", "0002_extensions_et_themes")]

    operations = [
        AddTrigramGinIndex(
            model_name="text",
            index=GinIndex(
                fields=["title"],
                name="corpus_text_title_trgm_gin",
                opclasses=["gin_trgm_ops"],
            ),
        ),
        AddTrigramGinIndex(
            model_name="text",
            index=GinIndex(
                fields=["body"],
                name="corpus_text_body_trgm_gin",
                opclasses=["gin_trgm_ops"],
            ),
        ),
    ]
