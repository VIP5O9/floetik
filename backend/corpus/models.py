"""
Modèles du corpus Floetik.

Principe directeur : un texte est écrit dans UNE langue (kreyòl ou français) et
publié tel quel. Il n'y a pas de traduction, pas de version alternative, pas de
repli linguistique. Le corpus est bilingue, chaque texte ne l'est pas.
"""

import re
import secrets

from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from slugify import slugify

# Marqueur des mots mis en or dans les textes en clair : *mot*
GOLD_MARKER = re.compile(r"\*(?P<word>[^*\n]+)\*")

# Vitesse de lecture retenue pour l'estimation (mots/minute).
# Volontairement basse : lecture de poésie, pas d'article de presse.
WORDS_PER_MINUTE = 180


def strip_markers(text: str) -> str:
    """Retire les marqueurs *…* pour les usages où ils n'ont pas de sens
    (extraits, recherche, export en texte nu)."""
    return GOLD_MARKER.sub(r"\g<word>", text)


class Language(models.TextChoices):
    HT = "ht", "Kreyòl"
    FR = "fr", "Français"


class Kind(models.TextChoices):
    POEM = "poem", "Poème"
    QUOTE = "quote", "Citation"
    STORY = "story", "Histoire"
    NOVEL = "novel", "Roman"
    NEWS = "news", "Actualité"
    OPINION = "opinion", "Opinion"


class Format(models.TextChoices):
    # Retours à la ligne signifiants, aucun formatage. Les strophes SONT le poème.
    PLAIN = "plain", "Texte brut"
    # Prose : paragraphes, intertitres, italiques, liens.
    MARKDOWN = "markdown", "Markdown"


class Status(models.TextChoices):
    DRAFT = "draft", "Brouillon"
    PUBLISHED = "published", "Publié"
    ARCHIVED = "archived", "Archivé"


# Les types dont le corps est du texte brut : tout le reste est de la prose.
PLAIN_KINDS = {Kind.POEM, Kind.QUOTE}


def default_format_for(kind: str) -> str:
    return Format.PLAIN if kind in PLAIN_KINDS else Format.MARKDOWN


def _liste(nombres) -> str:
    """« 3 » ou « 3 et 5 » ou « 3, 4 et 6 » — pour des messages d'erreur lisibles."""
    n = [str(x) for x in nombres]
    return n[0] if len(n) == 1 else f"{', '.join(n[:-1])} et {n[-1]}"


# Place réservée au suffixe de désambiguïsation (« -12 », « -a3f9c1 ») à la fin
# d'un slug : la base est tronquée d'autant pour que le tout tienne en colonne.
RESERVE_SUFFIXE_SLUG = 10


# Nombre d'essais d'écriture avant d'abandonner sur un conflit de slug. Le
# premier essai numérote (« -2 »), les suivants tirent un suffixe au hasard :
# deux enregistrements simultanés ne peuvent pas retomber sur le même.
TENTATIVES_SLUG = 6


def _est_un_conflit_de_slug(exc) -> bool:
    """L'échec porte-t-il sur l'unicité du slug, et sur rien d'autre ?"""
    if isinstance(exc, ValidationError):
        return list(getattr(exc, "error_dict", {})) == ["slug"]
    return "slug" in str(exc).lower()


def unique_slug(model, title: str, instance_pk=None, *, aleatoire: bool = False) -> str:
    """Slug ASCII sans accents — « Lanmou nan lapli » → « lanmou-nan-lapli ».

    La base est tronquée à la taille de la colonne : la translittération gonfle
    certains alphabets (« ж » → « zh », « 龍 » → « long »), et un titre de 200
    caractères peut produire un slug de 999 — que la colonne ne peut pas
    accueillir.
    """
    place = model._meta.get_field("slug").max_length - RESERVE_SUFFIXE_SLUG
    base = slugify(strip_markers(title), max_length=place, word_boundary=True) or "tex"
    if aleatoire:
        # Reprise après collision : ne pas relire la base, deux threads y
        # liraient la même chose et choisiraient encore le même numéro.
        return f"{base}-{secrets.token_hex(3)}"
    qs = model.objects.filter(slug__startswith=base)
    if instance_pk:
        qs = qs.exclude(pk=instance_pk)
    # Une seule requête, quel que soit le nombre d'homonymes : l'ancienne boucle
    # interrogeait la base une fois par tentative — 402 requêtes pour le 402e
    # « Sanzatann ».
    pris = set(qs.values_list("slug", flat=True))
    if base not in pris:
        return base
    n = 2
    while f"{base}-{n}" in pris:
        n += 1
    return f"{base}-{n}"


class Theme(models.Model):
    """Les 8 thèmes historiques (Lanmou, Lavi, Nostalji…) — extensibles."""

    name_ht = models.CharField("nom en kreyòl", max_length=60)
    name_fr = models.CharField("nom en français", max_length=60)
    slug = models.SlugField(unique=True, max_length=70, blank=True)
    description = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField("ordre d'affichage", default=0)

    class Meta:
        verbose_name = "thème"
        verbose_name_plural = "thèmes"
        ordering = ["order", "name_ht"]

    def __str__(self):
        return self.name_ht

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Theme, self.name_ht, self.pk)
        super().save(*args, **kwargs)


class SeriesKind(models.TextChoices):
    NOVEL = "novel", "Roman"
    STORY_SERIES = "story_series", "Feuilleton"
    COLLECTION = "collection", "Recueil"


class SeriesStatus(models.TextChoices):
    ONGOING = "ongoing", "En cours"
    COMPLETED = "completed", "Terminée"
    PAUSED = "paused", "En pause"


class Series(models.Model):
    """Une œuvre publiée par épisodes — « la suite dans 2 jours »."""

    title = models.CharField("titre", max_length=200)
    slug = models.SlugField(unique=True, max_length=220, blank=True)
    kind = models.CharField("type", max_length=20, choices=SeriesKind.choices)
    language = models.CharField("langue", max_length=2, choices=Language.choices)
    description = models.TextField("présentation", blank=True)
    cover = models.ImageField("couverture", upload_to="seri/", blank=True, null=True)
    status = models.CharField(
        "état", max_length=12, choices=SeriesStatus.choices, default=SeriesStatus.ONGOING
    )
    # Faux ⇒ les épisodes à venir n'affichent que leur date, jamais leur titre.
    # C'est le réglage qui protège la surprise.
    reveal_titles = models.BooleanField(
        "révéler le titre des épisodes à venir",
        default=False,
        help_text="Décoché, le public voit « Epizòd 4 — 29 juillet » sans le titre.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "série"
        verbose_name_plural = "séries"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Series, self.title, self.pk)
        super().save(*args, **kwargs)

    @property
    def published_episodes(self):
        return self.texts.live().order_by("episode_no")

    @property
    def next_episode(self):
        """Le prochain épisode programmé — alimente le compte à rebours public."""
        return (
            self.texts.filter(
                status=Status.PUBLISHED, published_at__gt=timezone.now()
            )
            .order_by("published_at")
            .first()
        )

    @property
    def next_episode_at(self):
        nxt = self.next_episode
        return nxt.published_at if nxt else None


class TextQuerySet(models.QuerySet):
    def live(self):
        """Publié ET la date est passée. Un épisode programmé pour dans 2 jours
        existe en base, apparaît au sommaire, mais son corps n'est jamais servi."""
        return self.filter(status=Status.PUBLISHED, published_at__lte=timezone.now())

    def scheduled(self):
        return self.filter(status=Status.PUBLISHED, published_at__gt=timezone.now())


class Text(models.Model):
    kind = models.CharField("type", max_length=12, choices=Kind.choices)
    format = models.CharField(
        "format", max_length=10, choices=Format.choices, blank=True,
        help_text="Laissé vide, déduit du type : brut pour la poésie, Markdown pour la prose.",
    )
    language = models.CharField(
        "langue d'écriture", max_length=2, choices=Language.choices,
        help_text="La langue dans laquelle Florentz a écrit. Le texte est publié tel quel.",
    )

    title = models.CharField("titre", max_length=200)
    slug = models.SlugField(unique=True, max_length=220, blank=True)
    body = models.TextField(
        "texte",
        help_text="Poésie : les retours à la ligne sont conservés tels quels. "
                  "Entourez un mot d'astérisques *comme ceci* pour le mettre en or.",
    )
    excerpt = models.TextField(
        "extrait", blank=True, help_text="Laissé vide, généré depuis le texte."
    )

    themes = models.ManyToManyField(Theme, related_name="texts", blank=True)
    series = models.ForeignKey(
        Series, related_name="texts", on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name="série",
    )
    episode_no = models.PositiveIntegerField("n° d'épisode", null=True, blank=True)

    status = models.CharField(
        "état", max_length=12, choices=Status.choices, default=Status.DRAFT
    )
    published_at = models.DateTimeField(
        "date de publication", null=True, blank=True,
        help_text="Une date future programme la parution — le texte reste invisible jusque-là.",
    )

    reading_time = models.PositiveIntegerField("durée de lecture (min)", default=0)
    view_count = models.PositiveIntegerField("lectures", default=0)
    available_as_frame = models.BooleanField("disponible en cadre", default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TextQuerySet.as_manager()

    class Meta:
        verbose_name = "texte"
        verbose_name_plural = "textes"
        ordering = ["-published_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["series", "episode_no"],
                name="unique_episode_per_series",
                condition=models.Q(series__isnull=False),
            )
        ]
        indexes = [
            models.Index(fields=["status", "published_at"]),
            models.Index(fields=["language", "kind"]),
            # Index GIN trigrammes : accélère cheche() (recherche floue par
            # similarité de trigrammes) sur titre et corps. Sans lui, chaque
            # recherche fait un scan séquentiel de toute la table. PostgreSQL
            # uniquement — voir migration 0003 pour le no-op ailleurs.
            GinIndex(fields=["title"], name="corpus_text_title_trgm_gin", opclasses=["gin_trgm_ops"]),
            GinIndex(fields=["body"], name="corpus_text_body_trgm_gin", opclasses=["gin_trgm_ops"]),
        ]

    def __str__(self):
        return self.title

    @property
    def is_live(self) -> bool:
        return (
            self.status == Status.PUBLISHED
            and self.published_at is not None
            and self.published_at <= timezone.now()
        )

    @property
    def is_scheduled(self) -> bool:
        return (
            self.status == Status.PUBLISHED
            and self.published_at is not None
            and self.published_at > timezone.now()
        )

    def clean(self):
        """Protège l'ordre d'un feuilleton.

        Trois fautes ruinent une série à suspense, et toutes sont faciles à
        commettre un dimanche soir en programmant plusieurs épisodes d'affilée :
        publier un épisode alors qu'un précédent manque, alors qu'un précédent
        est encore en brouillon, ou en le datant avant un épisode antérieur.
        La dernière est la plus vicieuse : tout paraît normal en base, et le
        lecteur découvre la suite avant le début.
        """
        if self.episode_no and not self.series:
            raise ValidationError({"series": "Un numéro d'épisode exige une série."})
        if not (self.series and self.episode_no and self.status == Status.PUBLISHED):
            return

        precedents = self.series.texts.filter(
            episode_no__lt=self.episode_no
        ).exclude(pk=self.pk)

        # a) Aucun trou dans la numérotation : 1…N-1 doivent tous exister.
        existants = set(precedents.values_list("episode_no", flat=True))
        absents = sorted(set(range(1, self.episode_no)) - existants)
        if absents:
            raise ValidationError(
                {"episode_no": f"L'épisode {self.episode_no} ne peut pas paraître : "
                               f"l'épisode {_liste(absents)} n'existe pas encore."}
            )

        # b) Aucun précédent en brouillon ou archivé.
        en_attente = sorted(
            precedents.exclude(status=Status.PUBLISHED).values_list(
                "episode_no", flat=True
            )
        )
        if en_attente:
            raise ValidationError(
                {"status": f"L'épisode {self.episode_no} ne peut pas paraître : "
                           f"l'épisode {_liste(en_attente)} n'est pas publié."}
            )

        # c) Les dates doivent suivre la numérotation, programmation comprise.
        quand = self.published_at or timezone.now()
        trop_tard = (
            precedents.filter(published_at__gte=quand).order_by("-episode_no").first()
        )
        if trop_tard:
            raise ValidationError(
                {"published_at": f"L'épisode {self.episode_no} paraîtrait avant "
                                 f"l'épisode {trop_tard.episode_no}, prévu le "
                                 f"{timezone.localtime(trop_tard.published_at):%d/%m/%Y à %Hh%M}."}
            )
        trop_tot = (
            self.series.texts.filter(episode_no__gt=self.episode_no)
            .exclude(pk=self.pk)
            .filter(published_at__lte=quand)
            .order_by("episode_no")
            .first()
        )
        if trop_tot:
            raise ValidationError(
                {"published_at": f"L'épisode {trop_tot.episode_no} est prévu le "
                                 f"{timezone.localtime(trop_tot.published_at):%d/%m/%Y à %Hh%M}, "
                                 f"donc avant celui-ci."}
            )

    def build_excerpt(self) -> str:
        clean = strip_markers(self.body).strip()
        if self.format == Format.PLAIN:
            # Poésie : les deux premiers vers non vides, la forme compte.
            lines = [ln for ln in clean.splitlines() if ln.strip()]
            return "\n".join(lines[:2])
        flat = " ".join(clean.split())
        return flat[:220].rsplit(" ", 1)[0] + "…" if len(flat) > 220 else flat

    def save(self, *args, **kwargs):
        if not self.format:
            self.format = default_format_for(self.kind)
        if not self.excerpt:
            self.excerpt = self.build_excerpt()
        words = len(strip_markers(self.body).split())
        self.reading_time = max(1, round(words / WORDS_PER_MINUTE))
        if self.status == Status.PUBLISHED and self.published_at is None:
            self.published_at = timezone.now()

        # Le slug déjà fixé — texte existant, ou slug saisi à la main dans
        # l'admin — n'est jamais recalculé : c'est une URL publique, elle doit
        # rester stable.
        slug_impose = bool(self.slug)
        for tentative in range(TENTATIVES_SLUG):
            if not slug_impose:
                self.slug = unique_slug(
                    Text, self.title, self.pk, aleatoire=tentative > 0
                )
            try:
                # Le slug est choisi AVANT l'INSERT mais écrit APRÈS : entre les
                # deux, un autre enregistrement peut prendre le même. On réessaie
                # au lieu de laisser remonter une 500. Le savepoint est
                # indispensable : sans lui la transaction resterait cassée.
                with transaction.atomic():
                    self.full_clean()
                    super().save(*args, **kwargs)
                return
            except (IntegrityError, ValidationError) as exc:
                dernier_essai = tentative == TENTATIVES_SLUG - 1
                if slug_impose or dernier_essai or not _est_un_conflit_de_slug(exc):
                    raise


class AudioVersion(models.Model):
    """Florentz lisant son propre texte.

    Date de publication distincte : l'enregistrement arrive souvent après l'écrit,
    et un roman devient un audiolivre chapitre par chapitre.
    """

    text = models.OneToOneField(
        Text, related_name="audio", on_delete=models.CASCADE, verbose_name="texte"
    )
    file = models.FileField("fichier audio", upload_to="odyo/%Y/%m/")
    duration = models.PositiveIntegerField("durée (secondes)", default=0)
    waveform = models.JSONField(
        "forme d'onde", blank=True, null=True,
        help_text="Amplitudes normalisées, pour dessiner la piste côté site.",
    )
    published_at = models.DateTimeField("date de publication", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "version audio"
        verbose_name_plural = "versions audio"

    def __str__(self):
        return f"Audio — {self.text.title}"

    @property
    def is_live(self) -> bool:
        return self.published_at is not None and self.published_at <= timezone.now()
