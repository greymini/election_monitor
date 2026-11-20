"""Issues (slice 3): create, list, detail, link and unlink reports, close; same-area and open-only rules;
who may change them; the alert-card button for coordinators."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.bot import texts_hi as T
from tests.triage_world import alerts_for, file_report

BASE = "/acs/32/field"


@pytest.fixture
def api(settings, pool, core, fake_tg, storage, team, conn):
    conn.execute("UPDATE field.member SET is_coordinator = true WHERE member_id = %s", (team["incharge-b"],))
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg, storage=storage)
    with TestClient(app):
        def as_(user):
            c = TestClient(app)
            c.headers["Authorization"] = f"Bearer {core.issue_token(user)}"
            return c
        yield as_


def _events(conn, issue_id):
    return [r["kind"] for r in conn.execute("SELECT kind FROM field.issue_event WHERE issue_id = %s ORDER BY event_id",
                                            (issue_id,))]


def test_create_link_unlink_close(api, conn, core, team):
    coord = api("usr-incharge-b")
    r1 = file_report(conn, core, team["worker-a1"])                     # area-a1
    r2 = file_report(conn, core, team["worker-a1"])
    res = coord.post(f"{BASE}/issues", json={"report_id": str(r1["report_id"])})
    assert res.status_code == 201
    issue = res.json()["issue"]
    assert issue["issue_no"] == "32-I-000001" and issue["area_id"] == "area-a1" and issue["status"] == "open"
    assert issue["title"] == "बिजली-पानी-सड़क — डेमो पंचायत क1"           # default: category — area
    iid = issue["issue_id"]
    assert [r["report_no"] for r in res.json()["reports"]] == [r1["report_no"]]

    assert coord.post(f"{BASE}/issues/{iid}/reports", json={"report_id": str(r2["report_id"])}).json()["changed"]
    again = coord.post(f"{BASE}/reports/{r2['report_id']}/link-issue", json={"issue_id": iid})
    assert again.status_code == 200 and again.json()["changed"] is False
    detail = coord.get(f"{BASE}/issues/{iid}").json()
    assert len(detail["reports"]) == 2
    assert [e["kind"] for e in detail["timeline"]] == ["opened", "report_linked", "report_linked"]

    assert coord.delete(f"{BASE}/issues/{iid}/reports/{r2['report_id']}").status_code == 204
    assert conn.execute("SELECT issue_id FROM field.report WHERE report_id = %s", (r2["report_id"],)).fetchone()["issue_id"] is None
    assert coord.delete(f"{BASE}/issues/{iid}/reports/{r2['report_id']}").status_code == 404

    renamed = coord.patch(f"{BASE}/issues/{iid}", json={"title": "डेमो चापाकल पंचायत क1"}).json()["issue"]
    assert renamed["title"] == "डेमो चापाकल पंचायत क1"
    closed = coord.post(f"{BASE}/issues/{iid}/close", json={"note": "मरम्मत हो गई"}).json()["issue"]
    assert closed["status"] == "closed" and closed["closed_at"] is not None
    assert coord.post(f"{BASE}/issues/{iid}/reports", json={"report_id": str(r2["report_id"])}).status_code == 409
    assert coord.post(f"{BASE}/issues/{iid}/close", json={}).status_code == 409
    assert _events(conn, iid) == ["opened", "report_linked", "report_linked", "report_unlinked", "renamed", "closed"]
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM field.report_event WHERE report_id = %s ORDER BY event_id",
                                             (r2["report_id"],))]
    assert "issue_linked" in kinds and "issue_unlinked" in kinds


def test_same_area_only(api, conn, core, team):
    coord = api("usr-incharge-b")
    a1 = file_report(conn, core, team["worker-a1"])                     # area-a1
    a2 = file_report(conn, core, team["worker-a2"], area=("blk-a", "area-a2"))
    no_area = file_report(conn, core, team["worker-a2"], area=(None, None))
    iid = coord.post(f"{BASE}/issues", json={"title": "डेमो सड़क", "area_id": "area-a1"}).json()["issue"]["issue_id"]
    assert coord.post(f"{BASE}/issues/{iid}/reports", json={"report_id": str(a2["report_id"])}).status_code == 422
    assert coord.post(f"{BASE}/issues/{iid}/reports", json={"report_id": str(no_area["report_id"])}).status_code == 422
    assert coord.post(f"{BASE}/issues", json={"report_id": str(no_area["report_id"])}).status_code == 422
    assert coord.post(f"{BASE}/issues", json={"title": "x", "area_id": "area-zz"}).status_code == 422
    assert coord.post(f"{BASE}/issues/{iid}/reports", json={"report_id": str(a1["report_id"])}).status_code == 200
    other = coord.post(f"{BASE}/issues", json={"title": "डेमो दूसरा", "area_id": "area-a1"}).json()["issue"]["issue_id"]
    assert coord.post(f"{BASE}/issues/{other}/reports", json={"report_id": str(a1["report_id"])}).status_code == 409


def test_who_may_change_and_see(api, conn, core, team):
    r = file_report(conn, core, team["worker-a1"])
    restricted = file_report(conn, core, team["worker-a1"], category="LAW")
    assert api("usr-incharge-a").post(f"{BASE}/issues", json={"title": "x", "area_id": "area-a1"}).status_code == 403
    assert api("usr-worker-a1").post(f"{BASE}/issues", json={"title": "x", "area_id": "area-a1"}).status_code == 403
    principal = api("usr-principal")
    open_ = principal.post(f"{BASE}/issues", json={"report_id": str(r["report_id"])}).json()["issue"]["issue_id"]
    secret = principal.post(f"{BASE}/issues", json={"report_id": str(restricted["report_id"])}).json()["issue"]["issue_id"]
    seen = {i["issue_id"] for i in api("usr-incharge-a").get(f"{BASE}/issues").json()["items"]}
    assert seen == {open_}                                              # restricted category: hidden
    assert api("usr-incharge-a").get(f"{BASE}/issues/{secret}").status_code == 404
    assert api("usr-incharge-a").post(f"{BASE}/issues/{open_}/close", json={}).status_code == 403
    assert {i["issue_id"] for i in principal.get(f"{BASE}/issues", params={"status": "open"}).json()["items"]} == {open_, secret}
    assert principal.get(f"{BASE}/issues", params={"area_id": "area-b1"}).json()["total"] == 0


def test_coordinator_links_from_alert_card(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    principal = world.principal
    card = next(m for m in reversed(principal.mine) if m["reply_markup"] and T.BTN_ISSUE_LINK in principal.buttons(m))
    assert principal.tap(T.BTN_ISSUE_LINK, card=card) == "report_pick_issue"
    assert principal.tap(T.BTN_ISSUE_NEW, card=principal.mine[-1] if principal.mine[-1]["reply_markup"] else card) \
        in ("report_issue_created",)
    row = conn.execute("SELECT issue_id FROM field.report WHERE report_id = %s", (r["report_id"],)).fetchone()
    assert row["issue_id"] is not None
    world.drain()
    issue_no = conn.execute("SELECT issue_no FROM field.issue").fetchone()["issue_no"]
    assert any(T.CARD_ISSUE.split(":")[0] in (m.get("text") or "") and issue_no in m["text"] for m in world.fake.cards)

    # a second report in the same area joins the existing issue from its card
    r2 = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    card2 = next(m for m in reversed(principal.mine) if m["reply_markup"] and T.BTN_ISSUE_LINK in principal.buttons(m))
    principal.tap(T.BTN_ISSUE_LINK, card=card2)
    pick = next(m for m in reversed(world.fake.cards) if m["reply_markup"] and
                any(issue_no in b["text"] for row in m["reply_markup"]["inline_keyboard"] for b in row))
    label = next(b["text"] for row in pick["reply_markup"]["inline_keyboard"] for b in row if issue_no in b["text"])
    assert principal.tap(label, card=pick) == "report_issue_linked"
    assert conn.execute("SELECT issue_id FROM field.report WHERE report_id = %s", (r2["report_id"],)).fetchone()["issue_id"] \
        == row["issue_id"]


def test_reporter_parent_card_has_no_issue_button(conn, core, world):
    file_report(conn, core, world.team["worker-a1"])                   # normal: in-charge A only
    world.drain()
    cards = [m for m in world.incharge.mine if m["reply_markup"]]
    assert cards and all(T.BTN_ISSUE_LINK not in world.incharge.buttons(m) for m in cards)
    assert alerts_for(conn, conn.execute("SELECT report_id FROM field.report").fetchone()["report_id"])
