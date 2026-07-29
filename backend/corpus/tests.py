"""
Suite de tests — Jalon 0.

Chaque test reproduit un bug identifié dans ROADMAP.md avant de le corriger.
"""

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, TestCase
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
        response = self.client.get("/api/v1/openapi.json")
        self.assertEqual(response.status_code, 200)
        params = response.json()["paths"]["/api/v1/cheche"]["get"]["parameters"]
        q_params = [p for p in params if p["name"] == "q"]
        self.assertEqual(len(q_params), 1, f"paramètre q introuvable parmi {params}")
        self.assertEqual(q_params[0]["schema"].get("maxLength"), 200)


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
        response = self.client.get("/api/v1/openapi.json")
        self.assertEqual(response.status_code, 200)
        params = response.json()["paths"]["/api/v1/tex"]["get"]["parameters"]
        limit_params = [p for p in params if p["name"] == "limit"]
        self.assertEqual(
            len(limit_params), 1, f"paramètre limit introuvable parmi {params}"
        )
        self.assertEqual(limit_params[0]["schema"].get("maximum"), 100)
