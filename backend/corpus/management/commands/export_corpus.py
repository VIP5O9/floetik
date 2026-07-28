"""
Export intégral du corpus.

Ces textes sont l'œuvre d'une vie, aujourd'hui dispersée sur un compte Instagram
qui peut disparaître demain. La plateforme doit être une libération, pas une
nouvelle prison : à tout moment, une commande rend le corpus entier sous une
forme lisible sans Floetik, sans Django et sans base de données.

    python manage.py export_corpus
    python manage.py export_corpus --out D:\\sauvegardes\\floetik
"""

import json
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from corpus.models import Series, Text


class Command(BaseCommand):
    help = "Exporte tout le corpus en JSON + Markdown (brouillons compris)."

    def add_arguments(self, parser):
        parser.add_argument("--out", default=settings.CORPUS_EXPORT_DIR)
        parser.add_argument(
            "--published-only",
            action="store_true",
            help="N'exporter que les textes déjà parus.",
        )

    def handle(self, *args, **opts):
        stamp = timezone.localtime().strftime("%Y-%m-%d_%H%M")
        root = Path(opts["out"]) / stamp
        (root / "tex").mkdir(parents=True, exist_ok=True)

        textes = Text.objects.all().select_related("series").prefetch_related("themes")
        if opts["published_only"]:
            textes = textes.live()

        payload = []
        for t in textes.order_by("created_at"):
            data = {
                "titre": t.title,
                "slug": t.slug,
                "type": t.get_kind_display(),
                "langue": t.get_language_display(),
                "format": t.format,
                "themes": [th.name_ht for th in t.themes.all()],
                "serie": t.series.title if t.series else None,
                "episode": t.episode_no,
                "etat": t.get_status_display(),
                "publie_le": t.published_at.isoformat() if t.published_at else None,
                "lectures": t.view_count,
                "texte": t.body,
            }
            payload.append(data)

            # Un fichier Markdown par texte : lisible dans n'importe quel éditeur,
            # dans dix ans, sans aucun outil.
            front = [
                "---",
                f"titre: {t.title}",
                f"type: {t.get_kind_display()}",
                f"langue: {t.get_language_display()}",
            ]
            if t.series:
                front.append(f"serie: {t.series.title}")
                front.append(f"episode: {t.episode_no}")
            if t.themes.exists():
                front.append(f"themes: {', '.join(th.name_ht for th in t.themes.all())}")
            if t.published_at:
                front.append(f"publie: {t.published_at.date().isoformat()}")
            front.append("---")

            fichier = root / "tex" / f"{t.slug}.md"
            fichier.write_text(
                "\n".join(front) + f"\n\n# {t.title}\n\n{t.body}\n", encoding="utf-8"
            )

        series = [
            {
                "titre": s.title,
                "slug": s.slug,
                "type": s.get_kind_display(),
                "langue": s.get_language_display(),
                "etat": s.get_status_display(),
                "description": s.description,
                "episodes": list(
                    s.texts.order_by("episode_no").values_list("episode_no", "title")
                ),
            }
            for s in Series.objects.all()
        ]

        (root / "corpus.json").write_text(
            json.dumps(
                {
                    "exporte_le": datetime.now().isoformat(),
                    "nombre_de_textes": len(payload),
                    "series": series,
                    "textes": payload,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"{len(payload)} texte(s) et {len(series)} série(s) exportés dans {root}"
            )
        )
