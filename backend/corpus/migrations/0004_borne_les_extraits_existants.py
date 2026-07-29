"""Borne les extraits déjà en base.

Le plafond de 220 caractères ne s'appliquait qu'au format Markdown : les
poèmes et les citations, en texte brut, ont pu enregistrer un extrait égal au
corps entier (un cas mesuré : 1679 caractères servis dans une carte de liste).
Corriger build_excerpt() ne suffit pas — ces extraits ne seraient régénérés
qu'au prochain enregistrement du texte.

On ne touche QUE les extraits trop longs : un extrait court saisi à la main
est un choix éditorial et reste intact.
"""

from django.db import migrations


def borne_les_extraits(apps, schema_editor):
    from corpus.models import EXCERPT_MAX_CHARS, _tronque

    Text = apps.get_model("corpus", "Text")
    trop_longs = []
    for texte in Text.objects.exclude(excerpt="").iterator():
        if len(texte.excerpt) > EXCERPT_MAX_CHARS:
            texte.excerpt = _tronque(texte.excerpt)
            trop_longs.append(texte)
    if trop_longs:
        Text.objects.bulk_update(trop_longs, ["excerpt"], batch_size=200)


def noop(apps, schema_editor):
    """Irréversible : le texte tronqué est perdu, mais le corps le contient
    toujours — un simple enregistrement régénère l'extrait."""


class Migration(migrations.Migration):
    dependencies = [("corpus", "0003_index_gin_trigrammes")]

    operations = [migrations.RunPython(borne_les_extraits, noop)]
