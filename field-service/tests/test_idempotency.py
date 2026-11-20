"""Idempotency keys on web writes that create something: POST /txns, /reports, /issues, /members and
/members/import. A repeat with the same key returns the first result and creates nothing new."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from pulse.api import idempotency
from pulse.api.main import create_app
from pulse.common.clock import utcnow
from tests.helpers import bucket_id
from tests.triage_world import file_report

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


def _count(conn, table, where="TRUE"):
    return conn.execute(f"SELECT count(*) AS n FROM field.{table} WHERE {where}").fetchone()["n"]


def _twice(client, path, body, key="k-1"):
    first = client.post(path, json=body, headers={"Idempotency-Key": key})
    again = client.post(path, json=body, headers={"Idempotency-Key": key})
    assert first.status_code in (200, 201), first.text
    assert (again.status_code, again.json()) == (first.status_code, first.json())
    assert again.headers.get("Idempotent-Replay") == "true" and "Idempotent-Replay" not in first.headers
    return first.json()


def test_txn(api, conn):
    w = api("usr-worker-a1")
    body = {"kind": "expense", "amount": "1500", "bucket_id": bucket_id(conn, "FOOD")}
    _twice(w, f"{BASE}/txns", body)
    assert _count(conn, "money_txn") == 1
    # a different body under the same key is refused; a new key creates a new entry
    assert w.post(f"{BASE}/txns", json={**body, "amount": "1600"}, headers={"Idempotency-Key": "k-1"}).status_code == 422
    assert w.post(f"{BASE}/txns", json=body, headers={"Idempotency-Key": "k-2"}).status_code == 201
    assert _count(conn, "money_txn") == 2
    # without a key nothing changes: each POST creates
    assert w.post(f"{BASE}/txns", json=body).status_code == 201
    assert _count(conn, "money_txn") == 3


def test_keys_are_per_user(api, conn):
    body = {"kind": "expense", "amount": "100", "bucket_id": bucket_id(conn, "FOOD")}
    assert api("usr-worker-a1").post(f"{BASE}/txns", json=body, headers={"Idempotency-Key": "same"}).status_code == 201
    assert api("usr-worker-a2").post(f"{BASE}/txns", json=body, headers={"Idempotency-Key": "same"}).status_code == 201
    assert _count(conn, "money_txn") == 2


def test_a_failed_write_does_not_use_up_the_key(api, conn):
    w = api("usr-worker-a1")
    bad = {"kind": "expense", "amount": "100", "bucket_id": bucket_id(conn, "FOOD"), "remark": "call 9876543210"}
    assert w.post(f"{BASE}/txns", json=bad, headers={"Idempotency-Key": "k"}).status_code == 422
    assert _count(conn, "idempotency_key") == 0
    good = {**bad, "remark": "chai"}
    assert w.post(f"{BASE}/txns", json=good, headers={"Idempotency-Key": "k"}).status_code == 201


def test_report(api, conn):
    body = {"note_text": "handpump broken near the school", "area_id": "area-a1", "block_id": "blk-a"}
    got = _twice(api("usr-worker-a1"), f"{BASE}/reports", body)
    assert got["report"]["report_no"] and _count(conn, "report", "status <> 'draft'") == 1


def test_issue(api, conn, core, team):
    rep = file_report(conn, core, team["worker-a1"])
    _twice(api("usr-incharge-b"), f"{BASE}/issues", {"report_id": str(rep["report_id"])})
    assert _count(conn, "issue") == 1


def test_member(api, conn, team):
    body = {"user_id": "usr-worker-c1", "display_name": "Demo Worker C1", "parent_member_id": str(team["principal"])}
    _twice(api("usr-principal"), f"{BASE}/members", body)
    assert _count(conn, "member", "user_id = 'usr-worker-c1'") == 1
    assert _count(conn, "admin_audit", "action = 'member_created'") == 1              # audited once, not per repeat


def test_csv_import(api, conn):
    csv = "user_id,display_name,parent_user_id\nusr-worker-c1,Demo Worker C1,usr-principal\n"
    a = api("usr-principal")
    # a dry run never uses up the key
    assert a.post(f"{BASE}/members/import?dry_run=true", json={"csv": csv}, headers={"Idempotency-Key": "imp"}).json()["ok"]
    got = _twice(a, f"{BASE}/members/import?dry_run=false", {"csv": csv}, key="imp")
    assert got["imported"] is True
    assert _count(conn, "member", "user_id = 'usr-worker-c1'") == 1
    assert _count(conn, "admin_audit", "action = 'members_imported'") == 1


def test_key_header_is_checked(api, conn):
    body = {"kind": "expense", "amount": "100", "bucket_id": bucket_id(conn, "FOOD")}
    assert api("usr-worker-a1").post(f"{BASE}/txns", json=body, headers={"Idempotency-Key": "x" * 101}).status_code == 422


def test_purge_drops_old_keys(conn):
    conn.execute("INSERT INTO field.idempotency_key (user_id, route, key, request_hash, created_at) VALUES "
                 "('u', 'POST /txns', 'old', 'h', now() - interval '3 days'), ('u', 'POST /txns', 'new', 'h', now())")
    assert idempotency.purge(conn, utcnow()) == 1
    assert [r["key"] for r in conn.execute("SELECT key FROM field.idempotency_key")] == ["new"]
    assert idempotency.RETENTION == timedelta(hours=48)
