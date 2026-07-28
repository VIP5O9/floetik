from datetime import datetime

from ninja import Schema


class ThemeOut(Schema):
    slug: str
    name_ht: str
    name_fr: str


class SeriesRef(Schema):
    slug: str
    title: str
    kind: str


class AudioOut(Schema):
    url: str
    duration: int
    waveform: list | None = None


class TextCard(Schema):
    """Ce que voit le lecteur dans une liste — jamais le corps du texte."""

    slug: str
    title: str
    kind: str
    language: str
    excerpt: str
    reading_time: int
    published_at: datetime | None
    themes: list[ThemeOut]
    series: SeriesRef | None
    episode_no: int | None
    has_audio: bool


class Neighbour(Schema):
    slug: str
    title: str
    episode_no: int | None


class TextDetail(TextCard):
    body: str
    format: str
    view_count: int
    available_as_frame: bool
    audio: AudioOut | None
    previous: Neighbour | None
    next: Neighbour | None


class EpisodeOut(Schema):
    """Un épisode au sommaire d'une série.

    Un épisode à venir expose sa date mais ni son corps ni — sauf réglage
    contraire — son titre. C'est la surprise, garantie côté serveur.
    """

    episode_no: int | None
    title: str | None
    slug: str | None
    published_at: datetime | None
    is_available: bool


class SeriesOut(Schema):
    slug: str
    title: str
    kind: str
    language: str
    description: str
    status: str
    cover: str | None
    episode_count: int
    next_episode_at: datetime | None


class SeriesDetail(SeriesOut):
    episodes: list[EpisodeOut]
