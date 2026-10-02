"""GET /acs/{ac}/booths/{uid}/card against the loaded dev dataset.

The card returned 500 for every booth after 0015 renamed the metric-view
columns (votes_counted, electors_now, net_change ...), and even before that its
shape did not match what frontend/src/components/BoothDrawer.tsx reads
(`roll`, `crosswalk[].election_label`, `priority.inputs_used`,
`new_voters.null_reason`), so the drawer's Voters and Sources tabs would have
thrown on a live response. These tests pin the shape the drawer consumes.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import requires_db

pytestmark = requires_db


def _card(client, token, ac_number, booth_uid):
    return client.get(f"/acs/{ac_number}/booths/{booth_uid}/card",
                      headers={"Authorization": f"Bearer {token}"})


@pytest.fixture(scope="module")
def roll_booth(loaded_dataset, db_url):
    """A booth that has a roll snapshot, so the Voters tab has data."""
    import psycopg

    with psycopg.connect(db_url) as conn:
        row = conn.execute(
            "SELECT s.booth_uid FROM roll_snapshot s JOIN ac a USING (ac_id) "
            "WHERE a.ac_number = 32 ORDER BY s.booth_uid LIMIT 1").fetchone()
    assert row, "the dev dataset loads a roll for a few booths"
    return row[0]


def _booth_in_block(db_url, block_id, inside=True):
    import psycopg

    op = "=" if inside else "<>"
    with psycopg.connect(db_url) as conn:
        return conn.execute(
            f"SELECT b.booth_uid FROM booth b JOIN area a ON a.area_id = b.area_id "
            f"WHERE a.block_id {op} %s ORDER BY b.booth_uid LIMIT 1", (block_id,)).fetchone()[0]


@pytest.mark.parametrize("role", ["admin", "strategist", "block"])
def test_the_card_loads_for_every_role(client, tokens, ids, users, db_url, role):
    # A block user only sees booths in their own block.
    booth = (_booth_in_block(db_url, users["block"]["block_id"]) if role == "block"
             else ids["booth_uid"])
    response = _card(client, tokens[role], ids["ac_number"], booth)
    assert response.status_code == 200, response.text


def test_a_block_user_cannot_open_another_blocks_booth(client, tokens, ids, users, db_url):
    other = _booth_in_block(db_url, users["block"]["block_id"], inside=False)
    assert _card(client, tokens["block"], ids["ac_number"], other).status_code == 403


def test_the_card_has_the_shape_the_drawer_reads(client, tokens, ids, roll_booth):
    card = _card(client, tokens["admin"], ids["ac_number"], roll_booth).json()

    assert set(card) >= {"booth", "results", "roll", "new_voters", "priority",
                         "crosswalk", "caveats"}

    assert card["results"], "a loaded booth has at least one election"
    for row in card["results"]:
        for key in ("election_label", "valid_votes", "votes_polled", "turnout_pct",
                    "margin_pct", "signed_margin_pct", "source_doc", "source_page"):
            assert key in row, key
        assert "votes_counted" not in row

    assert card["roll"], "this booth has a roll snapshot"
    for row in card["roll"]:
        for key in ("revision", "revision_date", "electors", "male", "female",
                    "other", "source_doc", "source_page"):
            assert key in row, key

    # Never null: the drawer reads .additions / .inputs_used.length directly.
    assert isinstance(card["new_voters"], dict)
    assert {"additions", "new_voter_pct", "null_reason"} <= set(card["new_voters"])
    assert isinstance(card["priority"], dict)
    assert isinstance(card["priority"]["inputs_used"], list)
    assert "weight_used" in card["priority"]

    for row in card["crosswalk"]:
        assert {"election_label", "ps_number", "confidence", "reviewed",
                "match_method"} <= set(row)


def test_a_booth_with_no_roll_says_why_new_voters_is_empty(client, tokens, ids, db_url):
    import psycopg

    with psycopg.connect(db_url) as conn:
        row = conn.execute(
            "SELECT b.booth_uid FROM booth b JOIN ac a USING (ac_id) "
            "WHERE a.ac_number = 32 AND NOT EXISTS (SELECT 1 FROM roll_snapshot s "
            "WHERE s.booth_uid = b.booth_uid) ORDER BY b.booth_uid LIMIT 1").fetchone()
    card = _card(client, tokens["admin"], ids["ac_number"], row[0]).json()
    assert card["roll"] == []
    assert card["new_voters"]["additions"] is None
    assert card["new_voters"]["null_reason"]


def test_a_block_user_gets_no_community_estimate(client, tokens, ids, users, db_url):
    booth = _booth_in_block(db_url, users["block"]["block_id"])
    card = _card(client, tokens["block"], ids["ac_number"], booth).json()
    assert "caste_estimate" not in card


def test_a_booth_from_another_constituency_is_a_404(client, tokens, ids):
    response = _card(client, tokens["admin"], ids["empty_ac"], ids["booth_uid"])
    assert response.status_code == 404
