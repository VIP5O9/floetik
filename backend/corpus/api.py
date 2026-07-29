"""
API de lecture publique.

Aucune écriture : Florentz publie par l'admin, le public lit. Cette API n'expose
donc que des GET, et ne sert jamais le corps d'un texte non encore paru.
"""

import random
import re

from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.db.models import Count, F, Min, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
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

# Une même IP ne recompte pas un texte avant ce délai — absorbe les rechargements
# en rafale sans faire disparaître de vraies relectures plus tard dans la journée.
VIEW_DEDUP_WINDOW = 30 * 60  # secondes

BOT_USER_AGENT = re.compile(
    r"bot|crawl|spider|slurp|preview|monitor|facebookexternalhit|whatsapp|telegram",
    re.IGNORECASE,
)


def _counts_as_view(request, text: "Text") -> bool:
    """Faux pour un robot connu ou une IP ayant déjà vu ce texte récemment.

    IP prise sur REMOTE_ADDR : pas de confiance accordée à X-Forwarded-For tant
    que la topologie de déploiement (proxy Railway/Cloudflare) n'est pas fixée —
    un en-tête client n'est pas une source fiable d'IP sans ça.
    """
    user_agent = request.META.get("HTTP_USER_AGENT", "")
    if not user_agent or BOT_USER_AGENT.search(user_agent):
        return False
    ip = request.META.get("REMOTE_ADDR", "")
    cache_key = f"vu:{text.pk}:{ip}"
    return cache.add(cache_key, True, VIEW_DEDUP_WINDOW)

api = NinjaAPI(
    title="Floetik",
    version="1.0.0",
    description="Le corpus de Florentz Charles — poèmes, romans, histoires, opinions.",
    # La documentation (Swagger UI et openapi.json) décrit toute la surface de
    # l'API : c'est la carte de la maison. Elle reste ouverte à Florentz, qui
    # est déjà connecté à l'admin, mais pas au premier venu.
    docs_decorator=staff_member_required,
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
    return qs


@api.get("/tex/{slug}", response=TextDetail, summary="Lire un texte")
def get_text(request, slug: str):
    text = get_object_or_404(_base_qs(), slug=slug)
    if _counts_as_view(request, text):
        Text.objects.filter(pk=text.pk).update(view_count=F("view_count") + 1)
        text.view_count += 1

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
        "view_count": text.view_count,
        "available_as_frame": text.available_as_frame,
        "audio": audio,
        "previous": previous,
        "next": nxt,
    }


@api.get("/tex/{slug}/vwazen", response=list[TextCard], summary="Textes voisins")
def related_texts(request, slug: str, limit: int = Query(4, ge=1, le=20)):
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
    """« Yon tèks o aza » — le geste qui fait revenir sur un site de poésie.

    Un OFFSET aléatoire évite le scan complet + tri d'ORDER BY RANDOM(), qui
    grossit avec tout le corpus à chaque appel — une cible facile.
    """
    qs = _base_qs()
    count = qs.count()
    if not count:
        return api.create_response(request, {"detail": "Corpus vide"}, status=404)
    text = qs[random.randrange(count)]
    return get_text(request, text.slug)


# ───────────────────────────── Recherche ─────────────────────────────

# `q` part dans six prédicats SQL (unaccent+icontains sur titre et corps, deux
# noms de thème, deux comparaisons trigrammes) : le coût est en O(len(q) × lignes).
# Sans plafond, un GET anonyme de 500 000 caractères occupe un backend Postgres
# plus de huit minutes. 200 caractères laissent passer un vers entier ou une
# phrase de recherche en kreyòl comme en français ; au-delà, ce n'est plus une
# recherche. Rejet en 422 plutôt que troncature : tronquer répondrait à une
# requête que le lecteur n'a pas faite.
SEARCH_QUERY_MAX_LENGTH = 200


@api.get("/cheche", response=list[TextCard], summary="Chercher dans le corpus")
@paginate(LimitOffsetPagination)
def search(
    request,
    q: str = Query(
        ...,
        max_length=SEARCH_QUERY_MAX_LENGTH,
        description="Termes cherchés — 200 caractères au plus",
    ),
    lang: str | None = None,
):
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
    return qs.distinct()


# ───────────────────────────── Thèmes ─────────────────────────────


@api.get("/tem", response=list[ThemeOut], summary="Lister les thèmes")
def list_themes(request):
    return Theme.objects.all()


# ───────────────────────────── Séries ─────────────────────────────


def _series_out(s: Series) -> dict:
    # list_series() pré-calcule ces deux champs par annotation SQL pour éviter
    # un COUNT + un MIN par série ; get_series() (une seule série) se rabat
    # sur les propriétés du modèle.
    episode_count = s._episode_count if hasattr(s, "_episode_count") else s.texts.live().count()
    next_episode_at = (
        s._next_episode_at if hasattr(s, "_next_episode_at") else s.next_episode_at
    )
    return {
        "slug": s.slug,
        "title": s.title,
        "kind": s.kind,
        "language": s.language,
        "description": s.description,
        "status": s.status,
        "cover": s.cover.url if s.cover else None,
        "episode_count": episode_count,
        "next_episode_at": next_episode_at,
    }


def _visible_series_qs():
    """Une série n'existe publiquement qu'à partir de son premier épisode publié
    (paru ou programmé). Sans ce filtre, créer la fiche d'une série à venir —
    titre, couverture, présentation — la rendrait publique avant même qu'un
    épisode soit programmé, à rebours de tout le soin apporté ailleurs à
    protéger la surprise (reveal_titles, épisodes programmés sans corps)."""
    return Series.objects.filter(
        pk__in=Text.objects.filter(status=Status.PUBLISHED, series__isnull=False).values(
            "series_id"
        )
    )


@api.get("/seri", response=list[SeriesOut], summary="Lister les séries")
def list_series(request, lang: str | None = None):
    now = timezone.now()
    qs = _visible_series_qs().annotate(
        _episode_count=Count(
            "texts", filter=Q(texts__status=Status.PUBLISHED, texts__published_at__lte=now)
        ),
        _next_episode_at=Min(
            "texts__published_at",
            filter=Q(texts__status=Status.PUBLISHED, texts__published_at__gt=now),
        ),
    )
    if lang:
        qs = qs.filter(language=lang)
    return [_series_out(s) for s in qs]


@api.get("/seri/{slug}", response=SeriesDetail, summary="Sommaire d'une série")
def get_series(request, slug: str):
    """Le sommaire inclut les épisodes à venir — c'est là que naît l'attente.

    Un épisode non paru expose sa date de parution et rien d'autre : ni corps,
    ni slug, et son titre seulement si l'auteur a choisi de le révéler.
    """
    series = get_object_or_404(_visible_series_qs(), slug=slug)

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
