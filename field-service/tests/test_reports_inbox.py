"""Report fields the web inbox, drawer and map need: thumbnail links, open and located filters, the
count of reports without a location, assignable members, permission hints, and the map tiles."""
import dataclasses

import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.common.clock import utcnow
from pulse.config import ConfigError, check_settings
from pulse.reports import service as reports
from tests.test_reports_api import file_report
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


@pytest.fixture
def world(conn, core, team, storage):
    a = file_report(conn, core, team["worker-a1"], photo="inbox-1")
    asset = conn.execute("SELECT asset_id FROM field.media_asset WHERE tg_file_id = 'inbox-1'").fetchone()["asset_id"]
    obj = storage.put(make_jpeg())
    conn.execute("UPDATE field.media_asset SET status = 'stored', sha256 = %s, storage_key = %s WHERE asset_id = %s",
                 (obj.sha256, obj.key, asset))
    reports.set_location(conn, a["report_id"], 24.2, 86.3, actor=team["worker-a1"], now=utcnow())
    b = file_report(conn, core, team["worker-a1"])
    conn.execute("UPDATE field.report SET status = 'resolved', resolved_at = now(), ack_at = now() WHERE report_id = %s",
                 (b["report_id"],))
    return a, b


def test_list_has_thumbnails_and_filters(api, world):
    a, b = world
    p = api("usr-principal")
    rows = {r["report_id"]: r for r in p.get(f"{BASE}/reports").json()["items"]}
    assert rows[str(a["report_id"])]["thumb_url"] and rows[str(b["report_id"])]["thumb_url"] is None
    assert api(None).get(rows[str(a["report_id"])]["thumb_url"]).status_code == 200
    assert [r["report_id"] for r in p.get(f"{BASE}/reports", params={"open": True}).json()["items"]] == [str(a["report_id"])]
    located = p.get(f"{BASE}/reports", params={"located": True}).json()
    assert [(r["lat"], r["lon"]) for r in located["items"]] == [("24.200000", "86.300000")]
    assert located["without_location"] == 1


def test_detail_has_permission_hints_and_assignable(api, world, team):
    a, _ = world
    d = api("usr-principal").get(f"{BASE}/reports/{a['report_id']}").json()
    assert d["can"] == {"triage": True, "work": False, "reopen": True, "correct": True, "manage_issues": True}
    members = api("usr-principal").get(f"{BASE}/reports/{a['report_id']}/assignable").json()["members"]
    assert str(team["worker-a1"]) in {m["member_id"] for m in members}
    w = api("usr-worker-a1").get(f"{BASE}/reports/{a['report_id']}").json()["can"]
    assert w["triage"] is False and w["manage_issues"] is False
    assert api("usr-worker-a1").get(f"{BASE}/reports/{a['report_id']}/assignable").json()["members"] == []


def test_places_carry_map_tiles(api, settings):
    m = api("usr-worker-a1").get(f"{BASE}/places").json()["map"]
    assert "{z}" in m["tile_url"] and m["attribution"]
    with pytest.raises(ConfigError, match="MAP_TILE_URL"):
        check_settings(dataclasses.replace(settings, map_tile_url="ftp://tiles"))
