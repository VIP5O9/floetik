"""
Le back-office de Florentz.

Priorité absolue : publier un texte doit être rapide et sans piège. Si l'admin
est pénible, il retourne sur Instagram et la plateforme reste vide.
"""

from django.contrib import admin
from django.db.models import Count
from django.utils import timezone
from django.utils.html import format_html

from .models import AudioVersion, Series, Text, Theme


@admin.register(Theme)
class ThemeAdmin(admin.ModelAdmin):
    list_display = ("name_ht", "name_fr", "nb_textes", "order")
    list_editable = ("order",)
    search_fields = ("name_ht", "name_fr")
    prepopulated_fields = {"slug": ("name_ht",)}

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_n=Count("texts"))

    @admin.display(description="textes", ordering="_n")
    def nb_textes(self, obj):
        return obj._n


class AudioInline(admin.StackedInline):
    model = AudioVersion
    extra = 0
    fields = ("file", "duration", "published_at")
    verbose_name = "version audio"
    verbose_name_plural = "version audio (sa voix)"


class EpisodeInline(admin.TabularInline):
    """Le sommaire de la série, éditable en place."""

    model = Text
    extra = 0
    fields = ("episode_no", "title", "status", "published_at", "etat_public")
    readonly_fields = ("etat_public",)
    ordering = ("episode_no",)
    show_change_link = True

    @admin.display(description="visible ?")
    def etat_public(self, obj):
        return _badge(obj)


def _badge(obj):
    """Le seul indicateur qui compte : le public le voit-il, oui ou non ?"""
    if not obj.pk:
        return "—"
    if obj.is_live:
        return format_html('<b style="color:#1a7f37">● En ligne</b>')
    if obj.is_scheduled:
        reste = obj.published_at - timezone.now()
        jours, heures = reste.days, reste.seconds // 3600
        delai = f"dans {jours} j" if jours else f"dans {heures} h"
        return format_html(
            '<b style="color:#9a6700">◷ Programmé</b> — {} ({})',
            timezone.localtime(obj.published_at).strftime("%d/%m à %Hh%M"),
            delai,
        )
    return format_html('<span style="color:#6e7781">○ {}</span>', obj.get_status_display())


@admin.register(Series)
class SeriesAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "language", "status", "nb_episodes", "prochain")
    list_filter = ("kind", "status", "language")
    search_fields = ("title", "description")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [EpisodeInline]
    fieldsets = (
        (None, {"fields": ("title", "slug", "kind", "language", "status")}),
        ("Présentation", {"fields": ("description", "cover")}),
        (
            "Suspense",
            {
                "fields": ("reveal_titles",),
                "description": "Décoché, le public voit qu'un épisode arrive et quand, "
                               "mais pas son titre.",
            },
        ),
    )

    @admin.display(description="épisodes")
    def nb_episodes(self, obj):
        return f"{obj.texts.live().count()} / {obj.texts.count()}"

    @admin.display(description="prochain épisode")
    def prochain(self, obj):
        nxt = obj.next_episode
        if not nxt:
            return "—"
        return timezone.localtime(nxt.published_at).strftime("%d/%m/%Y à %Hh%M")


@admin.register(Text)
class TextAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "language", "serie_episode", "visible", "view_count")
    list_filter = ("kind", "language", "status", "themes", "series", "available_as_frame")
    search_fields = ("title", "body")
    prepopulated_fields = {"slug": ("title",)}
    filter_horizontal = ("themes",)
    date_hierarchy = "published_at"
    inlines = [AudioInline]
    readonly_fields = ("reading_time", "view_count", "visible", "created_at", "updated_at")
    actions = ["publier_maintenant"]

    fieldsets = (
        (None, {"fields": ("title", "slug", "kind", "language", "format")}),
        (
            "Le texte",
            {
                "fields": ("body", "excerpt"),
                "description": "Poésie : les retours à la ligne sont conservés à l'identique. "
                               "Entourez un mot d'astérisques <code>*comme ceci*</code> "
                               "pour l'afficher en or.",
            },
        ),
        ("Classement", {"fields": ("themes", "series", "episode_no")}),
        (
            "Publication",
            {
                "fields": ("status", "published_at", "visible"),
                "description": "Une date future programme la parution : le texte reste "
                               "invisible jusqu'au jour dit.",
            },
        ),
        ("Boutique", {"fields": ("available_as_frame",)}),
        (
            "Informations",
            {
                "classes": ("collapse",),
                "fields": ("reading_time", "view_count", "created_at", "updated_at"),
            },
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("series")

    @admin.display(description="visible ?")
    def visible(self, obj):
        return _badge(obj)

    @admin.display(description="série")
    def serie_episode(self, obj):
        if not obj.series:
            return "—"
        return f"{obj.series.title} · ép. {obj.episode_no or '?'}"

    @admin.action(description="Publier maintenant")
    def publier_maintenant(self, request, queryset):
        # Ordre par épisode : dans un lot mixte (plusieurs épisodes d'une même
        # série sélectionnés ensemble), publier le n°1 avant le n°2 évite de
        # buter inutilement sur le garde-fou d'ordre pour un simple problème
        # de séquence de traitement.
        n = deja_en_ligne = 0
        for texte in queryset.order_by("series_id", "episode_no", "created_at"):
            if texte.is_live:
                # Ne pas écraser published_at d'un texte déjà en ligne : ça
                # effacerait sa vraie date de parution et le ferait remonter
                # en tête du fil sans raison.
                deja_en_ligne += 1
                continue
            texte.status = "published"
            texte.published_at = timezone.now()
            try:
                texte.full_clean()
            except Exception as exc:
                self.message_user(request, f"« {texte.title} » : {exc}", level="ERROR")
                continue
            texte.save()
            n += 1
        if n:
            self.message_user(request, f"{n} texte(s) publié(s).")
        if deja_en_ligne:
            self.message_user(
                request,
                f"{deja_en_ligne} texte(s) déjà en ligne, non modifié(s).",
                level="WARNING",
            )


@admin.register(AudioVersion)
class AudioVersionAdmin(admin.ModelAdmin):
    list_display = ("text", "duration", "published_at")
    list_filter = ("published_at",)
    search_fields = ("text__title",)
