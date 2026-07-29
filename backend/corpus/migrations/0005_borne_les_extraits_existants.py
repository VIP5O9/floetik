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
    # Copie historique : une migration de données ne doit pas importer le code
    # vivant du modèle. S'il change ou disparaît, la migration casse alors qu'elle
    # doit rester rejouable sur une base ancienne.
    EXCERPT_MAX_CHARS = 220

    def _tronque(texte):
        if len(texte) <= EXCERPT_MAX_CHARS:
            return texte
        coupe = texte[:EXCERPT_MAX_CHARS].rsplit(" ", 1)[0]
        return (coupe or texte[:EXCERPT_MAX_CHARS]) + "…"

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
    dependencies = [("corpus", "0004_alter_text_episode_no")]

    operations = [migrations.RunPython(borne_les_extraits, noop)]
