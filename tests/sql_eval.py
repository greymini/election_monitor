"""Evaluating a generated metric function in PostgreSQL, and comparing results.

Shared by the two suites that execute the SQL side of the parity check against
different databases:

  * `tests/test_metric_functions_sql.py` runs against a throwaway PostgreSQL
    started in-process by `pgserver`, with **only** the generated function block
    applied. No extensions, no schema, no views. It needs no Docker and no
    administrator rights, so it runs wherever the test suite runs.
  * `tests/e2e/test_metric_parity_sql.py` runs against a real database with the
    full schema from `db/migrations`, which is the only place the views can be
    exercised.

Both must call the functions identically, hence one module. The alternative -
each suite with its own copy of the call construction - would let one of them go
on testing an old signature after a parameter type changed, which is the same
class of drift the functions themselves exist to prevent.
"""

from __future__ import annotations

import decimal

from analytics import metric_sql


def call_sql(cur, name: str, args: tuple) -> object:
    """Evaluate one generated function, casting each placeholder to its declared
    parameter type.

    The casts are derived from the declaration rather than written per call.
    PostgreSQL resolves function arguments using **implicit** casts only, and
    several natural argument types are not implicitly convertible to the
    declared ones: `COUNT(*)` is `bigint` and int8 does not become int4,
    `PERCENT_RANK()` is `double precision` and float8 does not become numeric,
    `booth_crosswalk.confidence` is `real` and float8 does not become real
    either. Deriving the casts means a type change in `metric_sql.py` cannot
    leave a test calling a signature that no longer exists - it would fail to
    resolve, loudly, instead of silently testing something else.
    """
    fn = next((f for f in metric_sql.METRIC_FUNCTIONS if f.name == name), None)
    if fn is None:
        raise AssertionError(
            f"{name} is not declared in analytics/metric_sql.py, so there is "
            "nothing to compare against"
        )
    if len(args) != len(fn.args):
        raise AssertionError(
            f"{name} takes {len(fn.args)} argument(s) "
            f"({fn.signature()}), the case supplies {len(args)}"
        )
    placeholders = ", ".join(f"%s::{typ}" for _, typ in fn.args)
    cur.execute(f"SELECT {name}({placeholders}) AS value", list(args))
    return cur.fetchone()["value"]


def comparable(value: object) -> object:
    """Normalise a value returned by PostgreSQL for comparison with Python.

    `NUMERIC` arrives as `decimal.Decimal`, which does not compare cleanly with
    `float`. Booleans and `None` pass through untouched, because the whole point
    of most of these cases is that `None` stays `None`.
    """
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


def create_functions(cur) -> int:
    """Apply just the generated function block from the metrics migration.

    Used by the pgserver-backed suite, where there is no schema to apply the
    whole migration into. The block is extracted from the migration file rather
    than regenerated in memory, so what runs here is the text that is committed -
    if the two had drifted, `test_metric_parity.py` would already have failed,
    and reading the file keeps this from being the one place that hides it.

    Returns the number of functions created, so a caller can assert it is not
    accidentally exercising an empty database.
    """
    text = metric_sql.migration_path().read_text(encoding="utf-8")
    start = text.index(metric_sql.BEGIN_MARKER)
    end = text.index(metric_sql.END_MARKER)
    cur.execute(text[start:end])
    return len(metric_sql.METRIC_FUNCTIONS)
