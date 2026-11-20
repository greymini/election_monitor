"""The outbox stores no message bodies (slice 2c): alert_message.body holds a template name and ids or
codes, the text is drawn at send time, and the database refuses a body with text in it."""
from __future__ import annotations

import psycopg
import pytest

from pulse.bot import texts_hi as T
from pulse.notify import outbox, render
from tests import updates as U
from tests.helpers import link
from tests.test_flow_expense import Chat
from tests.triage_world import file_report


def _bodies(conn):
    return [r["body"] for r in conn.execute("SELECT body FROM field.alert_message ORDER BY alert_id")]


def test_every_queued_body_is_a_template(conn, core, pool, bot, team, drain):
    link(pool, bot, team["principal"], U.USER_A)
    link(pool, bot, team["incharge-a"], U.USER_B)
    principal, incharge = Chat(pool, bot, U.USER_A, after=drain), Chat(pool, bot, U.USER_B, after=drain)
    principal.say("दिया 5000 प्रभारी क")
    principal.tap(T.BTN_RECORD)                                          # ack_request to the in-charge
    incharge.tap(T.BTN_ACK_YES)                                          # ack notice back to the principal
    file_report(conn, core, team["worker-a1"], urgency="urgent")         # alert cards
    drain()
    bodies = _bodies(conn)
    assert {b["render"] for b in bodies} >= {"ack_request", "ack_notice", "report_card"}
    for b in bodies:
        assert set(b) <= render.ALLOWED_PARAMS
        assert "text" not in b and "markup" not in b
    # the text still reached the phones, drawn at send time
    assert any(T.BTN_ACK_YES in [x["text"] for row in (m["reply_markup"] or {}).get("inline_keyboard", []) for x in row]
               for m in bot.client.to(U.USER_B["id"]))


def test_database_refuses_a_text_body(conn, core, team):
    file_report(conn, core, team["worker-a1"], urgency="urgent")
    alert_id = conn.execute("SELECT alert_id FROM field.alert_message LIMIT 1").fetchone()["alert_id"]
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("""UPDATE field.alert_message SET body = '{"render": "nudge", "text": "डेमो"}' WHERE alert_id = %s""",
                     (alert_id,))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("""UPDATE field.alert_message SET body = '{"text": "डेमो"}' WHERE alert_id = %s""", (alert_id,))


def test_body_helper_allows_ids_and_codes_only():
    assert outbox.body("nudge") == {"render": "nudge"}
    with pytest.raises(ValueError):
        outbox.body("feedback", note="free text from a person")


def test_migration_redacts_old_bodies():
    from pulse.migrate import MIGRATIONS_DIR

    sql = (MIGRATIONS_DIR / "field_0009_hardening.sql").read_text(encoding="utf-8")
    assert "alert_message_no_text" in sql and "tg_edit_raw" in sql
