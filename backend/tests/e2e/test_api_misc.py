"""/config, ground reports and token handling against the real app."""

from __future__ import annotations

from tests.e2e.conftest import requires_db

pytestmark = requires_db


def test_config_defaults_to_the_primary_seat(client):
    """It took the first AC by number (31, Gandey), which has no data, so a
    first-time visitor landed on an empty dashboard."""
    assert client.get("/config").json()["default_ac"] == 32


def test_a_ground_report_for_another_constituencys_booth_is_refused(client, tokens):
    headers = {"Authorization": f"Bearer {tokens['admin']}"}
    response = client.post("/acs/42/ground-reports", headers=headers,
                           json={"booth_uid": "32-B0001", "text": "Water shortage near the school"})
    assert response.status_code == 404


def test_a_ground_report_for_an_unknown_booth_is_a_404_not_a_500(client, tokens):
    headers = {"Authorization": f"Bearer {tokens['admin']}"}
    response = client.post("/acs/32/ground-reports", headers=headers,
                           json={"booth_uid": "32-B9999", "text": "Road repairs pending"})
    assert response.status_code == 404


def test_a_block_user_cannot_report_on_another_block(client, tokens, users, db_url):
    import psycopg

    with psycopg.connect(db_url) as conn:
        other = conn.execute(
            "SELECT b.booth_uid FROM booth b JOIN area a ON a.area_id = b.area_id "
            "WHERE a.block_id <> %s AND b.ac_id = a.ac_id ORDER BY 1 LIMIT 1",
            (users["block"]["block_id"],)).fetchone()[0]
    response = client.post("/acs/32/ground-reports",
                           headers={"Authorization": f"Bearer {tokens['block']}"},
                           json={"booth_uid": other, "text": "Crowding at the booth"})
    assert response.status_code == 403


def test_a_token_with_a_non_numeric_subject_is_a_401(client):
    from jose import jwt

    from common.config import get_settings

    s = get_settings()
    token = jwt.encode({"sub": "not-a-number"}, s.jwt_secret, algorithm=s.jwt_algorithm)
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
