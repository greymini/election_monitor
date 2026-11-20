import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.bot.dispatch import dispatch
from tests import updates as U

BASE = "/acs/32/field/members"


@pytest.fixture
def api(settings, pool, core, fake_tg):
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {core.issue_token('usr-principal')}"
        yield c


def test_auth_required(api, core):
    assert api.get(BASE, headers={"Authorization": ""}).status_code == 401
    assert api.get(BASE, headers={"Authorization": "Bearer junk"}).status_code == 401
    rep = core.issue_token("usr-worker-a1")
    assert api.get(BASE, headers={"Authorization": f"Bearer {rep}"}).status_code == 403
    assert api.get("/acs/99/field/members").status_code == 404


def test_create_list_update_members(api):
    r = api.post(BASE, json={"user_id": "usr-principal", "display_name": "डेमो प्रत्याशी", "is_principal": True,
                             "finance_scope": "all", "can_give_advance": True})
    assert r.status_code == 201, r.text
    principal = r.json()
    assert principal["parent_member_id"] is None and principal["telegram_linked"] is False

    r = api.post(BASE, json={"user_id": "usr-incharge-a", "display_name": "डेमो प्रभारी क",
                             "parent_member_id": principal["member_id"], "finance_scope": "team",
                             "scopes": [{"block_id": "blk-a"}]})
    assert r.status_code == 201, r.text
    incharge = r.json()
    assert incharge["scopes"] == [{"block_id": "blk-a", "area_id": None}]

    # second principal → 409; bad scope → 422; unknown field → 422
    assert api.post(BASE, json={"user_id": "usr-incharge-b", "display_name": "x", "is_principal": True}).status_code == 409
    assert api.post(BASE, json={"user_id": "usr-incharge-b", "display_name": "x", "parent_member_id": principal["member_id"],
                                "scopes": [{"block_id": "blk-zz"}]}).status_code == 422
    assert api.post(BASE, json={"user_id": "usr-incharge-b", "display_name": "x", "role": "admin"}).status_code == 422

    r = api.patch(f"{BASE}/{incharge['member_id']}", json={"scopes": [{"block_id": "blk-a"}, {"area_id": "area-b1"}],
                                                          "can_give_advance": True})
    assert r.status_code == 200, r.text
    assert r.json()["can_give_advance"] is True
    assert len(r.json()["scopes"]) == 2

    assert api.patch(f"{BASE}/{incharge['member_id']}", json={"finance_scope": None}).status_code == 422
    assert api.patch(f"{BASE}/{uuid.uuid4()}", json={"display_name": "x"}).status_code == 404

    listed = api.get(BASE).json()
    assert [m["user_id"] for m in listed] == ["usr-principal", "usr-incharge-a"]


def test_link_token_link_and_unlink(api, pool, bot, fake_tg, team):
    member_id = str(team["worker-a1"])
    r = api.post(f"{BASE}/{member_id}/link-token")
    assert r.status_code == 201
    body = r.json()
    link = urlparse(body["deep_link"])
    assert link.netloc == "t.me" and link.path == "/pulse_test_bot"
    assert parse_qs(link.query)["start"] == [body["token"]]

    assert dispatch(pool, bot, U.start(6001, body["token"])) == "linked"
    member = next(m for m in api.get(BASE).json() if m["member_id"] == member_id)
    assert member["telegram_linked"] is True

    assert api.delete(f"{BASE}/{member_id}/link").status_code == 204
    assert dispatch(pool, bot, U.text(6002, "x")) == "unlinked"
    member = next(m for m in api.get(BASE).json() if m["member_id"] == member_id)
    assert member["telegram_linked"] is False

    assert api.post(f"{BASE}/{uuid.uuid4()}/link-token").status_code == 404


def test_members_show_last_active_and_pin_state(api, conn, team):
    conn.execute("UPDATE field.member SET pin_locked_at = now() WHERE member_id = %s", (team["worker-a1"],))
    rows = {m["member_id"]: m for m in api.get(BASE).json()}
    w = rows[str(team["worker-a1"])]
    assert w["pin_locked_at"] is not None and w["pin_set"] is False
    assert w["last_seen_at"] is None                     # never messaged the bot: null, not a made-up time
    assert rows[str(team["principal"])]["pin_locked_at"] is None


def test_member_changes_are_audited_without_names(api, conn):
    principal = api.post(BASE, json={"user_id": "usr-principal", "display_name": "Demo Candidate", "is_principal": True,
                                     "finance_scope": "all"}).json()
    a = api.post(BASE, json={"user_id": "usr-incharge-a", "display_name": "Demo In-charge A",
                             "parent_member_id": principal["member_id"], "scopes": [{"block_id": "blk-a"}]}).json()
    api.patch(f"{BASE}/{a['member_id']}", json={"display_name": "Renamed A", "finance_scope": "team"})
    csv = "user_id,display_name,parent_user_id\nusr-worker-a1,Demo Worker A1,usr-incharge-a\n"
    assert api.post(f"{BASE}/import?dry_run=true", json={"csv": csv}).json()["imported"] is False   # dry run: no audit
    assert api.post(f"{BASE}/import?dry_run=false", json={"csv": csv}).json()["imported"] is True
    rows = conn.execute("SELECT action, actor_user_id, target_member_id, data FROM field.admin_audit "
                        "WHERE action LIKE 'member%' ORDER BY audit_id").fetchall()
    assert [r["action"] for r in rows] == ["member_created", "member_created", "member_changed", "member_created",
                                           "members_imported"]
    assert all(r["actor_user_id"] == "usr-principal" for r in rows)
    assert rows[1]["data"]["scopes"] == [{"block_id": "blk-a", "area_id": None}] and rows[1]["data"]["via"] == "api"
    assert rows[2]["data"]["fields"] == ["display_name", "finance_scope"] and rows[2]["data"]["finance_scope"] == "team"
    assert rows[3]["data"]["via"] == "csv" and rows[4]["data"] == {"count": 1, "user_ids": ["usr-worker-a1"]}
    text = str([r["data"] for r in rows])
    assert "Renamed" not in text and "Demo" not in text                    # names are not copied into the audit
