from decimal import Decimal

import pytest

from pulse.ledger.parse_amount import AmountError, parse_amount


@pytest.mark.parametrize(
    "text,amount,description",
    [
        ("1500", "1500", ""),
        ("1,500", "1500", ""),
        ("1,50,000", "150000", ""),
        ("150,000", "150000", ""),
        ("१५००", "1500", ""),
        ("१,५०,००० टेंट", "150000", "टेंट"),
        ("1.5k", "1500", ""),
        ("1.5 k petrol", "1500", "petrol"),
        ("5K", "5000", ""),
        ("5 हजार", "5000", ""),
        ("5 हज़ार", "5000", ""),
        ("5 hazar", "5000", ""),
        ("5 hajar", "5000", ""),
        ("2.5 lakh", "250000", ""),
        ("2 लाख", "200000", ""),
        ("2 lac", "200000", ""),
        ("rs 500", "500", ""),
        ("Rs.1,500 tent", "1500", "tent"),
        ("₹500", "500", ""),
        ("₹ 500 chai", "500", "chai"),
        ("500/-", "500", ""),
        ("500/- chai", "500", "chai"),
        ("500 रुपये चाय", "500", "चाय"),
        ("1500 chai nashta", "1500", "chai nashta"),
        ("1500 - chai", "1500", "chai"),
        ("150.50 biscuit", "150.50", "biscuit"),
    ],
)
def test_accepted_forms(text, amount, description):
    p = parse_amount(text)
    assert p is not None
    assert p.amount == Decimal(amount)
    assert isinstance(p.amount, Decimal)
    assert p.description == description
    assert p.needs_confirm is False


@pytest.mark.parametrize("text,reason", [
    ("0", "zero"),
    ("0.00 chai", "zero"),
    ("0 hazar", "zero"),
    ("-500", "negative"),
    ("− 500 chai", "negative"),
    ("₹ -20", "negative"),
    ("10.555", "decimals"),
    ("1,5", "malformed"),
    ("1,5000", "malformed"),
    ("100000 lakh", "too_large"),
])
def test_rejects(text, reason):
    with pytest.raises(AmountError) as e:
        parse_amount(text)
    assert e.value.reason == reason


@pytest.mark.parametrize("text", [
    "chai 500",          # number not at the start
    "kal 1500 diya",
    "नमस्ते",
    "",
    "rs",
    "5kg aloo",          # number glued to a word
    "2nd meeting",
    "10:30 baje",        # a time
    "12/11 ko",          # a date
])
def test_not_an_expense(text):
    assert parse_amount(text) is None


def test_above_ten_lakh_needs_confirmation():
    assert parse_amount("10,00,000").needs_confirm is False          # exactly the limit: no
    assert parse_amount("10,00,001").needs_confirm is True
    assert parse_amount("15 लाख tent").needs_confirm is True
    assert parse_amount("15 lakh", confirm_above=Decimal("2000000")).needs_confirm is False
