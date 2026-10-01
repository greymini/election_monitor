"""News visibility per constituency, end to end through the crawl and the API."""

from __future__ import annotations

import pytest

from tests.e2e.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def crawled(db_url, monkeypatch, conn):
    monkeypatch.setenv("DATABASE_URL", db_url)
    from common.config import get_settings
    from common.db import close_pools

    get_settings.cache_clear()
    close_pools()
    from news import crawl_rss

    with conn.cursor() as cur:
        cur.execute("INSERT INTO news_source (name, url, kind, lang, is_active) "
                    "VALUES ('test-feed', 'https://feed.test/rss', 'rss', 'hi', true) "
                    "ON CONFLICT DO NOTHING")
    items = [
        {"url": "https://feed.test/a", "title": "गिरिडीह में नई सड़क का उद्घाटन",
         "summary": "", "published": None},
        {"url": "https://feed.test/b", "title": "Tundi panchayat meeting on water",
         "summary": "", "published": None},
    ]
    monkeypatch.setattr(crawl_rss, "fetch_feed", lambda *a, **k: items)
    stats = crawl_rss.crawl()
    yield stats
    with conn.cursor() as cur:
        cur.execute("DELETE FROM news_item WHERE url LIKE 'https://feed.test/%'")
        cur.execute("DELETE FROM news_source WHERE name = 'test-feed'")
    close_pools()


def _news(client, tokens, ac_number, **params):
    return client.get(f"/acs/{ac_number}/news", params=params,
                      headers={"Authorization": f"Bearer {tokens['admin']}"}).json()


def test_a_crawled_item_appears_on_its_constituency_only(crawled, client, tokens):
    assert crawled["inserted"] >= 2
    giridih = {r["title"] for r in _news(client, tokens, 32, include_unlabelled=True)["rows"]}
    tundi = {r["title"] for r in _news(client, tokens, 42, include_unlabelled=True)["rows"]}
    assert "गिरिडीह में नई सड़क का उद्घाटन" in giridih
    assert "Tundi panchayat meeting on water" in tundi
    assert "Tundi panchayat meeting on water" not in giridih


def test_issue_trends_count_only_this_constituency(conn, client, tokens):
    with conn.cursor() as cur:
        cur.execute("SELECT ac_id FROM ac WHERE ac_number = 42")
        tundi = cur.fetchone()["ac_id"]
        cur.execute(
            "INSERT INTO news_item (url, url_hash, title, published, labelled_at, issues, parties, "
            "ac_ids) "
            "VALUES ('https://feed.test/c', 'test-hash-c', 'Tundi only', now(), now(), "
            "ARRAY['water'], ARRAY['JMM'], ARRAY[%s]::INT[])", (tundi,))
    try:
        body = client.get("/acs/32/news/issues",
                          headers={"Authorization": f"Bearer {tokens['admin']}"}).json()
        assert sum(r["items"] for r in body["by_week"]) == 0
        assert body["by_party"] == []
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM news_item WHERE url = 'https://feed.test/c'")
