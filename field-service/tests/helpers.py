"""Test helpers. raw_txn writes money_txn directly: only tests do this, to build rows the ledger
service would refuse (CHECK and trigger tests) or every kind before slice 1c exists (parity)."""
from __future__ import annotations

import itertools
from datetime import date, timedelta
from decimal import Decimal

from pulse.bot.dispatch import dispatch
from pulse.team import linking
from tests import updates as U

_n = itertools.count(1)


def bucket_id(conn, code: str) -> int:
    return conn.execute("SELECT bucket_id FROM field.exp_bucket WHERE code = %s", (code,)).fetchone()["bucket_id"]


def raw_txn(conn, *, kind: str, amount: str = "100", created_by, from_member_id=None, to_member_id=None,
            bucket_id=None, status: str = "posted", fund_source=None, remark=None, suggest_source=None, **extra):
    cols = {
        "txn_no": f"T-{next(_n):06d}", "ac_id": "ac-32", "kind": kind, "amount": Decimal(amount),
        "txn_date": date.today(), "from_member_id": from_member_id, "to_member_id": to_member_id,
        "bucket_id": bucket_id, "status": status, "fund_source": fund_source, "remark": remark,
        "suggest_source": suggest_source if suggest_source or kind != "expense" else "user",
        "in_election_window": False, "created_by": created_by, "created_via": "web",
        **extra,
    }
    if status == "void":
        cols.update(void_reason=cols.get("void_reason", "test"), voided_by=created_by,
                    voided_at=cols.get("voided_at", "2026-11-19T00:00:00Z"))
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return conn.execute(f"INSERT INTO field.money_txn ({names}) VALUES ({marks}) RETURNING *",
                        list(cols.values())).fetchone()


def link(pool, bot, member_id, user=U.USER_A, update_id: int | None = None) -> None:
    """Link a member's Telegram account and clear what the fake client recorded."""
    with pool.connection() as conn, conn.transaction():
        issued = linking.create_link_token(conn, "ac-32", member_id, ttl=timedelta(hours=24))
    assert dispatch(pool, bot, U.start(update_id or 900_000 + next(_n), issued.token, user)) == "linked"
    bot.client.sent.clear()
    bot.client.cards.clear()
    bot.client.edits.clear()
    bot.client.answered.clear()
