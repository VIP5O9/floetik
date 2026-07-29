"""
Suite de tests — Jalon 0.

Chaque test reproduit un bug identifié dans ROADMAP.md avant de le corriger.
"""

import unittest
from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

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
