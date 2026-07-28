"""
Extensions PostgreSQL et amorçage des thèmes historiques.

`unaccent` et `pg_trgm` sont indispensables à la recherche en kreyòl : la langue
n'a pas de dictionnaire PostgreSQL, donc aucune lemmatisation n'est possible.
On compense par l'insensibilité aux accents et la tolérance aux variations
d'orthographe, encore mouvantes.

CREATE EXTENSION exige un rôle superutilisateur.
"""

from django.contrib.postgres.operations import TrigramExtension, UnaccentExtension
from django.db import migrations

# Les 8 thèmes issus de dix ans de publications — extensibles depuis l'admin.
THEMES = [
    ("Lanmou", "Amour"),
    ("Lavi", "Vie"),
    ("Nostalji", "Nostalgie"),
    ("Sosyete", "Société"),
    ("Imè", "Humour"),
    ("Zanmi", "Amitié"),
    ("Espwa", "Espoir"),
    ("Memwa", "Mémoire"),
]


def creer_themes(apps, schema_editor):
    from slugify import slugify

    Theme = apps.get_model("corpus", "Theme")
    for ordre, (ht, fr) in enumerate(THEMES):
        Theme.objects.get_or_create(
            slug=slugify(ht), defaults={"name_ht": ht, "name_fr": fr, "order": ordre}
        )


def supprimer_themes(apps, schema_editor):
    from slugify import slugify

    Theme = apps.get_model("corpus", "Theme")
    Theme.objects.filter(slug__in=[slugify(ht) for ht, _ in THEMES]).delete()


class Migration(migrations.Migration):
    dependencies = [("corpus", "0001_initial")]

    operations = [
        UnaccentExtension(),
        TrigramExtension(),
        migrations.RunPython(creer_themes, supprimer_themes),
    ]
