"""Display helpers: rupees with Indian digit grouping, dates in Asia/Kolkata."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pulse.bot import texts_hi


def group_indian(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def inr(amount: Decimal) -> str:
    """₹1,50,000 or ₹1,500.50 (paise shown only when non-zero)."""
    value = Decimal(amount).quantize(Decimal("0.01"))
    sign = "-" if value < 0 else ""
    value = abs(value)
    rupees = int(value)
    paise = int((value - rupees) * 100)
    text = f"₹{group_indian(rupees)}"
    if paise:
        text += f".{paise:02d}"
    return sign + text


def day_label(d: date, today: date) -> str:
    if d == today:
        return texts_hi.TODAY
    if (today - d).days == 1:
        return texts_hi.YESTERDAY
    return d.strftime("%d-%m-%Y")


def short_date(d: date) -> str:
    return d.strftime("%d-%m")
