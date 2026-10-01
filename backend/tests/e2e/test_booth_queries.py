"""GET /booths and GET /results/{label}/booths: one row per booth, any election."""

from __future__ import annotations

from collections import Counter

from tests.e2e.conftest import requires_db

pytestmark = requires_db


def _get(client, tokens, path, **params):
    response = client.get(path, params=params,
                          headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert response.status_code == 200, response.text
    return response.json()


def _dupes(uids):
    return [u for u, n in Counter(uids).items() if n > 1]


def test_the_map_for_a_non_baseline_election_has_its_figures(client, tokens, ids):
    """mv_booth_priority is baseline-only, so filtering on it returned no booth
    at all for VS-2019 - an empty map with no explanation."""
    body = _get(client, tokens, "/acs/32/booths", election_label="VS-2019")
    features = body["features"]
    assert features, "booths exist whatever the election"
    with_margin = [f for f in features if f["properties"]["margin_pct"] is not None]
    assert len(with_margin) > len(features) // 2
    assert all(f["properties"]["election_label"] == "VS-2019" for f in with_margin)


def test_the_map_has_one_feature_per_booth(client, tokens, ids, conn):
    """booth_crosswalk was joined on PS number with no election, so a booth whose
    number was the same in several elections appeared once per election."""
    for label in (None, "VS-2024", "VS-2019"):
        params = {"election_label": label} if label else {}
        uids = [f["properties"]["booth_uid"]
                for f in _get(client, tokens, "/acs/32/booths", **params)["features"]]
        assert not _dupes(uids), (label, _dupes(uids)[:5])


def test_a_merged_booth_is_one_row_in_the_results_table(client, tokens, ids, conn):
    """Two PS numbers crosswalked to one booth in the same election (a merge)
    used to repeat that booth's row once per PS number."""
    with conn.cursor() as cur:
        cur.execute("SELECT x.election_id, x.ac_id FROM booth_crosswalk x "
                    "JOIN election e ON e.election_id = x.election_id "
                    "WHERE x.booth_uid = %s AND e.label = 'VS-2024'", (ids["booth_uid"],))
        row = cur.fetchone()
        cur.execute("INSERT INTO booth_crosswalk (election_id, ac_id, ps_number, booth_uid, "
                    "match_method, confidence, reviewed) VALUES (%s, %s, 9999, %s, 'manual', "
                    "0.9, true)", (row["election_id"], row["ac_id"], ids["booth_uid"]))
    try:
        rows = _get(client, tokens, "/acs/32/results/VS-2024/booths")["rows"]
        assert not _dupes([r["booth_uid"] for r in rows])
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM booth_crosswalk WHERE ps_number = 9999 AND booth_uid = %s",
                        (ids["booth_uid"],))
