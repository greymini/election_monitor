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


@pytest.fixture
def labelled(conn):
    """Three labelled items: one Giridih, one Tundi, one state-wide."""
    with conn.cursor() as cur:
        cur.execute("SELECT ac_id, ac_number FROM ac WHERE ac_number IN (32, 42)")
        ids = {r["ac_number"]: r["ac_id"] for r in cur.fetchall()}
        rows = [
            ("https://feed.test/g", "Giridih bypoll: JMM and BJP", [ids[32]], "ac",
             ["candidate/organisation"], ["BJP", "JMM"], 0.8),
            ("https://feed.test/t", "Tundi water protest", [ids[42]], "ac",
             ["water"], [], 0.3),
            ("https://feed.test/s", "Jharkhand BJP reshuffle in Ranchi", [], "state",
             ["other"], ["BJP"], 0.25),
        ]
        for url, title, ac_ids, scope, issues, parties, rel in rows:
            cur.execute(
                "INSERT INTO news_item (url, url_hash, title, published, labelled_at, "
                "label_method, issues, parties, ac_ids, scope, relevance) "
                "VALUES (%s, %s, %s, now(), now(), 'rules', %s, %s, %s::INT[], %s, %s)",
                (url, "test-" + url[-1], title, issues, parties, ac_ids, scope, rel))
    yield
    with conn.cursor() as cur:
        cur.execute("DELETE FROM news_item WHERE url LIKE 'https://feed.test/%'")


def _titles(body):
    return {r["title"] for r in body["rows"]}


def test_state_scope_adds_jharkhand_news_that_names_no_seat(labelled, client, tokens):
    ac = _titles(_news(client, tokens, 32))
    state = _titles(_news(client, tokens, 32, scope="state"))
    assert "Giridih bypoll: JMM and BJP" in ac
    assert "Jharkhand BJP reshuffle in Ranchi" not in ac
    assert {"Giridih bypoll: JMM and BJP", "Jharkhand BJP reshuffle in Ranchi",
            "Tundi water protest"} <= state


def test_party_filter_and_relevance_sort(labelled, client, tokens):
    body = _news(client, tokens, 32, scope="state", party="BJP", sort="relevance")
    titles = [r["title"] for r in body["rows"] if r["title"].startswith(("Giridih", "Jharkhand"))]
    assert titles == ["Giridih bypoll: JMM and BJP", "Jharkhand BJP reshuffle in Ranchi"]
    assert all("BJP" in r["parties"] for r in body["rows"])
    assert body["rows"][0]["label_method"] == "rules"


def test_summary_counts_party_mentions_for_this_constituency_only(labelled, client, tokens):
    body = client.get("/acs/32/news/summary",
                      headers={"Authorization": f"Bearer {tokens['admin']}"}).json()
    parties = {r["party"]: r["items"] for r in body["parties"]}
    assert parties.get("BJP", 0) >= 1 and parties.get("JMM", 0) >= 1
    assert "water" not in {r["issue"] for r in body["issues"]}   # Tundi's item
    issue = next(r for r in body["issues"] if r["issue"] == "candidate/organisation")
    assert any(e["title"] == "Giridih bypoll: JMM and BJP" for e in issue["examples"])
    assert body["totals"]["rules_labelled"] >= 1
    assert abs(sum(r["share_pct"] for r in body["parties"]) - 100) < 1


def test_summary_state_scope_includes_unseated_news(labelled, client, tokens):
    body = client.get("/acs/32/news/summary", params={"scope": "state"},
                      headers={"Authorization": f"Bearer {tokens['admin']}"}).json()
    assert any(r["title"] == "Jharkhand BJP reshuffle in Ranchi" for r in body["top"])
    assert body["other_items"] >= 1


def test_places_and_the_place_filter(conn, client, tokens):
    """A place in /news/summary filters /news by the terms the crawler matched."""
    with conn.cursor() as cur:
        cur.execute("SELECT ac_id FROM ac WHERE ac_number = 32")
        giridih = cur.fetchone()["ac_id"]
        cur.execute(
            "INSERT INTO news_item (url, url_hash, title, published, labelled_at, label_method, "
            "issues, parties, ac_ids, scope, relevance, matched_terms) VALUES "
            "('https://feed.test/p1', 'test-p1', 'Pirtand road protest', now(), now(), 'rules', "
            " ARRAY['roads'], '{}', ARRAY[%s]::INT[], 'ac', 0.3, ARRAY['pirtand']), "
            "('https://feed.test/p2', 'test-p2', 'Giridih town fair', now(), now(), 'rules', "
            " ARRAY['other'], '{}', ARRAY[%s]::INT[], 'ac', 0.2, ARRAY['giridih'])",
            (giridih, giridih))
    try:
        auth = {"Authorization": f"Bearer {tokens['admin']}"}
        body = client.get("/acs/32/news/summary", headers=auth).json()
        places = {p["place"]: p["items"] for p in body["places"]}
        assert places.get("Pirtand", 0) >= 1 and places.get("Giridih", 0) >= 1
        rows = _news(client, tokens, 32, place="Pirtand")["rows"]
        assert [r["title"] for r in rows] == ["Pirtand road protest"]
        missing = client.get("/acs/32/news", params={"place": "Nowhere"}, headers=auth)
        assert missing.status_code == 404
        state = client.get("/acs/32/news/summary", params={"scope": "state"}, headers=auth).json()
        assert state["places"][-1]["ac_number"] is None   # "Rest of Jharkhand"
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM news_item WHERE url LIKE 'https://feed.test/p%'")
