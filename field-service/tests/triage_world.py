"""Five linked phones for triage tests, and a helper that files reports straight through the service.

    principal   USER_A  principal, finance all
    coord       USER_D  in-charge B, made a coordinator
    incharge    USER_B  in-charge A: parent of workers A1 and A2
    rep         USER_C  worker A1 (scope area-a1): the reporter
    wk2         USER_E  worker A2
Every phone runs the worker's queue after each update, like a live deployment.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from pulse.common.clock import utcnow
from pulse.reports import media
from pulse.reports import service as reports
from tests import updates as U
from tests.helpers import link
from tests.test_flow_report import Reporter


@pytest.fixture
def world(pool, bot, team, conn, drain):
    conn.execute("UPDATE field.member SET is_coordinator = true WHERE member_id = %s", (team["incharge-b"],))
    phones = {}
    for key, member, user in (("principal", "principal", U.USER_A), ("coord", "incharge-b", U.USER_D),
                              ("incharge", "incharge-a", U.USER_B), ("rep", "worker-a1", U.USER_C),
                              ("wk2", "worker-a2", U.USER_E)):
        link(pool, bot, team[member], user)
        phones[key] = Reporter(pool, bot, user, after=drain)
    return SimpleNamespace(team=team, drain=drain, fake=bot.client, **phones)


def category_id(conn, code: str) -> int:
    return conn.execute("SELECT category_id FROM field.report_category WHERE code = %s", (code,)).fetchone()["category_id"]


def file_report(conn, core, member_id, *, category: str | None = "SERVICES", urgency: str = "normal",
                note: str = "chapakal kharab", photo: str | None = None, how: str = "user",
                now: datetime | None = None, area: tuple[str | None, str | None] | None = None):
    now = now or utcnow()
    member = conn.execute("SELECT * FROM field.member WHERE member_id = %s", (member_id,)).fetchone()
    r = reports.open_draft(conn, core, member, now=now)
    rid = r["report_id"]
    if area is not None:
        reports.set_area(conn, core, rid, area[0], area[1], "chosen", actor=member_id, now=now)
    if photo:
        item = media.Item(tg_kind="photo", kind="photo", file_id=photo, file_unique_id=photo, file_size=1000,
                          mime="image/jpeg", duration_s=None, width=10, height=10, media_group_id=None)
        asset = media.register(conn, member, item, now=now)
        reports.add_media(conn, rid, asset["asset_id"], actor=member_id, now=now)
    reports.add_note(conn, rid, note, actor=member_id, now=now)
    if category:
        reports.set_category(conn, rid, category_id(conn, category), actor=member_id, now=now)
    if urgency == "urgent":
        reports.set_urgency(conn, rid, "urgent", source="user", actor=member_id, now=now)
    return reports.file(conn, core, rid, how=how, actor=member_id, now=now)


def alerts_for(conn, report_id, kind: str = "alert"):
    return conn.execute(
        """SELECT a.*, m.display_name FROM field.alert_message a JOIN field.member m ON m.member_id = a.recipient_member_id
           WHERE a.report_id = %s AND a.kind = %s ORDER BY m.display_name""", (report_id, kind)).fetchall()
