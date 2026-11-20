"""Entry detail for the ledger drawer, the receipt route, and the save-free preview used by the web
form to show the bot's warnings."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.ledger import service as ledger
from pulse.ledger.service import ExpenseInput
from pulse.reports import media
from tests.helpers import bucket_id
from tests.test_storage_imagemeta import make_jpeg

BASE = "/acs/32/field"


@pytest.fixture
def api(settings, pool, core, fake_tg, storage, team):
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg, storage=storage)
    with TestClient(app):
        def as_(user):
            c = TestClient(app)
            if user:
                c.headers["Authorization"] = f"Bearer {core.issue_token(user)}"
            return c
        yield as_


def test_preview_shows_warnings_and_saves_nothing(api, conn, team):
    conn.execute("INSERT INTO field.ac_election_window (ac_id, starts_on, ends_on, cash_limit) "
                 "VALUES ('ac-32', current_date - 5, current_date + 5, 10000)")
    p = api("usr-principal")
    body = {"kind": "expense", "amount": "30000", "bucket_id": bucket_id(conn, "SOCIAL"), "pay_mode": "cash",
            "area_id": "area-a1", "block_id": "blk-a"}
    r = p.post(f"{BASE}/txns/preview", json=body)
    assert r.status_code == 200, r.text
    got = r.json()
    assert set(got["warnings"]) == {"large", "cash_over_limit", "sensitive_in_window"}
    assert got["in_election_window"] is True
    assert conn.execute("SELECT count(*) AS n FROM field.money_txn").fetchone()["n"] == 0
    assert p.post(f"{BASE}/txns", json=body).status_code == 201
    first = conn.execute("SELECT txn_no FROM field.money_txn").fetchone()["txn_no"]
    assert first.endswith("000001")                         # the preview did not use up a number


def test_preview_refuses_what_saving_would(api, conn, team):
    """The preview runs the same service code as saving: it refuses exactly what saving refuses, with the
    same status and reason, and accepts what saving accepts; it never saves anything itself."""
    w = api("usr-worker-a1")
    cases = [
        {"kind": "advance", "amount": "100", "to_member_id": str(team["worker-a2"])},       # cannot give advances
        {"kind": "expense", "amount": "100", "bucket_id": bucket_id(conn, "FOOD"), "remark": "call 9876543210"},
        {"kind": "expense", "amount": "100", "bucket_id": 999999},                          # no such bucket
    ]
    for body in cases:
        pre, save = w.post(f"{BASE}/txns/preview", json=body), w.post(f"{BASE}/txns", json=body)
        assert pre.status_code >= 400 and (pre.status_code, pre.json()) == (save.status_code, save.json()), body
    assert conn.execute("SELECT count(*) AS n FROM field.money_txn").fetchone()["n"] == 0
    ok = {"kind": "expense", "amount": "100", "bucket_id": bucket_id(conn, "FOOD")}
    assert w.post(f"{BASE}/txns/preview", json=ok).status_code == 200
    assert conn.execute("SELECT count(*) AS n FROM field.money_txn").fetchone()["n"] == 0
    assert w.post(f"{BASE}/txns", json=ok).status_code == 201


def test_detail_has_audit_followup_and_links(api, conn, core, limits, team):
    works = bucket_id(conn, "WORKS")
    row = ledger.create_expense(conn, core, team["worker-a1"], ExpenseInput(amount=Decimal("900"), bucket_id=works),
                                limits=limits)
    ledger.void(conn, row["txn_id"], team["worker-a1"], "typed twice", limits=limits)
    d = api("usr-principal").get(f"{BASE}/txns/{row['txn_id']}").json()
    assert d["txn"]["txn_no"] == row["txn_no"] and d["txn"]["status"] == "void"
    assert [a["action"] for a in d["audit"]] == ["void"] and d["audit"][0]["actor_name"]
    assert d["followup"]["answer"] is None and d["report"] is None and d["receipt"] is None
    assert api("usr-worker-b1").get(f"{BASE}/txns/{row['txn_id']}").status_code == 404   # not in their scope


def test_receipt_link_is_signed_and_scoped(api, conn, core, limits, team, storage):
    member = conn.execute("SELECT * FROM field.member WHERE member_id = %s", (team["worker-a1"],)).fetchone()
    item = media.Item(tg_kind="photo", kind="photo", file_id="rc-1", file_unique_id="rcu-1", file_size=10,
                      mime="image/jpeg", duration_s=None, width=10, height=10, media_group_id=None)
    asset = media.register(conn, member, item)
    obj = storage.put(make_jpeg())
    conn.execute("UPDATE field.media_asset SET status = 'stored', sha256 = %s, storage_key = %s WHERE asset_id = %s",
                 (obj.sha256, obj.key, asset["asset_id"]))
    row = ledger.create_expense(conn, core, team["worker-a1"],
                                ExpenseInput(amount=Decimal("500"), bucket_id=bucket_id(conn, "FOOD"),
                                             receipt_asset_id=asset["asset_id"]), limits=limits)
    d = api("usr-worker-a1").get(f"{BASE}/txns/{row['txn_id']}").json()
    url = d["receipt"]["url"]
    anon = api(None)
    got = anon.get(url)
    assert got.status_code == 200 and got.content[:3] == b"\xff\xd8\xff"
    assert anon.get(url.replace("token=", "token=x")).status_code == 403
    other = ledger.create_expense(conn, core, team["worker-a1"],
                                  ExpenseInput(amount=Decimal("5"), bucket_id=bucket_id(conn, "FOOD")), limits=limits)
    assert anon.get(url.replace(str(row["txn_id"]), str(other["txn_id"]))).status_code == 403
