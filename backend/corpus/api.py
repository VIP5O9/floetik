"""
API de lecture publique.

Aucune écriture : Florentz publie par l'admin, le public lit. Cette API n'expose
donc que des GET, et ne sert jamais le corps d'un texte non encore paru.
"""

from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from ninja import NinjaAPI, Query
from ninja.pagination import LimitOffsetPagination, paginate

from .models import Series, Status, Text, Theme
from .schemas import (
    EpisodeOut,
    SeriesDetail,
    SeriesOut,
    TextCard,
    TextDetail,
    ThemeOut,
)

api = NinjaAPI(
    title="Floetik",
    version="1.0.0",
    description="Le corpus de Florentz Charles — poèmes, romans, histoires, opinions.",
)


def _card(t: Text) -> dict:
    return {
        "slug": t.slug,
        "title": t.title,
        "kind": t.kind,
        "language": t.language,
        "excerpt": t.excerpt,
        "reading_time": t.reading_time,
        "published_at": t.published_at,
        "themes": list(t.themes.all()),
        "series": t.series,
        "episode_no": t.episode_no,
        "has_audio": hasattr(t, "audio") and t.audio.is_live,
    }


def _base_qs():
    return (
        Text.objects.live()
        .select_related("series", "audio")
        .prefetch_related("themes")
    )


# ───────────────────────────── Textes ─────────────────────────────


@api.get("/tex", response=list[TextCard], summary="Lister les textes")
@paginate(LimitOffsetPagination)
def list_texts(
    request,
    lang: str | None = Query(None, description="ht ou fr — filtre, pas traduction"),
    kind: str | None = None,
    theme: str | None = None,
    series: str | None = None,
):
    qs = _base_qs()
    if lang:
        qs = qs.filter(language=lang)
    if kind:
        qs = qs.filter(kind=kind)
    if theme:
        qs = qs.filter(themes__slug=theme)
    if series:
        qs = qs.filter(series__slug=series)
    return [_card(t) for t in qs]


@api.get("/tex/{slug}", response=TextDetail, summary="Lire un texte")
def get_text(request, slug: str):
    text = get_object_or_404(_base_qs(), slug=slug)
    Text.objects.filter(pk=text.pk).update(view_count=F("view_count") + 1)

    previous = nxt = None
    if text.series and text.episode_no:
        siblings = text.series.texts.live()
        previous = (
            siblings.filter(episode_no__lt=text.episode_no)
            .order_by("-episode_no")
            .first()
        )
        nxt = (
            siblings.filter(episode_no__gt=text.episode_no)
            .order_by("episode_no")
            .first()
        )

    audio = None
    if hasattr(text, "audio") and text.audio.is_live:
        audio = {
            "url": text.audio.file.url,
            "duration": text.audio.duration,
            "waveform": text.audio.waveform,
        }

    return {
        **_card(text),
        "body": text.body,
        "format": text.format,
        "view_count": text.view_count + 1,
        "available_as_frame": text.available_as_frame,
        "audio": audio,
        "previous": previous,
        "next": nxt,
    }


@api.get("/tex/{slug}/vwazen", response=list[TextCard], summary="Textes voisins")
def related_texts(request, slug: str, limit: int = 4):
    text = get_object_or_404(Text.objects.live(), slug=slug)
    qs = (
        _base_qs()
        .filter(themes__in=text.themes.all())
        .exclude(pk=text.pk)
        .distinct()[:limit]
    )
    return [_card(t) for t in qs]


@api.get("/aza", response=TextDetail, summary="Un texte au hasard")
def random_text(request):
    """« Yon tèks o aza » — le geste qui fait revenir sur un site de poésie."""
    text = _base_qs().order_by("?").first()
    if not text:
        return api.create_response(request, {"detail": "Corpus vide"}, status=404)
    return get_text(request, text.slug)


# ───────────────────────────── Recherche ─────────────────────────────


@api.get("/cheche", response=list[TextCard], summary="Chercher dans le corpus")
@paginate(LimitOffsetPagination)
def search(request, q: str, lang: str | None = None):
    """Recherche insensible aux accents et tolérante aux fautes.

    PostgreSQL n'a pas de dictionnaire pour le créole haïtien : pas de
    lemmatisation possible. On combine donc `unaccent` (« lanmou » trouve
    « lànmou ») et les trigrammes de `pg_trgm` pour absorber les variations
    d'orthographe, encore mouvantes en kreyòl.
    """
    q = (q or "").strip()
    if len(q) < 2:
        return []

    qs = _base_qs().filter(
        # Correspondance directe, accents ignorés
        Q(title__unaccent__icontains=q)
        | Q(body__unaccent__icontains=q)
        # Un thème est une porte d'entrée : chercher « Lanmou » doit ramener
        # les textes qui en relèvent, même s'ils ne contiennent pas le mot.
        | Q(themes__name_ht__unaccent__icontains=q)
        | Q(themes__name_fr__unaccent__icontains=q)
        # Tolérance aux fautes. `trigram_word_similar` compare la requête au mot
        # le plus proche du texte, alors que `trigram_similar` la comparerait au
        # champ entier — une faute dans un titre long ne ressortirait jamais.
        | Q(title__unaccent__trigram_word_similar=q)
        | Q(body__unaccent__trigram_word_similar=q)
    )
    if lang:
        qs = qs.filter(language=lang)
    return [_card(t) for t in qs.distinct()]


# ───────────────────────────── Thèmes ─────────────────────────────


@api.get("/tem", response=list[ThemeOut], summary="Lister les thèmes")
def list_themes(request):
    return Theme.objects.all()


# ───────────────────────────── Séries ─────────────────────────────


def _series_out(s: Series) -> dict:
    return {
        "slug": s.slug,
        "title": s.title,
        "kind": s.kind,
        "language": s.language,
        "description": s.description,
        "status": s.status,
        "cover": s.cover.url if s.cover else None,
        "episode_count": s.texts.live().count(),
        "next_episode_at": s.next_episode_at,
    }


@api.get("/seri", response=list[SeriesOut], summary="Lister les séries")
def list_series(request, lang: str | None = None):
    qs = Series.objects.all()
    if lang:
        qs = qs.filter(language=lang)
    return [_series_out(s) for s in qs]


@api.get("/seri/{slug}", response=SeriesDetail, summary="Sommaire d'une série")
def get_series(request, slug: str):
    """Le sommaire inclut les épisodes à venir — c'est là que naît l'attente.

    Un épisode non paru expose sa date de parution et rien d'autre : ni corps,
    ni slug, et son titre seulement si l'auteur a choisi de le révéler.
    """
    series = get_object_or_404(Series, slug=slug)

    episodes: list[EpisodeOut] = []
    for t in series.texts.filter(status=Status.PUBLISHED).order_by("episode_no"):
        if t.is_live:
            episodes.append(
                {
                    "episode_no": t.episode_no,
                    "title": t.title,
                    "slug": t.slug,
                    "published_at": t.published_at,
                    "is_available": True,
                }
            )
        else:
            episodes.append(
                {
                    "episode_no": t.episode_no,
                    "title": t.title if series.reveal_titles else None,
                    "slug": None,
                    "published_at": t.published_at,
                    "is_available": False,
                }
            )

    return {**_series_out(series), "episodes": episodes}
