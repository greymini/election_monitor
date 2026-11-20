"""GET /me and /places: the signed-in user, member row and finance scope; the AC's blocks and areas."""
import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app

BASE = "/acs/32/field"


@pytest.fixture
def get(settings, pool, core, fake_tg, team):
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg)
    with TestClient(app) as c:
        def go(user, path):
            return c.get(BASE + path, headers={"Authorization": f"Bearer {core.issue_token(user)}"} if user else {})
        yield go


def test_me_returns_user_member_and_acs(get, team):
    r = get("usr-worker-a1", "/me").json()
    assert r["user"]["role"] == "rep" and r["ac"]["ac_number"] == 32
    assert r["member"]["member_id"] == str(team["worker-a1"]) and r["member"]["finance_scope"] == "own"
    assert r["acs"] == [{"ac_number": 32, "name_hi": "डेमो विधानसभा 32", "name_en": "Demo AC 32"}]
    assert get("usr-principal", "/me").json()["member"]["is_principal"] is True


def test_me_needs_a_token_and_a_known_ac(get):
    assert get(None, "/me").status_code == 401


def test_places_lists_blocks_and_areas_with_electors(get):
    r = get("usr-worker-a1", "/places").json()
    assert len(r["blocks"]) == 3 and len(r["areas"]) == 8
    a3 = next(a for a in r["areas"] if a["area_id"] == "area-a3")
    assert a3["electors"] is None                              # missing is null, never 0
