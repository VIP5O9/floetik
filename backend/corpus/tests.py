"""
Suite de tests — Jalon 0.

Chaque test reproduit un bug identifié dans ROADMAP.md avant de le corriger.
"""

import json
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.contrib.auth import get_user_model
from django.test import (
    Client,
    RequestFactory,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .admin import TextAdmin
from .models import Kind, Language, Series, SeriesKind, Status, Text


def make_text(**kwargs):
    n = Text.objects.count() + 1
    defaults = dict(
        kind=Kind.POEM,
        language=Language.HT,
        status=Status.PUBLISHED,
        published_at=timezone.now() - timedelta(days=1),
        title=f"Tèks {n}",
        body="Yon vè\nYon lòt vè",
    )
    defaults.update(kwargs)
    return Text.objects.create(**defaults)


def _select_queries_on(ctx, table):
    return [
        q["sql"]
        for q in ctx.captured_queries
        if table in q["sql"] and q["sql"].strip().upper().startswith("SELECT")
    ]


class ListTextsPaginationTests(TestCase):
    def setUp(self):
        for _ in range(5):
            make_text()

    def test_limit_is_applied_in_sql_not_in_python(self):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get("/api/v1/tex", {"limit": 2})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["items"]), 2)
        self.assertEqual(body["count"], 5)

        text_queries = _select_queries_on(ctx, "corpus_text")
        self.assertTrue(text_queries, "no SELECT against corpus_text captured")
        self.assertTrue(
            any("LIMIT" in q.upper() for q in text_queries),
            f"expected a LIMIT clause on the corpus_text SELECT, got: {text_queries}",
        )

    def test_card_shape_unaffected_by_returning_the_queryset(self):
        response = self.client.get("/api/v1/tex", {"limit": 1})
        item = response.json()["items"][0]
        self.assertIn("has_audio", item)
        self.assertFalse(item["has_audio"])
        self.assertEqual(item["themes"], [])
        self.assertIsNone(item["series"])


@unittest.skipUnless(
    connection.vendor == "postgresql", "cheche() utilise unaccent/pg_trgm, indisponible hors PostgreSQL"
)
class SearchPaginationTests(TestCase):
    def setUp(self):
        for i in range(5):
            make_text(title=f"Lanmou {i}", body="pale de lanmou")

    def test_limit_is_applied_in_sql_not_in_python(self):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get("/api/v1/cheche", {"q": "lanmou", "limit": 2})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["items"]), 2)
        self.assertEqual(body["count"], 5)

        text_queries = _select_queries_on(ctx, "corpus_text")
        self.assertTrue(text_queries, "no SELECT against corpus_text captured")
        self.assertTrue(
            any("LIMIT" in q.upper() for q in text_queries),
            f"expected a LIMIT clause on the corpus_text SELECT, got: {text_queries}",
        )


class FullCleanOnSaveTests(TestCase):
    """Le garde-fou d'ordre des séries (Text.clean()) ne doit plus être
    contournable par un .save() direct, hors admin."""

    def setUp(self):
        self.series = Series.objects.create(
            title="Sezon 1", kind=SeriesKind.STORY_SERIES, language=Language.HT
        )

    def test_publishing_episode_2_without_episode_1_is_blocked(self):
        with self.assertRaises(ValidationError):
            Text.objects.create(
                kind=Kind.STORY,
                language=Language.HT,
                title="Epizòd 2",
                body="Kò tèks la",
                series=self.series,
                episode_no=2,
                status=Status.PUBLISHED,
                published_at=timezone.now() - timedelta(days=1),
            )

    def test_valid_episode_sequence_still_saves(self):
        make_text(
            kind=Kind.STORY,
            title="Epizòd 1",
            series=self.series,
            episode_no=1,
            published_at=timezone.now() - timedelta(days=2),
        )
        make_text(
            kind=Kind.STORY,
            title="Epizòd 2",
            series=self.series,
            episode_no=2,
            published_at=timezone.now() - timedelta(days=1),
        )
        self.assertEqual(self.series.texts.count(), 2)

    def test_standalone_text_without_series_still_saves(self):
        text = make_text()
        self.assertTrue(text.pk)


def make_series(n_live=0, n_scheduled=0, **kwargs):
    kwargs.setdefault("title", f"Seri {Series.objects.count() + 1}")
    kwargs.setdefault("kind", SeriesKind.STORY_SERIES)
    kwargs.setdefault("language", Language.HT)
    series = Series.objects.create(**kwargs)
    ep = 1
    for _ in range(n_live):
        make_text(
            kind=Kind.STORY,
            series=series,
            episode_no=ep,
            published_at=timezone.now() - timedelta(days=n_live - ep + 1),
        )
        ep += 1
    for _ in range(n_scheduled):
        make_text(
            kind=Kind.STORY,
            series=series,
            episode_no=ep,
            published_at=timezone.now() + timedelta(days=ep),
        )
        ep += 1
    return series


class SeriesListNPlusOneTests(TestCase):
    def test_query_count_does_not_grow_with_series_count(self):
        make_series(n_live=2, n_scheduled=1)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get("/api/v1/seri")
        baseline = len(ctx.captured_queries)

        for _ in range(5):
            make_series(n_live=2, n_scheduled=1)

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get("/api/v1/seri")
        self.assertEqual(len(ctx.captured_queries), baseline)
        self.assertEqual(len(response.json()), 6)

    def test_episode_count_and_next_episode_at_are_correct(self):
        series = make_series(n_live=2, n_scheduled=1)
        response = self.client.get("/api/v1/seri")
        body = next(s for s in response.json() if s["slug"] == series.slug)
        self.assertEqual(body["episode_count"], 2)
        self.assertIsNotNone(body["next_episode_at"])


class RandomTextTests(TestCase):
    def test_empty_corpus_returns_404(self):
        response = self.client.get("/api/v1/aza")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Corpus vide")

    def test_single_text_is_returned(self):
        text = make_text()
        response = self.client.get("/api/v1/aza")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["slug"], text.slug)

    def test_only_live_texts_are_eligible(self):
        make_text(status=Status.DRAFT, published_at=None)
        make_text(
            status=Status.PUBLISHED, published_at=timezone.now() + timedelta(days=1)
        )
        live = make_text()
        for _ in range(10):
            response = self.client.get("/api/v1/aza")
            self.assertEqual(response.json()["slug"], live.slug)

    def test_no_full_table_order_by_random(self):
        for _ in range(5):
            make_text()
        with CaptureQueriesContext(connection) as ctx:
            self.client.get("/api/v1/aza")
        text_queries = _select_queries_on(ctx, "corpus_text")
        self.assertTrue(text_queries)
        # SQLite rend order_by("?") en "ORDER BY RAND()", PostgreSQL en
        # "ORDER BY RANDOM()" — les deux contiennent "RAND".
        self.assertFalse(
            any("RAND" in q.upper() for q in text_queries),
            f"expected no ORDER BY RAND(OM)() scan, got: {text_queries}",
        )


class ViewCountTests(TestCase):
    REAL_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

    def setUp(self):
        cache.clear()

    def test_first_view_increments(self):
        text = make_text()
        response = self.client.get(
            f"/api/v1/tex/{text.slug}", HTTP_USER_AGENT=self.REAL_UA, REMOTE_ADDR="203.0.113.5"
        )
        self.assertEqual(response.json()["view_count"], 1)

    def test_repeated_view_same_ip_does_not_increment_again(self):
        text = make_text()
        self.client.get(
            f"/api/v1/tex/{text.slug}", HTTP_USER_AGENT=self.REAL_UA, REMOTE_ADDR="203.0.113.5"
        )
        response = self.client.get(
            f"/api/v1/tex/{text.slug}", HTTP_USER_AGENT=self.REAL_UA, REMOTE_ADDR="203.0.113.5"
        )
        self.assertEqual(response.json()["view_count"], 1)

    def test_different_ip_counts_as_a_separate_view(self):
        text = make_text()
        self.client.get(
            f"/api/v1/tex/{text.slug}", HTTP_USER_AGENT=self.REAL_UA, REMOTE_ADDR="203.0.113.5"
        )
        response = self.client.get(
            f"/api/v1/tex/{text.slug}", HTTP_USER_AGENT=self.REAL_UA, REMOTE_ADDR="198.51.100.7"
        )
        self.assertEqual(response.json()["view_count"], 2)

    def test_known_bot_user_agent_never_increments(self):
        text = make_text()
        response = self.client.get(
            f"/api/v1/tex/{text.slug}",
            HTTP_USER_AGENT="Googlebot/2.1 (+http://www.google.com/bot.html)",
            REMOTE_ADDR="203.0.113.5",
        )
        self.assertEqual(response.json()["view_count"], 0)

    def test_missing_user_agent_never_increments(self):
        text = make_text()
        response = self.client.get(f"/api/v1/tex/{text.slug}", REMOTE_ADDR="203.0.113.5")
        self.assertEqual(response.json()["view_count"], 0)


class ExportCorpusTests(TestCase):
    def _run_export(self, **opts):
        tmpdir = tempfile.mkdtemp()
        call_command("export_corpus", out=tmpdir, **opts)
        stamp_dirs = list(Path(tmpdir).iterdir())
        self.assertEqual(len(stamp_dirs), 1, f"expected one stamp dir, got {stamp_dirs}")
        root = stamp_dirs[0]
        data = json.loads((root / "corpus.json").read_text(encoding="utf-8"))
        return root, data

    def test_published_only_excludes_unpublished_episodes_from_series_summary(self):
        series = make_series(n_live=1, n_scheduled=1)
        _, data = self._run_export(published_only=True)
        entry = next(s for s in data["series"] if s["slug"] == series.slug)
        self.assertEqual(len(entry["episodes"]), 1)

    def test_without_published_only_series_summary_includes_all_episodes(self):
        series = make_series(n_live=1, n_scheduled=1)
        _, data = self._run_export(published_only=False)
        entry = next(s for s in data["series"] if s["slug"] == series.slug)
        self.assertEqual(len(entry["episodes"]), 2)

    def test_title_with_colon_does_not_break_front_matter(self):
        text = make_text(title="Lèt pou ou: dènye mo")
        root, _ = self._run_export(published_only=False)
        front_matter = (root / "tex" / f"{text.slug}.md").read_text(encoding="utf-8")
        title_line = front_matter.splitlines()[1]
        self.assertEqual(title_line, f"titre: {json.dumps(text.title)}")

    def test_exporte_le_is_timezone_aware(self):
        make_text()
        _, data = self._run_export(published_only=False)
        self.assertRegex(data["exporte_le"], r"[+-]\d{2}:\d{2}$")


class SeriesVisibilityTests(TestCase):
    def test_series_with_only_a_draft_episode_is_not_listed(self):
        series = Series.objects.create(
            title="Sezon fantom", kind=SeriesKind.STORY_SERIES, language=Language.HT
        )
        Text.objects.create(
            kind=Kind.STORY,
            language=Language.HT,
            title="Epizòd 1",
            body="Kò tèks la",
            series=series,
            episode_no=1,
            status=Status.DRAFT,
        )
        response = self.client.get("/api/v1/seri")
        self.assertNotIn(series.slug, [s["slug"] for s in response.json()])

    def test_series_with_no_episodes_at_all_is_not_listed(self):
        series = Series.objects.create(
            title="Sezon vid", kind=SeriesKind.STORY_SERIES, language=Language.HT
        )
        response = self.client.get("/api/v1/seri")
        self.assertNotIn(series.slug, [s["slug"] for s in response.json()])

    def test_draft_only_series_detail_is_404(self):
        series = Series.objects.create(
            title="Sezon kache", kind=SeriesKind.STORY_SERIES, language=Language.HT
        )
        Text.objects.create(
            kind=Kind.STORY,
            language=Language.HT,
            title="Epizòd 1",
            body="Kò tèks la",
            series=series,
            episode_no=1,
            status=Status.DRAFT,
        )
        response = self.client.get(f"/api/v1/seri/{series.slug}")
        self.assertEqual(response.status_code, 404)

    def test_series_with_a_scheduled_episode_is_listed(self):
        series = make_series(n_live=0, n_scheduled=1)
        response = self.client.get("/api/v1/seri")
        self.assertIn(series.slug, [s["slug"] for s in response.json()])


class PublierMaintenantTests(TestCase):
    def setUp(self):
        self.admin = TextAdmin(Text, AdminSite())
        self.factory = RequestFactory()

    def _request(self):
        request = self.factory.post("/admin/corpus/text/")
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def test_already_live_text_is_not_rewritten(self):
        original_published_at = timezone.now() - timedelta(days=5)
        live = make_text(published_at=original_published_at)
        self.admin.publier_maintenant(self._request(), Text.objects.filter(pk=live.pk))
        live.refresh_from_db()
        self.assertEqual(live.published_at, original_published_at)

    def test_draft_text_gets_published_now(self):
        draft = make_text(status=Status.DRAFT, published_at=None)
        self.admin.publier_maintenant(self._request(), Text.objects.filter(pk=draft.pk))
        draft.refresh_from_db()
        self.assertEqual(draft.status, Status.PUBLISHED)
        self.assertIsNotNone(draft.published_at)

    def test_out_of_order_batch_selection_still_publishes_in_episode_order(self):
        series = Series.objects.create(
            title="Sezon lòd", kind=SeriesKind.STORY_SERIES, language=Language.HT
        )
        # Créés dans l'ordre chronologique normal (ép. 1 puis ép. 2) : sans tri
        # explicite par episode_no, l'ordre par défaut du modèle (-created_at)
        # traiterait ép. 2 en premier et buterait sur le garde-fou d'ordre.
        ep1 = Text.objects.create(
            kind=Kind.STORY, language=Language.HT, title="Epizòd 1", body="b",
            series=series, episode_no=1, status=Status.DRAFT,
        )
        ep2 = Text.objects.create(
            kind=Kind.STORY, language=Language.HT, title="Epizòd 2", body="b",
            series=series, episode_no=2, status=Status.DRAFT,
        )
        qs = Text.objects.filter(pk__in=[ep1.pk, ep2.pk])
        self.admin.publier_maintenant(self._request(), qs)
        ep1.refresh_from_db()
        ep2.refresh_from_db()
        self.assertEqual(ep1.status, Status.PUBLISHED)
        self.assertEqual(ep2.status, Status.PUBLISHED)


class EpisodeSansDateTests(TestCase):
    """Un épisode « publié » sans date de parution n'est jamais servi (is_live
    reste faux à jamais). La suite ne doit donc pas pouvoir paraître devant lui :
    le lecteur aurait le deuxième épisode et pas le premier."""

    def setUp(self):
        self.series = Series.objects.create(
            title="Lapli", kind=SeriesKind.STORY_SERIES, language=Language.HT
        )

    def test_sequel_cannot_be_published_while_the_opener_has_no_date(self):
        ouverture = make_text(
            kind=Kind.STORY,
            title="Lapli epizòd 1",
            series=self.series,
            episode_no=1,
            published_at=timezone.now() - timedelta(days=2),
        )
        # Date effacée hors save() — bulk update, import, migration de données.
        Text.objects.filter(pk=ouverture.pk).update(published_at=None)
        self.assertEqual(
            self.client.get(f"/api/v1/tex/{ouverture.slug}").status_code,
            404,
            "l'ouverture sans date ne devrait pas être servie",
        )

        with self.assertRaises(ValidationError):
            make_text(
                kind=Kind.STORY,
                title="Lapli epizòd 2",
                series=self.series,
                episode_no=2,
                published_at=timezone.now() - timedelta(days=1),
            )

        sommaire = self.client.get(f"/api/v1/seri/{self.series.slug}").json()
        self.assertEqual(
            [e["episode_no"] for e in sommaire["episodes"] if e["is_available"]], []
        )


class EpisodeZeroTests(TestCase):
    """La numérotation des épisodes commence à 1. Un « épisode 0 » — le prologue
    d'un romancier — est refusé plutôt qu'ignoré : sinon il traverse les deux
    conditions d'entrée du garde-fou (0 est faux) et paraît hors de tout ordre."""

    def test_a_text_numbered_zero_cannot_be_saved_without_a_series(self):
        with self.assertRaises(ValidationError):
            make_text(title="Pwològ", episode_no=0)

    def test_a_prologue_numbered_zero_never_reaches_the_sommaire(self):
        series = Series.objects.create(
            title="Sezon pwològ", kind=SeriesKind.STORY_SERIES, language=Language.HT
        )
        make_text(
            kind=Kind.STORY,
            title="Epizòd 5",
            series=series,
            episode_no=5,
            status=Status.DRAFT,
            published_at=None,
        )
        with self.assertRaises(ValidationError):
            make_text(
                kind=Kind.STORY,
                title="Pwològ",
                series=series,
                episode_no=0,
                published_at=timezone.now() + timedelta(days=99),
            )
        self.assertEqual(self.client.get(f"/api/v1/seri/{series.slug}").status_code, 404)


class SuppressionSerieTests(TestCase):
    """Supprimer une série détache ses textes : la série passe à NULL (SET_NULL),
    le n° d'épisode doit suivre. Sinon le public lit « épisode 1 de rien », et la
    fiche devient impossible à réenregistrer depuis l'admin."""

    def setUp(self):
        self.series = Series.objects.create(
            title="Woman ki disparèt", kind=SeriesKind.STORY_SERIES, language=Language.HT
        )
        self.chapitre = make_text(
            kind=Kind.STORY,
            title="Chapit youn",
            series=self.series,
            episode_no=1,
            published_at=timezone.now() - timedelta(days=1),
        )

    def test_deleted_series_is_never_served_as_episode_of_nothing(self):
        self.series.delete()
        body = self.client.get(f"/api/v1/tex/{self.chapitre.slug}").json()
        self.assertEqual(body["title"], "Chapit youn")
        self.assertIsNone(body["series"])
        self.assertIsNone(body["episode_no"])

    def test_text_can_still_be_edited_after_its_series_is_deleted(self):
        self.series.delete()
        orphelin = Text.objects.get(pk=self.chapitre.pk)
        orphelin.title = "Chapit youn — revize"
        orphelin.save()
        self.assertEqual(
            Text.objects.get(pk=self.chapitre.pk).title, "Chapit youn — revize"
        )

    def test_bulk_deletion_of_series_detaches_its_texts_too(self):
        Series.objects.filter(pk=self.series.pk).delete()
        self.assertIsNone(Text.objects.get(pk=self.chapitre.pk).episode_no)


class RelatedTextsLimitTests(TestCase):
    def setUp(self):
        self.text = make_text()

    def test_negative_limit_is_rejected_not_a_500(self):
        response = self.client.get(f"/api/v1/tex/{self.text.slug}/vwazen", {"limit": -1})
        self.assertEqual(response.status_code, 422)

    def test_oversized_limit_is_rejected(self):
        response = self.client.get(f"/api/v1/tex/{self.text.slug}/vwazen", {"limit": 1000000})
        self.assertEqual(response.status_code, 422)

    def test_default_limit_still_works(self):
        response = self.client.get(f"/api/v1/tex/{self.text.slug}/vwazen")
        self.assertEqual(response.status_code, 200)


@unittest.skipUnless(
    connection.vendor == "postgresql", "cheche() utilise unaccent/pg_trgm, indisponible hors PostgreSQL"
)
class SearchQueryLengthTests(TestCase):
    """`q` est interpolé dans six prédicats SQL : son coût est en O(len(q) × lignes).
    Sans borne, un simple GET anonyme immobilise un backend Postgres plusieurs
    minutes. La borne est de 200 caractères — littéral écrit à la main ici."""

    def setUp(self):
        make_text(title="Lanmou", body="pale de lanmou")

    def test_query_of_exactly_200_chars_is_accepted(self):
        response = self.client.get("/api/v1/cheche", {"q": "a" * 200})
        self.assertEqual(response.status_code, 200)

    def test_query_of_201_chars_is_rejected(self):
        response = self.client.get("/api/v1/cheche", {"q": "a" * 201})
        self.assertEqual(response.status_code, 422)

    def test_single_character_query_still_returns_an_empty_result(self):
        """Le plancher existant (moins de 2 caractères → rien) survit au plafond :
        trop court reste un 200 vide, seul le trop long est refusé."""
        response = self.client.get("/api/v1/cheche", {"q": "a"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])

    def test_maximum_is_published_in_the_openapi_schema(self):
        response = _client_staff(self.client).get("/api/v1/openapi.json")
        self.assertEqual(response.status_code, 200)
        params = response.json()["paths"]["/api/v1/cheche"]["get"]["parameters"]
        q_params = [p for p in params if p["name"] == "q"]
        self.assertEqual(len(q_params), 1, f"paramètre q introuvable parmi {params}")
        self.assertEqual(q_params[0]["schema"].get("maxLength"), 200)


def _client_staff(client):
    """Depuis le durcissement (#5), le schéma OpenAPI n'est plus public : il faut
    être membre du personnel pour le lire. Les tests qui vérifient que les bornes
    sont *publiées* dans le schéma doivent donc s'authentifier — la borne elle-même
    reste vérifiée sans authentification par les tests 422 voisins."""
    Utilisateur = get_user_model()
    Utilisateur.objects.filter(username="schema-lecteur").delete()
    personnel = Utilisateur.objects.create_user(
        username="schema-lecteur", password="mot-de-passe-de-test-long", is_staff=True
    )
    client.force_login(personnel)
    return client


class PaginationCeilingTests(TestCase):
    """`?limit` sans plafond amplifie toute attaque et fait surchercher la base.
    Le plafond est de 100 lignes par page — littéral écrit à la main ici."""

    def setUp(self):
        make_text()

    def test_limit_of_exactly_100_is_accepted(self):
        response = self.client.get("/api/v1/tex", {"limit": 100})
        self.assertEqual(response.status_code, 200)

    def test_limit_of_101_is_rejected(self):
        response = self.client.get("/api/v1/tex", {"limit": 101})
        self.assertEqual(response.status_code, 422)

    def test_maxint_limit_is_rejected(self):
        response = self.client.get("/api/v1/tex", {"limit": 2147483647})
        self.assertEqual(response.status_code, 422)

    def test_ceiling_is_published_in_the_openapi_schema(self):
        response = _client_staff(self.client).get("/api/v1/openapi.json")
        self.assertEqual(response.status_code, 200)
        params = response.json()["paths"]["/api/v1/tex"]["get"]["parameters"]
        limit_params = [p for p in params if p["name"] == "limit"]
        self.assertEqual(
            len(limit_params), 1, f"paramètre limit introuvable parmi {params}"
        )
        self.assertEqual(limit_params[0]["schema"].get("maximum"), 100)
class SlugTranslitterationTests(TestCase):
    """Certains alphabets gonflent à la translittération : « ж » devient « zh »,
    « 龍 » devient « long ». Un titre de 200 caractères peut produire un slug de
    999 caractères, que la colonne (220) ne peut pas accueillir."""

    def test_titre_cyrillique_de_200_caracteres_produit_un_slug_valide(self):
        texte = make_text(title="ж" * 200)
        self.assertTrue(texte.slug)
        self.assertLessEqual(len(texte.slug), 220)
        self.assertEqual(Text.objects.get(slug=texte.slug).pk, texte.pk)

    def test_deux_titres_cjk_identiques_recoivent_des_slugs_courts_et_distincts(self):
        premier = make_text(title="龍" * 200)
        second = make_text(title="龍" * 200)
        self.assertNotEqual(premier.slug, second.slug)
        self.assertLessEqual(len(second.slug), 220)


def payload_admin_texte(**kwargs):
    """Le POST que le navigateur envoie sur /admin/corpus/text/add/."""
    donnees = {
        "kind": Kind.POEM,
        "language": Language.HT,
        "format": "",
        "title": "Yon tit",
        "slug": "",
        "body": "Yon vè\nYon lòt vè",
        "excerpt": "",
        "themes": [],
        "series": "",
        "episode_no": "",
        "status": Status.DRAFT,
        "published_at_0": "",
        "published_at_1": "",
        "audio-TOTAL_FORMS": "0",
        "audio-INITIAL_FORMS": "0",
        "audio-MIN_NUM_FORMS": "0",
        "audio-MAX_NUM_FORMS": "1",
    }
    donnees.update(kwargs)
    return donnees


class AdminEnregistrementTexteTests(TestCase):
    """Le seam public de l'admin : POST sur /admin/corpus/text/add/.
    Une erreur de formulaire est acceptable, une 500 nue ne l'est jamais."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="florentz", email="f@example.com", password="mot-de-passe-test"
        )
        self.client.force_login(self.user)

    def test_titre_cjk_de_200_caracteres_ne_provoque_pas_de_500(self):
        response = self.client.post(
            "/admin/corpus/text/add/", payload_admin_texte(title="龍" * 200)
        )
        self.assertIn(response.status_code, (200, 302), "500 nue sur l'ajout d'un texte")
        self.assertEqual(response.status_code, 302, "le texte aurait dû être créé")
        texte = Text.objects.get()
        self.assertTrue(texte.slug)
        self.assertLessEqual(len(texte.slug), 220)


class SlugHomonymesTests(TestCase):
    """Le choix du slug ne doit pas coûter une requête par homonyme : sinon
    enregistrer devient de plus en plus lent à mesure que le corpus grossit."""

    def _requetes_pour_creer_un_homonyme(self):
        with CaptureQueriesContext(connection) as ctx:
            make_text(title="Sanzatann")
        nb = len(ctx.captured_queries)
        self.assertGreater(nb, 0, "aucune requête capturée : le compteur ne mesure rien")
        return nb

    def test_le_nombre_de_requetes_ne_croit_pas_avec_le_nombre_dhomonymes(self):
        for _ in range(3):
            make_text(title="Sanzatann")
        avec_3 = self._requetes_pour_creer_un_homonyme()

        for _ in range(30):
            make_text(title="Sanzatann")
        avec_34 = self._requetes_pour_creer_un_homonyme()

        self.assertEqual(
            avec_34,
            avec_3,
            f"coût du slug linéaire en homonymes : {avec_3} requêtes avec 3 homonymes, "
            f"{avec_34} avec 34",
        )

    def test_les_homonymes_recoivent_des_slugs_distincts_et_numerotes(self):
        premier = make_text(title="Sanzatann")
        deuxieme = make_text(title="Sanzatann")
        troisieme = make_text(title="Sanzatann")
        self.assertEqual(premier.slug, "sanzatann")
        self.assertEqual(deuxieme.slug, "sanzatann-2")
        self.assertEqual(troisieme.slug, "sanzatann-3")


class SlugConcurrenceTests(TransactionTestCase):
    """Un « Enregistrer » double-cliqué, ou deux onglets d'admin ouverts, font
    deux INSERT simultanés avec le même slug calculé.

    TransactionTestCase (et non TestCase) est indispensable : chaque thread a sa
    propre connexion et doit voir les COMMIT des autres, ce que l'isolation en
    transaction unique de TestCase interdit.
    """

    NB_THREADS = 12

    def test_saves_concurrents_du_meme_titre_ne_lèvent_aucune_erreur(self):
        barriere = threading.Barrier(self.NB_THREADS)
        resultats = []
        verrou = threading.Lock()

        def creer():
            barriere.wait()  # tous les threads partent vraiment en même temps
            try:
                issue = ("ok", make_text(title="Menm tit la").slug)
            except Exception as exc:
                issue = ("erreur", f"{type(exc).__name__}: {exc}")
            finally:
                connection.close()
            with verrou:
                resultats.append(issue)

        threads = [threading.Thread(target=creer) for _ in range(self.NB_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(
            len(resultats), self.NB_THREADS, "des threads n'ont rien rapporté"
        )
        erreurs = [message for issue, message in resultats if issue == "erreur"]
        self.assertEqual(erreurs, [], f"enregistrements en échec : {erreurs}")
        slugs = {valeur for issue, valeur in resultats if issue == "ok"}
        self.assertEqual(len(slugs), self.NB_THREADS, f"slugs non distincts : {slugs}")
        self.assertEqual(Text.objects.count(), self.NB_THREADS)

    def test_ajouts_concurrents_du_meme_titre_dans_ladmin_ne_rendent_jamais_500(self):
        User = get_user_model()
        user = User.objects.create_superuser(
            username="florentz", email="f@example.com", password="mot-de-passe-test"
        )
        barriere = threading.Barrier(self.NB_THREADS)
        codes = []
        verrou = threading.Lock()

        def poster():
            client = Client()
            client.force_login(user)
            donnees = payload_admin_texte(title="Menm tit la")
            barriere.wait()
            try:
                code = client.post("/admin/corpus/text/add/", donnees).status_code
            except Exception as exc:
                code = f"{type(exc).__name__}: {exc}"
            finally:
                connection.close()
            with verrou:
                codes.append(code)

        threads = [threading.Thread(target=poster) for _ in range(self.NB_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(codes), self.NB_THREADS, "des threads n'ont rien rapporté")
        self.assertEqual(
            [c for c in codes if c != 302], [], f"réponses inattendues : {codes}"
        )
        self.assertEqual(Text.objects.count(), self.NB_THREADS)
        self.assertEqual(
            Text.objects.values("slug").distinct().count(), self.NB_THREADS
        )


class SlugStabiliteTests(TestCase):
    """Un slug est une URL publique : une fois attribué, il ne bouge plus."""

    def test_renommer_un_texte_ne_change_pas_son_slug(self):
        texte = make_text(title="Lanmou nan lapli")
        self.assertEqual(texte.slug, "lanmou-nan-lapli")
        texte.title = "Yon lòt tit"
        texte.save()
        texte.refresh_from_db()
        self.assertEqual(texte.slug, "lanmou-nan-lapli")

    def test_le_slug_saisi_a_la_main_est_respecte(self):
        texte = make_text(title="Sanzatann", slug="chwazi-alamen")
        self.assertEqual(texte.slug, "chwazi-alamen")
# ---------------------------------------------------------------------------
# Jalon 1 — durcissement de la production (issue #5)
# ---------------------------------------------------------------------------


def _boot_settings_in_isolation(extra_env, tmpdir):
    """Importe le module de réglages dans un sous-processus isolé.

    Le paquet `config` est recopié dans `tmpdir` pour que son BASE_DIR pointe
    sur un répertoire sans fichier `.env` : les variables d'environnement du
    sous-processus sont alors la seule source de configuration, ce qui permet
    d'observer un démarrage avec SECRET_KEY réellement absente.
    """
    import config
    import os
    import shutil
    import subprocess
    import sys

    source = Path(config.__file__).resolve().parent
    # Trace explicite : si un jour ce test lisait un autre arbre que celui sous
    # test, la sortie du runner le dirait tout de suite.
    print(f"[test] paquet config sous test : {source}")
    assert (source / "settings.py").is_file(), f"settings.py introuvable dans {source}"

    copie = Path(tmpdir) / "config"
    shutil.copytree(source, copie, ignore=shutil.ignore_patterns("__pycache__"))
    assert not (Path(tmpdir) / ".env").exists()

    backend = source.parent
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"SECRET_KEY", "DEBUG", "ALLOWED_HOSTS", "DATABASE_URL"}
    }
    env["PYTHONPATH"] = os.pathsep.join([str(tmpdir), str(backend)])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", "import config.settings"],
        cwd=tmpdir,
        env=env,
        capture_output=True,
        text=True,
    )


class SecretKeyObligatoireTests(unittest.TestCase):
    def test_demarrage_sans_secret_key_echoue_avec_un_message_francais(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _boot_settings_in_isolation({}, tmpdir)
        self.assertNotEqual(
            result.returncode, 0, f"le démarrage a réussi sans SECRET_KEY : {result.stdout}"
        )
        self.assertIn("SECRET_KEY", result.stderr)
        self.assertIn("ImproperlyConfigured", result.stderr)

    def test_demarrage_avec_secret_key_reussit(self):
        """Contrôle : sans ce test, le précédent passerait sur n'importe quelle erreur."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _boot_settings_in_isolation({"SECRET_KEY": "une-vraie-cle"}, tmpdir)
        self.assertEqual(result.returncode, 0, result.stderr)


@override_settings(DEBUG=False)
class DocsNonPubliquesTests(TestCase):
    """La documentation décrit toute la surface de l'API : elle n'est pas publique."""

    def test_docs_anonyme_est_refuse(self):
        response = self.client.get("/api/v1/docs")
        self.assertNotEqual(response.status_code, 200)

    def test_openapi_json_anonyme_est_refuse(self):
        response = self.client.get("/api/v1/openapi.json")
        self.assertNotEqual(response.status_code, 200)

    def test_docs_restent_accessibles_a_florentz(self):
        """Contrôle : la protection ne doit pas condamner la porte pour l'auteur."""
        User = get_user_model()
        User.objects.create_superuser(username="florentz", password="mot-de-passe-tres-long")
        self.client.force_login(User.objects.get(username="florentz"))
        self.assertEqual(self.client.get("/api/v1/docs").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/openapi.json").status_code, 200)


class AdminLoginFreineTests(TestCase):
    """Le site n'a qu'un seul compte : deviner son mot de passe doit coûter cher."""

    URL = "/admin/login/"
    # Seuil fixé par la configuration : 5 échecs, puis la porte se ferme.
    LIMITE = 5

    def setUp(self):
        User = get_user_model()
        self.mot_de_passe = "yon-mo-pas-ki-long-anpil"
        User.objects.create_superuser(username="florentz", password=self.mot_de_passe)

    def _essai(self, mot_de_passe):
        return self.client.post(
            self.URL, {"username": "florentz", "password": mot_de_passe}
        )

    def test_apres_cinq_echecs_le_bon_mot_de_passe_ne_passe_plus(self):
        for _ in range(self.LIMITE):
            self._essai("mauvais")
        reponse = self._essai(self.mot_de_passe)
        self.assertNotIn("_auth_user_id", self.client.session)
        # 429 « Too Many Requests » : la bonne sémantique pour un freinage.
        self.assertEqual(reponse.status_code, 429)

    def test_un_echec_isole_ne_bloque_pas_florentz(self):
        """Contrôle : la serrure ne doit pas se refermer sur l'auteur."""
        self._essai("mauvais")
        reponse = self._essai(self.mot_de_passe)
        self.assertEqual(reponse.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_un_echec_laisse_une_trace_dans_les_journaux(self):
        with self.assertLogs("axes", level="WARNING") as journaux:
            self._essai("mauvais")
        self.assertTrue(
            any("florentz" in ligne for ligne in journaux.output),
            f"aucune trace nommant le compte visé : {journaux.output}",
        )


class JournalDeProductionTests(unittest.TestCase):
    """Un 500 en production doit laisser une trace qu'on peut relire demain."""

    SCRIPT = (
        "import os, logging, django\n"
        "os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'\n"
        "django.setup()\n"
        "logging.getLogger('django.request').error('BOUM-TEST-500')\n"
        "logging.shutdown()\n"
    )

    def test_une_erreur_serveur_atterrit_dans_un_fichier_meme_avec_debug_false(self):
        import os
        import subprocess
        import sys

        import config

        source = Path(config.__file__).resolve().parent
        print(f"[test] paquet config sous test : {source}")

        with tempfile.TemporaryDirectory() as tmpdir:
            import shutil

            shutil.copytree(
                source, Path(tmpdir) / "config", ignore=shutil.ignore_patterns("__pycache__")
            )
            journaux = Path(tmpdir) / "journaux"
            env = dict(os.environ)
            env.update(
                SECRET_KEY="une-vraie-cle",
                DEBUG="False",
                LOG_DIR=str(journaux),
                PYTHONPATH=os.pathsep.join([tmpdir, str(source.parent)]),
                PYTHONDONTWRITEBYTECODE="1",
            )
            result = subprocess.run(
                [sys.executable, "-c", self.SCRIPT],
                cwd=tmpdir,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            fichiers = sorted(journaux.glob("*")) if journaux.is_dir() else []
            self.assertTrue(
                fichiers, f"aucun fichier de journal créé dans {journaux}"
            )
            contenu = "\n".join(f.read_text() for f in fichiers if f.is_file())
        self.assertIn("BOUM-TEST-500", contenu)


def _reglages_isoles(extra_env):
    """Retourne les réglages de sécurité tels qu'ils seraient chargés au boot.

    Passe par un sous-processus : DEBUG est figé au moment de l'import du module
    de réglages, un override_settings ne dirait rien de la vraie configuration.
    """
    import os
    import shutil
    import subprocess
    import sys

    import config

    source = Path(config.__file__).resolve().parent
    print(f"[test] paquet config sous test : {source}")
    noms = [
        "SECURE_SSL_REDIRECT",
        "SECURE_HSTS_SECONDS",
        "SECURE_HSTS_INCLUDE_SUBDOMAINS",
        "SECURE_HSTS_PRELOAD",
        "SESSION_COOKIE_SECURE",
        "CSRF_COOKIE_SECURE",
        "SECURE_PROXY_SSL_HEADER",
        "CSRF_TRUSTED_ORIGINS",
    ]
    script = (
        "import json, config.settings as s\n"
        f"noms = {noms!r}\n"
        "manquants = [n for n in noms if not hasattr(s, n)]\n"
        "print(json.dumps({'manquants': manquants, "
        "'valeurs': {n: getattr(s, n, None) for n in noms}}))\n"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        shutil.copytree(
            source, Path(tmpdir) / "config", ignore=shutil.ignore_patterns("__pycache__")
        )
        env = {k: v for k, v in os.environ.items() if k not in {"DEBUG", "SECRET_KEY"}}
        env.update(
            SECRET_KEY="une-vraie-cle",
            LOG_DIR=str(Path(tmpdir) / "journaux"),
            PYTHONPATH=os.pathsep.join([tmpdir, str(source.parent)]),
            PYTHONDONTWRITEBYTECODE="1",
        )
        env.update(extra_env)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=tmpdir, env=env, capture_output=True, text=True,
        )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


class ReglagesHttpsTests(unittest.TestCase):
    def test_en_production_le_site_est_strict(self):
        lu = _reglages_isoles({"DEBUG": "False"})
        self.assertEqual(lu["manquants"], [])
        v = lu["valeurs"]
        self.assertTrue(v["SECURE_SSL_REDIRECT"])
        self.assertEqual(v["SECURE_HSTS_SECONDS"], 31536000)  # un an
        self.assertTrue(v["SECURE_HSTS_INCLUDE_SUBDOMAINS"])
        self.assertTrue(v["SECURE_HSTS_PRELOAD"])
        self.assertTrue(v["SESSION_COOKIE_SECURE"])
        self.assertTrue(v["CSRF_COOKIE_SECURE"])
        self.assertEqual(
            v["SECURE_PROXY_SSL_HEADER"], ["HTTP_X_FORWARDED_PROTO", "https"]
        )

    def test_en_developpement_le_site_reste_utilisable_en_http(self):
        lu = _reglages_isoles({"DEBUG": "True"})
        v = lu["valeurs"]
        self.assertFalse(v["SECURE_SSL_REDIRECT"])
        self.assertEqual(v["SECURE_HSTS_SECONDS"], 0)
        self.assertFalse(v["SESSION_COOKIE_SECURE"])
        self.assertFalse(v["CSRF_COOKIE_SECURE"])

    def test_les_origines_csrf_de_confiance_viennent_de_l_environnement(self):
        lu = _reglages_isoles(
            {"DEBUG": "False", "CSRF_TRUSTED_ORIGINS": "https://floetik.ht"}
        )
        self.assertEqual(lu["valeurs"]["CSRF_TRUSTED_ORIGINS"], ["https://floetik.ht"])
