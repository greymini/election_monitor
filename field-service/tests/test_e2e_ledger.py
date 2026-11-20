"""End to end from recorded updates (LLD §13): the principal gives ₹50,000 to an in-charge, who
acknowledges and passes ₹20,000 to a worker, who acknowledges, logs three expenses and returns the
rest. Every balance reconciles and spend equals only the three expenses."""
from decimal import Decimal

from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.bot import texts_hi as T
from pulse.bot.ui import CB_MENU_GIVE, CB_MENU_ME
from pulse.metrics.ledger import member_balance_sql, spend_total_sql
from tests import updates as U
from tests.helpers import link
from tests.test_flow_expense import Chat

D = Decimal


def test_money_round_trip_reconciles(pool, conn, bot, core, settings, fake_tg, team, drain):
    link(pool, bot, team["principal"], U.USER_A)
    link(pool, bot, team["incharge-a"], U.USER_B)
    link(pool, bot, team["worker-a1"], U.USER_C)
    principal, incharge, worker = (Chat(pool, bot, u, after=drain) for u in (U.USER_A, U.USER_B, U.USER_C))

    # principal → in-charge, by the typed shortcut
    assert principal.say("दिया 50000 प्रभारी क") == "name_matched"
    assert principal.tap(T.BTN_RECORD) == "posted"
    assert incharge.tap(T.BTN_ACK_YES) == "acknowledged"

    # in-charge → worker, by the menu
    incharge.say("namaste")
    assert incharge.tap_data(CB_MENU_GIVE, incharge.mine[-1]["message_id"]) == "pick_person"
    assert incharge.tap("डेमो कार्यकर्ता क1") == "await_amount"
    assert incharge.say("20,000") == "confirm"
    assert incharge.tap(T.BTN_RECORD) == "posted"
    assert worker.tap(T.BTN_ACK_YES) == "acknowledged"

    # three expenses
    for text in ("1500 chai nashta", "3200 petrol bolero", "800 banner"):
        assert worker.say(text) == "confirm"
        assert worker.tap(T.BTN_CONFIRM) == "posted"

    # return the rest
    worker.say("namaste")
    worker.tap_data(CB_MENU_ME, worker.mine[-1]["message_id"])
    assert worker.tap(T.BTN_RETURN) == "await_amount"
    assert worker.text == T.ASK_RETURN_AMOUNT.format(name="डेमो प्रभारी क", balance="₹14,500")
    worker.say("14500")
    assert worker.tap(T.BTN_RECORD) == "posted"
    assert incharge.tap(T.BTN_ACK_YES) == "acknowledged"

    bal = {k: conn.execute(member_balance_sql(), {"member_id": team[k]}).fetchone()["balance"]
           for k in ("principal", "incharge-a", "worker-a1")}
    assert bal == {"principal": D("-50000.00"), "incharge-a": D("44500.00"), "worker-a1": D("0.00")}
    spend = conn.execute(spend_total_sql()).fetchone()["spend_total"]
    assert spend == D("5500.00")                                   # only the three expenses
    assert sum(bal.values()) == -spend                             # nothing came from outside, so it closes
    kinds = sorted(r["kind"] + ":" + r["status"] for r in conn.execute("SELECT kind, status FROM field.money_txn"))
    assert kinds == ["advance:posted"] * 2 + ["expense:posted"] * 3 + ["return:posted"]

    # the API reads the same numbers
    with TestClient(create_app(settings, pool=pool, core=core, telegram_client=fake_tg)) as api:
        api.headers["Authorization"] = f"Bearer {core.issue_token('usr-principal')}"
        rows = {m["display_name"]: m for m in api.get("/acs/32/field/balances").json()["members"]}
        assert rows["डेमो प्रत्याशी"]["balance"] is None                # the source: not shown
        assert rows["डेमो प्रत्याशी"]["passed_on"] == "50000.00"
        assert rows["डेमो प्रभारी क"]["balance"] == "44500.00"
        assert rows["डेमो प्रभारी क"]["received"] == "64500.00"         # 50,000 + 14,500 returned
        assert rows["डेमो कार्यकर्ता क1"]["balance"] == "0.00"
        assert rows["डेमो कार्यकर्ता क1"]["spent"] == "5500.00"
        totals = api.get("/acs/32/field/spend/summary").json()["totals"]
        assert totals["spend_total"] == "5500.00" and totals["entries"] == 3
        assert totals["in_transit"] == "0.00" and totals["disputed"] == "0.00"
