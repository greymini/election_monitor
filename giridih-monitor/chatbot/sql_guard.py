"""Validate model-proposed SQL before it reaches Postgres (LLD 8.2).

The model never touches the database. It proposes SQL, this module parses it
with sqlglot and either rewrites it into something provably safe or rejects it.

Rules:
  * exactly one statement, and it must be a SELECT (CTEs allowed, all read-only)
  * every table referenced must be on ALLOWED_TABLES
  * no DML, DDL, transaction control, or INTO
  * no function outside FUNCTION_ALLOWLIST - this blocks pg_read_file,
    dblink, lo_import, pg_sleep and friends
  * a LIMIT is imposed if absent, and lowered if it exceeds MAX_ROWS

This is the first of two layers. The second is the giridih_ro role, which is
GRANTed only these same tables (db/migrations/0012_roles_grants.sql), so a
defect here still cannot read app_user, prompts or news bodies.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

MAX_ROWS = 500
STATEMENT_TIMEOUT_MS = 5000

# Must stay in step with the GRANT list in 0012_roles_grants.sql.
ALLOWED_TABLES = {
    "block", "area", "booth", "booth_crosswalk",
    "election", "party", "candidate",
    "community", "demography", "caste_estimate",
    "local_result",
    "roll_revision", "roll_snapshot", "roll_change",
    "mv_result_booth_party", "mv_booth_party_share", "mv_result_booth_wide",
    "mv_swing", "mv_transfer_ls_vs", "mv_volatility",
    "mv_new_voter_share", "mv_floating_vote", "mv_booth_priority", "mv_area_rollup",
}

FUNCTION_ALLOWLIST = {
    # aggregates and windows
    "sum", "avg", "min", "max", "count", "round", "abs", "stddev", "stddev_samp",
    "stddev_pop", "var_samp", "var_pop", "corr", "percentile_cont", "percentile_disc",
    "rank", "dense_rank", "row_number", "ntile", "lag", "lead", "first_value", "last_value",
    "percent_rank", "cume_dist", "greatest", "least", "coalesce", "nullif",
    # text and casting
    "lower", "upper", "trim", "btrim", "ltrim", "rtrim", "length", "substring", "substr",
    "concat", "concat_ws", "replace", "split_part", "left", "right", "cast", "to_char",
    "string_agg", "array_agg", "unnest",
    # dates
    "now", "date_trunc", "extract", "age", "date_part", "make_date",
    # maths
    "floor", "ceil", "ceiling", "power", "sqrt", "mod", "div", "exp", "ln", "log",
}

BLOCKED_KEYWORDS = (
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "lo_import", "lo_export",
    "dblink", "copy ", "pg_sleep", "pg_terminate_backend", "current_setting",
    "set_config", "pg_stat_file", "pg_logdir_ls",
)


class SqlRejected(ValueError):
    """The proposed SQL cannot be run. The message is shown to the model so it
    can correct itself, so it says what is wrong and what is allowed."""


@dataclass
class GuardedSql:
    sql: str
    tables: list[str]
    limit: int
    rewritten: bool


def _tables_in(tree: exp.Expression) -> tuple[set[str], set[str]]:
    """Return (real table names, CTE names). CTE names are not real tables."""
    cte_names = {
        cte.alias_or_name.lower()
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }
    tables = set()
    for table in tree.find_all(exp.Table):
        name = (table.name or "").lower()
        if not name:
            continue
        if table.db and table.db.lower() not in {"public", ""}:
            raise SqlRejected(
                f"schema {table.db!r} is not accessible; query the public schema only"
            )
        tables.add(name)
    return tables, cte_names


def _check_functions(tree: exp.Expression) -> None:
    for node in tree.find_all(exp.Anonymous):
        name = (node.this or "").lower() if isinstance(node.this, str) else ""
        if name and name not in FUNCTION_ALLOWLIST:
            raise SqlRejected(
                f"function {name}() is not available to the assistant. "
                f"Use plain aggregates, window functions and string or date functions."
            )


def guard(sql: str, max_rows: int = MAX_ROWS) -> GuardedSql:
    """Validate and rewrite. Raises SqlRejected with an explanation the model
    can act on."""
    if not sql or not sql.strip():
        raise SqlRejected("empty query")

    lowered = sql.lower()
    for word in BLOCKED_KEYWORDS:
        if word in lowered:
            raise SqlRejected(f"{word.strip()!r} is not permitted")

    try:
        statements = sqlglot.parse(sql, read="postgres")
    except Exception as exc:
        raise SqlRejected(f"could not parse the SQL: {exc}") from exc

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SqlRejected(
            f"send exactly one statement, not {len(statements)}; "
            f"semicolon-separated batches are not allowed"
        )

    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Subquery)):
        raise SqlRejected(f"only SELECT is allowed, got {type(tree).__name__.upper()}")

    for forbidden in (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create,
                      exp.Alter, exp.Grant, exp.Command):
        if tree.find(forbidden) is not None:
            raise SqlRejected(
                f"{forbidden.__name__.upper()} is not allowed; this is a read-only tool"
            )
    if tree.find(exp.Into) is not None:
        raise SqlRejected("SELECT ... INTO is not allowed")

    tables, ctes = _tables_in(tree)
    unknown = sorted(t for t in tables if t not in ALLOWED_TABLES and t not in ctes)
    if unknown:
        raise SqlRejected(
            f"table(s) not available to the assistant: {', '.join(unknown)}. "
            f"Available: {', '.join(sorted(ALLOWED_TABLES))}"
        )

    _check_functions(tree)

    rewritten = False
    limit_node = tree.args.get("limit") if isinstance(tree, exp.Select) else None
    effective = max_rows
    if limit_node is not None and isinstance(limit_node.expression, exp.Literal):
        try:
            requested = int(limit_node.expression.this)
            effective = min(requested, max_rows)
            rewritten = effective != requested
        except (TypeError, ValueError):
            rewritten = True
    else:
        rewritten = True

    tree = tree.limit(effective)
    return GuardedSql(
        sql=tree.sql(dialect="postgres", pretty=False),
        tables=sorted(tables - ctes),
        limit=effective,
        rewritten=rewritten,
    )


def run(sql: str, max_rows: int = MAX_ROWS) -> tuple[list[dict], GuardedSql]:
    """Guard, then execute as the read-only role with a hard timeout."""
    from common.db import connection

    guarded = guard(sql, max_rows)
    with connection(readonly=True) as conn, conn.cursor() as cur:
        cur.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
        cur.execute(guarded.sql)
        rows = cur.fetchall() if cur.description else []
    return rows, guarded


def to_csv(rows: list[dict], max_chars: int = 24000) -> str:
    """Rows as CSV for the model. Truncation is marked explicitly so the model
    does not total a partial table."""
    import csv
    import io

    if not rows:
        return "(no rows)"
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
        if buf.tell() > max_chars:
            buf.write(f"... truncated, {len(rows)} row(s) total - narrow the query\n")
            break
    return buf.getvalue()
