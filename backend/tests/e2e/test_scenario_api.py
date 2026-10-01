"""The scenario engine against the loaded database and through the API."""

from __future__ import annotations

import pytest

from tests.e2e.conftest import refresh_views, requires_db

pytestmark = requires_db


def _project(client, tokens, ac_number, **body):
    response = client.post(f"/acs/{ac_number}/scenario", json={"draws": 50, **body},
                           headers={"Authorization": f"Bearer {tokens['strategist']}"})
    assert response.status_code == 200, response.text
    return response.json()


def test_sympathy_swing_moves_the_margin_in_both_directions(client, tokens, ids):
    base = _project(client, tokens, ids["ac_number"], sympathy_swing=0.0)
    plus = _project(client, tokens, ids["ac_number"], sympathy_swing=0.05)
    minus = _project(client, tokens, ids["ac_number"], sympathy_swing=-0.05)
    key = "contest_margin" if "contest_margin" in base else "margin_point"
    assert plus[key] > base[key] > minus[key]


def test_a_block_user_cannot_run_a_scenario(client, tokens, ids):
    response = client.post(f"/acs/{ids['ac_number']}/scenario", json={},
                           headers={"Authorization": f"Bearer {tokens['block']}"})
    assert response.status_code == 403


def test_baseline_booths_are_not_duplicated_by_a_second_roll_link(conn, db_url, monkeypatch,
                                                                  loaded_dataset):
    """mv_new_voter_share has a row per (election, booth). Joined on booth only,
    each booth appeared once per linked election and the totals multiplied."""
    monkeypatch.setenv("DATABASE_URL", db_url)
    from common.config import get_settings
    from common.db import close_pools

    get_settings.cache_clear()
    close_pools()
    from analytics.scenario import load_baseline

    with conn.cursor() as cur:
        cur.execute("SELECT ac_id FROM ac WHERE ac_number = 32")
        ac_id = cur.fetchone()["ac_id"]
        before = len(load_baseline(ac_id))
        cur.execute(
            "SELECT e.election_id FROM election e WHERE e.ac_id = %s AND NOT e.is_baseline "
            "AND NOT EXISTS (SELECT 1 FROM election_roll_link l WHERE l.election_id = e.election_id) "
            "LIMIT 1", (ac_id,))
        other = cur.fetchone()["election_id"]
        cur.execute("SELECT revision_id FROM roll_revision WHERE ac_id = %s LIMIT 1", (ac_id,))
        revision = cur.fetchone()
        if revision is None:
            pytest.skip("no roll revision loaded")
        cur.execute("INSERT INTO election_roll_link (election_id, revision_id) VALUES (%s, %s)",
                    (other, revision["revision_id"]))
        try:
            refresh_views(cur)
            after = len(load_baseline(ac_id))
        finally:
            cur.execute("DELETE FROM election_roll_link WHERE election_id = %s", (other,))
            refresh_views(cur)
    assert after == before
    close_pools()
