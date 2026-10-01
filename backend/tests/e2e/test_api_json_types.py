"""Numbers leave the API as JSON numbers.

PostgreSQL NUMERIC arrives in Python as `decimal.Decimal`, and FastAPI
(pydantic 2) serialises a Decimal as a *string*. So margin_pct, turnout_pct,
priority_score and every other NUMERIC column reached the browser as "25.29".
The frontend formats with `value.toFixed(...)`, which throws on a string, so
the Overview and the booth drawer crashed against the real API while the
fixtures - which hold real numbers - rendered fine.
"""

from __future__ import annotations

import re

import pytest

from tests.e2e.conftest import requires_db
from tests.e2e.test_api_contract import call, endpoints

pytestmark = requires_db

DECIMAL_STRING = re.compile(r"-?\d+\.\d+")


def _decimal_strings(obj, path="$"):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _decimal_strings(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for index, value in enumerate(obj[:200]):
            yield from _decimal_strings(value, f"{path}[{index}]")
    elif isinstance(obj, str) and DECIMAL_STRING.fullmatch(obj):
        yield path


GETS = [e for e in endpoints() if e.method == "GET" and not e.is_legacy]


@pytest.mark.parametrize("endpoint", GETS, ids=str)
def test_no_number_is_serialised_as_a_string(client, tokens, ids, endpoint):
    response = call(client, endpoint, ids, tokens["admin"])
    if response.status_code != 200 or "json" not in response.headers.get("content-type", ""):
        pytest.skip(f"{endpoint} answered {response.status_code}")
    offenders = sorted({re.sub(r"\[\d+\]", "[]", p) for p in _decimal_strings(response.json())})
    assert not offenders, f"{endpoint} sends numbers as strings at: {offenders[:10]}"
