"""Amount extraction (LLD §5.1).

A message is an expense only if a number starts it, after an optional currency mark. Accepts ASCII
and Devanagari digits, Indian or Western comma grouping, decimals, the multipliers k / हजार /
hazar / hajar / लाख / lakh / lac, and the suffixes /- rs rupees रुपये. The rest of the message is
the description.

parse_amount(text) -> ParsedAmount, or None when the message does not start with an amount.
Raises AmountError for an amount that is present but unusable (zero, negative, malformed, ...).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from pulse.common.text import fold_digits

CONFIRM_ABOVE = Decimal("1000000")          # ₹10,00,000: ask the user to confirm
MAX_AMOUNT = Decimal("9999999999.99")       # numeric(12,2)
_CENT = Decimal("0.01")

_MULTIPLIERS = {
    "k": 1_000, "thousand": 1_000,
    "hazar": 1_000, "hajar": 1_000, "hazaar": 1_000, "hajaar": 1_000,
    "हजार": 1_000,                 # hajar (Devanagari)
    "हज़ार": 1_000,           # hajar with nukta
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
    "लाख": 100_000,                     # lakh (Devanagari)
}
_MULT_ALT = "|".join(sorted((re.escape(m) for m in _MULTIPLIERS), key=len, reverse=True))
_PREFIX = r"(?:₹|rs\.?|inr|रु\.?|रू\.?)"          # ₹ rs inr ru
_SUFFIX = (r"(?:/-|rs\.?|rupees?|rupaye|rupaiya|"
           r"रुपये|रुपया|रुपए|रु\.?)")
_BOUNDARY = r"(?=$|[\s,.;:!?)\-])"

_AMOUNT_RE = re.compile(
    rf"""^\s*
    (?P<sign1>[-−–]\s*)?
    (?:{_PREFIX}\s*)?
    (?P<sign2>[-−–]\s*)?
    (?P<num>\d[\d,]*(?:\.\d+)?|\.\d+)
    (?:\s*(?P<mult>{_MULT_ALT}){_BOUNDARY})?
    (?:\s*{_SUFFIX}{_BOUNDARY})?
    """,
    re.IGNORECASE | re.VERBOSE,
)
_VALID_GROUPING = re.compile(r"\d+|\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3}")


class AmountError(ValueError):
    """reason: zero | negative | decimals | too_large | malformed"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ParsedAmount:
    amount: Decimal
    description: str
    needs_confirm: bool


def parse_amount(text: str, *, confirm_above: Decimal = CONFIRM_ABOVE) -> ParsedAmount | None:
    if not text:
        return None
    s = fold_digits(unicodedata.normalize("NFC", text))
    m = _AMOUNT_RE.match(s)
    if m is None:
        return None
    rest = s[m.end():]
    # a number glued to other letters ("5kg", "2nd") or a time/date ("10:30", "12/11") is not an amount
    if rest and not rest[0].isspace() and rest[0] not in ",.;:!?)-":
        return None
    if re.match(r"^[:/.]\d", rest):
        return None

    if m.group("sign1") or m.group("sign2"):
        raise AmountError("negative")
    num = m.group("num")
    int_part = num.split(".")[0]
    if int_part and not _VALID_GROUPING.fullmatch(int_part):
        raise AmountError("malformed")
    try:
        value = Decimal(num.replace(",", ""))
    except InvalidOperation:
        raise AmountError("malformed") from None
    mult = m.group("mult")
    if mult:
        value *= _MULTIPLIERS[mult.lower()]
    if value == 0:
        raise AmountError("zero")
    if value != value.quantize(_CENT):
        raise AmountError("decimals")
    if value > MAX_AMOUNT:
        raise AmountError("too_large")

    description = re.sub(r"^[\s,.;:\-–]+", "", rest).strip()
    return ParsedAmount(amount=value.quantize(_CENT), description=description, needs_confirm=value > confirm_above)
