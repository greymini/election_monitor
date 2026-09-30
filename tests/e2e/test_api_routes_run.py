"""Every read route, executed against the real schema.

The gap this closes is N7: `GET /summary` selected `w.votes_counted` and
`w.total_valid`, two columns that do not exist on the rebuilt
`mv_result_booth_wide`. That is a 500 on the first page every user sees, it
survived review, and `tests/test_api_sql_relations.py` did not catch it because
that test checks the *relations* a query names, not the columns.

Nothing had ever run an API query against a database. This does. It is the
narrow version of what section 2 of the hardening brief asks for - every route,
every role - built now because the routes touched in this batch need verifying
and because a column-level check cannot be done any other way.

An empty database is the right fixture here. These routes must return 200 with
honest empty or NULL payloads when nothing is loaded, which is most of the six
constituencies most of the time; a route that only works once data exists is a
route that 500s on a fresh install.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import requires_db

pytestmark = requires_db


@pytest.fixture(scope="module")
def client(conn, db_url):
    """A TestClient wired to the e2e database.

    The app reads its settings at import time, so the environment has to be set
    before `api.main` is imported and the settings cache cleared after.
    """
    import os

    fastapi_testclient = pytest.importorskip("fastapi.testclient")

    os.environ["DATABASE_URL"] = db_url
    os.environ["JWT_SECRET"] = "e2e-route-check-secret-" + "x" * 32
    os.environ["CHAT_ENABLED"] = "false"
    # /auth/login refuses unless password mode is on; the default is OTP,
    # which would need an SMS provider.
    os.environ["AUTH_MODE"] = "password"

    from common.config import get_settings

    get_settings.cache_clear()

    # Purge only api.* so a reload does not mint a second copy of the exception
    # classes the rest of the suite compares against.
    import sys

    for name in [m for m in sys.modules if m.startswith("api.")]:
        del sys.modules[name]
    if "api" in sys.modules:
        del sys.modules["api"]

    from api.main import app

    with fastapi_testclient.TestClient(app) as test_client:
        yield test_client


def _token(client, role: str = "admin", block_id: int | None = None) -> dict[str, str]:
    """A bearer token for `role`.

    The user is inserted straight into `app_user` with the app's own
    `hash_password`, and then logged in through the real `/auth/login`. Going
    through the endpoint rather than minting a token directly means this also
    exercises the auth path, and a token this test forged itself would not prove
    the routes accept real ones.

    Login is by **phone**, not email - the first version of this helper assumed
    email, skipped when it got a 401, and reported 14 passes that had checked
    nothing at all.
    """
    import uuid

    from api.deps import hash_password
    from common.db import query_one

    phone = f"9{uuid.uuid4().int % 10**9:09d}"
    password = "route-check-" + uuid.uuid4().hex

    query_one(
        "INSERT INTO app_user (phone, name, role, block_id, password_hash, "
        "                      daily_token_budget) "
        "VALUES (%s, %s, %s, %s, %s, 150000) "
        "ON CONFLICT (phone) DO UPDATE SET password_hash = EXCLUDED.password_hash "
        "RETURNING user_id",
        (phone, f"Route check {role}", role, block_id, hash_password(password)),
    )

    response = client.post("/auth/login", json={"phone": phone, "password": password})
    assert response.status_code == 200, (
        f"login failed with {response.status_code}: {response.text[:300]}"
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# The read routes under /acs/{ac}, with the parameters each needs. Written out
# rather than discovered, so a new route has to be added here deliberately -
# a discovered list would silently shrink if the walker broke.
AC_ROUTES = [
    "/summary",
    "/areas",
    "/booths?metric=signed_margin_pct&election_label=VS-2024",
    "/results?election_label=VS-2024",
    "/rolls",
    "/caste",
    "/factors",
    "/transfer?year=2024",
    "/scenario/baseline",
    "/candidates",
    "/local-politics",
    "/news",
]


@pytest.mark.parametrize("path", AC_ROUTES)
def test_every_ac_route_runs_against_the_real_schema(client, path):
    """No 5xx. An empty database is a valid state, not an error."""
    headers = _token(client)
    response = client.get(f"/acs/32{path}", headers=headers)
    assert response.status_code < 500, (
        f"GET /acs/32{path} returned {response.status_code}:\n"
        f"{response.text[:600]}"
    )
    # 404 is acceptable for a route that needs a row that does not exist; 500
    # is what this test exists to catch, and 422 means the parameters here are
    # wrong rather than the SQL.
    assert response.status_code != 422, (
        f"GET /acs/32{path} rejected its parameters: {response.text[:300]}"
    )


def test_the_summary_route_returns_its_margin_and_turnout_columns(client):
    """N7 and N5 specifically.

    N7: the query named two columns that no longer exist. N5: the winner came
    from a hardcoded eight-party pivot, so it could report OTHERS as the winning
    party; it now ranks candidates and returns the candidate's name beside the
    party, which is the field that proves which path produced it.
    """
    headers = _token(client)
    response = client.get("/acs/32/summary", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert "elections" in body, body.keys()
    for row in body["elections"]:
        # Present even when null - the shape must not depend on the data.
        for field in ("margin_votes", "margin_pct", "turnout_pct",
                      "winner_party", "winner_candidate", "runner_party"):
            assert field in row, f"{field} missing from an election row: {row}"
        assert row["winner_party"] != "OTHERS", (
            "the AC winner is the 'OTHERS' bucket, which is N5: the ranking is "
            "reading a party pivot rather than candidates"
        )


def test_no_route_reports_a_number_where_nothing_is_loaded(client):
    """The whole project's rule, checked at the edge.

    An empty database must produce NULLs and empty lists, never zeros dressed
    as counts. D2, D4, B1 and B4 were all this failure.
    """
    headers = _token(client)
    response = client.get("/acs/32/summary", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    for row in body["elections"]:
        if row.get("booths") in (0, None):
            for field in ("margin_votes", "margin_pct", "turnout_pct"):
                assert row[field] is None, (
                    f"{row.get('label')} has no booths loaded but reports "
                    f"{field}={row[field]!r}"
                )
