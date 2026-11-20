"""The signed media route (slice 2c): no-store and no-referrer headers, one token per asset and viewer,
410 once the file is purged, and access log lines without the token."""
from __future__ import annotations

import logging

import pytest

from fastapi.testclient import TestClient

from pulse.api import logfilter, media_tokens
from pulse.api.main import create_app
from tests.conftest import TEST_JWT_SECRET
from tests.test_reports_api import BASE
from tests.test_storage_imagemeta import make_jpeg
from tests.triage_world import file_report


@pytest.fixture
def api_as(settings, pool, core, fake_tg, storage):
    clients = []

    def make(user_id):
        c = TestClient(create_app(settings, pool=pool, core=core, telegram_client=fake_tg, storage=storage))
        c.__enter__()
        if user_id:
            c.headers["Authorization"] = f"Bearer {core.issue_token(user_id)}"
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def two_photos(conn, core, team, storage):
    r = file_report(conn, core, team["worker-a1"], photo="hard-1")
    other = file_report(conn, core, team["worker-a1"], photo="hard-2")
    assets = {}
    for rid, fid in ((r["report_id"], "hard-1"), (other["report_id"], "hard-2")):
        a = conn.execute("SELECT asset_id FROM field.media_asset WHERE tg_file_id = %s", (fid,)).fetchone()["asset_id"]
        obj = storage.put(make_jpeg())
        conn.execute("UPDATE field.media_asset SET status = 'stored', sha256 = %s, storage_key = %s WHERE asset_id = %s",
                     (obj.sha256, obj.key, a))
        assets[fid] = (rid, a)
    return assets


def test_headers(api_as, team, two_photos):
    rid, a = two_photos["hard-1"]
    res = api_as(None).get(f"{BASE}/{rid}/media/{a}",
                           params={"token": media_tokens.issue(TEST_JWT_SECRET, team["principal"], rid, a)})
    assert res.status_code == 200
    assert res.headers["cache-control"] == "private, no-store"
    assert res.headers["referrer-policy"] == "no-referrer"
    assert res.headers["x-content-type-options"] == "nosniff"


def test_token_bound_to_asset_and_viewer(conn, api_as, team, two_photos):
    api = api_as(None)
    rid1, a1 = two_photos["hard-1"]
    rid2, a2 = two_photos["hard-2"]
    tok = media_tokens.issue(TEST_JWT_SECRET, team["principal"], rid1, a1)
    assert api.get(f"{BASE}/{rid1}/media/{a1}", params={"token": tok}).status_code == 200
    assert api.get(f"{BASE}/{rid2}/media/{a2}", params={"token": tok}).status_code == 403     # another asset
    assert api.get(f"{BASE}/{rid1}/media/{a2}", params={"token": tok}).status_code == 403     # mixed ids
    # the view is recorded against the viewer the link was issued to
    viewer = conn.execute("SELECT actor_member_id FROM field.report_event WHERE kind = 'media_viewed'").fetchone()
    assert viewer["actor_member_id"] == team["principal"]
    # a valid link issued to someone who cannot see the report opens nothing
    outsider = media_tokens.issue(TEST_JWT_SECRET, team["worker-b1"], rid1, a1)
    assert api.get(f"{BASE}/{rid1}/media/{a1}", params={"token": outsider}).status_code == 404


def test_purged_asset_is_gone(conn, api_as, team, two_photos):
    rid, a = two_photos["hard-1"]
    conn.execute("UPDATE field.media_asset SET status = 'purged', storage_key = NULL, purged_at = now() "
                 "WHERE asset_id = %s", (a,))
    res = api_as(None).get(f"{BASE}/{rid}/media/{a}",
                           params={"token": media_tokens.issue(TEST_JWT_SECRET, team["principal"], rid, a)})
    assert res.status_code == 410
    detail = api_as("usr-principal").get(f"{BASE}/{rid}").json()
    assert detail["media"][0]["status"] == "purged" and "url" not in detail["media"][0]


def test_access_log_line_has_no_token(api_as, caplog):
    api_as(None)                                                        # create_app installs the filter
    logger = logging.getLogger(logfilter.ACCESS_LOGGER)
    assert any(isinstance(f, logfilter.StripQuery) for f in logger.filters)
    secret = "eyJhbGciOiJIUzI1NiJ9.demo-token.signature"
    with caplog.at_level(logging.INFO, logger=logfilter.ACCESS_LOGGER):
        # the shape uvicorn logs: client, method, full path with query, HTTP version, status
        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET",
                    f"/acs/32/field/reports/r/media/a?token={secret}", "1.1", 200)
    [line] = [r.getMessage() for r in caplog.records if r.name == logfilter.ACCESS_LOGGER]
    assert secret not in line and "token" not in line
    assert "/acs/32/field/reports/r/media/a?…" in line
