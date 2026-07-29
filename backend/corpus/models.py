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
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.dispatch import receiver
from django.utils import timezone
from slugify import slugify

# Marqueur des mots mis en or dans les textes en clair : *mot*
GOLD_MARKER = re.compile(r"\*(?P<word>[^*\n]+)\*")

# Vitesse de lecture retenue pour l'estimation (mots/minute).
# Volontairement basse : lecture de poésie, pas d'article de presse.
WORDS_PER_MINUTE = 180

# Longueur maximale d'un extrait, avant l'ajout du caractère de troncature.
EXCERPT_MAX_CHARS = 220


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


def _tronque(texte: str, limite: int = EXCERPT_MAX_CHARS) -> str:
    """Borne un extrait sans couper un mot en deux.

    S'applique à TOUTES les formes : un vers unique de 1700 caractères servait
    autrefois le corps entier dans la carte de liste, ce que l'extrait n'est
    jamais censé faire.
    """
    if len(texte) <= limite:
        return texte
    coupe = texte[:limite]
    # Se replier sur la dernière frontière de mot ; à défaut (un « mot » plus
    # long que la limite), couper net plutôt que de tout rendre.
    if (espace := coupe.rstrip().rfind(" ")) > 0:
        coupe = coupe[:espace]
    return coupe.rstrip() + "…"


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
            # Seule la date est lue ici (compte à rebours) : ne pas rapatrier
            # le corps d'un épisode qui n'est de toute façon pas encore paru.
            .defer("body")
            .order_by("published_at")
            .first()
        )

    @property
    def next_episode_at(self):
        nxt = self.next_episode
        return nxt.published_at if nxt else None


@receiver(models.signals.pre_delete, sender=Series)
def _detacher_les_episodes(sender, instance, **kwargs):
    """Supprimer une série efface aussi le n° d'épisode de ses textes.

    Le lien vers la série passe à NULL (on_delete=SET_NULL) mais le numéro, lui,
    restait : le public lisait « épisode 1 » d'une série qui n'existe plus, et la
    fiche ne pouvait plus jamais être enregistrée, clean() exigeant une série pour
    tout numéro d'épisode. Branché sur pre_delete, donc valable aussi pour une
    suppression en lot (Series.objects.filter(...).delete()), qui n'appelle pas
    Series.delete().
    """
    instance.texts.update(episode_no=None)


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
    # La numérotation commence à 1 : un « épisode 0 » (prologue) serait légal
    # pour un PositiveIntegerField, mais 0 est faux en Python et traverserait
    # sans bruit les conditions d'entrée du garde-fou d'ordre. Un prologue est
    # l'épisode 1.
    episode_no = models.PositiveIntegerField(
        "n° d'épisode", null=True, blank=True,
        validators=[MinValueValidator(1, "La numérotation des épisodes commence à 1.")],
        help_text="Laissé vide hors série. Un prologue est l'épisode 1, pas l'épisode 0.",
    )

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
        # `is not None` et non la simple vérité : 0 est faux en Python, et un
        # épisode numéroté 0 sortirait d'ici sans avoir été contrôlé.
        if self.episode_no is not None and not self.series:
            raise ValidationError(
                {"series": "Un numéro d'épisode exige une série : choisissez une "
                           "série, ou videz le n° d'épisode."}
            )
        if not (
            self.series
            and self.episode_no is not None
            and self.status == Status.PUBLISHED
        ):
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

        # b) Aucun précédent en brouillon, archivé — ou sans date de parution.
        # Un « publié » sans date n'est jamais servi (is_live reste faux), et les
        # comparaisons SQL du point c) ignorent les NULL : sans ce filtre, la
        # suite paraîtrait devant une ouverture que personne ne peut lire.
        en_attente = sorted(
            precedents.filter(
                ~models.Q(status=Status.PUBLISHED) | models.Q(published_at__isnull=True)
            ).values_list("episode_no", flat=True)
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
            return _tronque("\n".join(lines[:2]))
        flat = " ".join(clean.split())
        return _tronque(flat)

    def _excerpt_is_stale(self) -> bool:
        """L'extrait doit-il être régénéré depuis le corps ?

        Oui s'il est vide, oui s'il avait été généré automatiquement depuis une
        version antérieure du corps. Non s'il a été écrit à la main : corriger
        son texte ne doit pas effacer un chapô choisi par l'auteur.

        Sans ce recalcul, retirer une phrase du corps laissait l'API servir
        l'ancienne indéfiniment — or retirer une phrase EST la rétractation.
        """
        if not self.excerpt:
            return True
        if not self.pk:
            return False
        ancien = (
            Text.objects.filter(pk=self.pk)
            .values_list("body", "excerpt", "format")
            .first()
        )
        if ancien is None:
            return False
        body, excerpt, format_ = ancien
        if body == self.body:
            return False
        # Reconstruire l'extrait tel qu'il aurait été produit pour l'ancien
        # corps : s'il correspond, c'est bien un extrait automatique.
        temoin = Text(body=body, format=format_)
        return excerpt == temoin.build_excerpt() == self.excerpt

    def save(self, *args, **kwargs):
        if not self.format:
            self.format = default_format_for(self.kind)
        # Le slug n'est PAS calculé ici : la boucle de reprise plus bas s'en
        # charge, et le fixer maintenant désactiverait sa protection contre les
        # enregistrements concurrents.
        if self._excerpt_is_stale():
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
