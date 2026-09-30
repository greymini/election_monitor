"""Every relation the code selects from must exist in a migration.

This test exists because of a mistake made while building the dashboard: two new
endpoints were added that queried `candidate_profile`, `local_office_holder`,
`organisation` and `political_event` - tables the spec assigns to a later
migration that had not been written. The whole suite passed, `ruff` passed, the
SQL linter passed (it only reads `db/migrations`), and the frontend built. The
first sign of trouble would have been a 500 on a live page.

With no database in the build environment nothing else would catch it, so this
closes the gap statically: it extracts every relation named in an SQL literal
across the first-party packages and checks it against what the migrations
create.

A note on how it reads the code, because the first attempt got this wrong. It
scanned whole files as text, which matches `from` in prose and in every Python
import, and produced 58 spurious failures including 'the', 'without' and
'pathlib'. It now walks the AST and keeps only string literals that *start* with
a SQL verb, which is how every query in this codebase is written. Python joins
adjacent string literals into one node, so concatenated queries arrive whole.

It is still regex-based over the SQL itself, like `scripts/lint_sql.py`, and is
a smoke test rather than a parser. A false positive is fixed by adding the name
to `KNOWN_NON_RELATIONS` with a reason.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"

PACKAGES = ("api", "analytics", "ingest", "news", "worker", "chatbot", "db")

# Things that look like relations to a regex and are not.
KNOWN_NON_RELATIONS = {
    # PostgreSQL catalogue and information schema.
    "pg_extension", "pg_roles", "pg_class", "pg_indexes", "pg_matviews",
    "pg_tables", "pg_stat_activity", "pg_available_extensions", "pg_namespace",
    "information_schema", "columns", "tables",
    # PostGIS.
    "spatial_ref_sys", "geometry_columns",
    # Set-returning functions and syntax the pattern mistakes for a table.
    "unnest", "generate_series", "values", "lateral", "jsonb_each",
    "jsonb_array_elements", "regexp_split_to_table", "string_to_table",
    "json_each", "dual", "only", "select", "make_interval",
    # CTE and subquery alias names. A CTE is defined inside its own statement,
    # so the reference is real but not to a stored relation.
    "totals", "ranked", "pivot", "meta", "shares", "windows", "changes",
    "quality", "legs", "pairs", "baseline", "inputs", "weighted", "lineage",
    "new_voters", "party_totals", "electors_at", "electors_start",
    "booth_uid_map", "prev", "now", "ls", "vs", "cw", "nv", "jl", "sh", "t",
    "keep", "rq", "by_party", "contenders", "sub", "revs",
    # `INSERT ... ON CONFLICT DO UPDATE SET` puts SET where a table name
    # would otherwise be.
    "set",
}

RELATION_PATTERNS = (
    re.compile(r"\bFROM\s+([a-z_][a-z0-9_]*)", re.IGNORECASE),
    re.compile(r"\bJOIN\s+([a-z_][a-z0-9_]*)", re.IGNORECASE),
    re.compile(r"\bINSERT\s+INTO\s+([a-z_][a-z0-9_]*)", re.IGNORECASE),
    re.compile(r"\bUPDATE\s+([a-z_][a-z0-9_]*)", re.IGNORECASE),
    re.compile(r"\bDELETE\s+FROM\s+([a-z_][a-z0-9_]*)", re.IGNORECASE),
    re.compile(
        r"\bREFRESH\s+MATERIALIZED\s+VIEW\s+(?:CONCURRENTLY\s+)?([a-z_][a-z0-9_]*)",
        re.IGNORECASE,
    ),
)

CREATE_PATTERN = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:UNIQUE\s+)?(?:MATERIALIZED\s+)?"
    r"(?:TEMP(?:ORARY)?\s+|UNLOGGED\s+)?(?:TABLE|VIEW|SEQUENCE)\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)

# A string is SQL only if it starts with a SQL verb.
SQL_START = re.compile(r"\s*(WITH|SELECT|INSERT|UPDATE|DELETE|REFRESH)\b", re.IGNORECASE)

# `IS [NOT] DISTINCT FROM x` is a comparison operator, not a FROM clause.
DISTINCT_FROM = re.compile(r"IS\s+(?:NOT\s+)?DISTINCT\s+FROM", re.IGNORECASE)

# SQL comments inside a query string. Several queries here carry a comment
# explaining which audit finding the clause addresses, and prose like
# "... from the most recent mother roll" matched the FROM pattern.
SQL_COMMENT = re.compile("--.*")


def migration_relations() -> set[str]:
    """Every relation any migration creates."""
    found: set[str] = set()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        found |= {
            m.group(1).lower()
            for m in CREATE_PATTERN.finditer(path.read_text(encoding="utf-8"))
        }
    return found


def python_sql_strings(path: Path) -> list[str]:
    """SQL string literals in a Python file, whole."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # pragma: no cover
        return []

    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if SQL_START.match(node.value):
                out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            # f-string: the literal parts carry the table names; the
            # interpolated parts are clause fragments built in code.
            joined = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if SQL_START.match(joined):
                out.append(joined)
    return out


def referenced_relations() -> dict[str, set[str]]:
    """relation -> the files that name it."""
    out: dict[str, set[str]] = {}
    for package in PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for blob in python_sql_strings(path):
                cleaned = SQL_COMMENT.sub(" ", blob)
                cleaned = DISTINCT_FROM.sub(" = ", cleaned)
                for pattern in RELATION_PATTERNS:
                    for match in pattern.finditer(cleaned):
                        name = match.group(1).lower()
                        if name in KNOWN_NON_RELATIONS:
                            continue
                        out.setdefault(name, set()).add(path.relative_to(ROOT).as_posix())
    return out


REFERENCED = referenced_relations()


def test_the_scan_found_the_relations_it_should():
    """Keeps the test below honest: a pattern matching nothing would pass it."""
    for expected in ("booth", "election", "result_booth", "mv_result_booth_wide", "ac"):
        assert expected in REFERENCED, f"the scan missed {expected}"
    assert len(REFERENCED) > 15


def test_the_scan_does_not_pick_up_prose_or_imports():
    """The failure mode of the first version. 'the', 'without' and 'pathlib'
    are not tables."""
    for noise in ("the", "without", "pathlib", "typing", "psycopg", "whichever"):
        assert noise not in REFERENCED, f"{noise!r} was scanned as a relation"


def test_the_migrations_create_the_relations_they_should():
    created = migration_relations()
    for expected in ("booth", "election", "ac", "party_alias", "mv_ac_summary",
                     "candidate_profile", "booth_lineage", "local_office_holder",
                     "organisation", "political_event"):
        assert expected in created, f"no migration creates {expected}"


@pytest.mark.parametrize(
    ("relation", "files"),
    sorted((r, sorted(f)) for r, f in REFERENCED.items()),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_every_referenced_relation_exists_in_a_migration(relation, files):
    created = migration_relations()
    assert relation in created, (
        f"{relation!r} is queried by {', '.join(files)} but no migration creates it. "
        "Either add it to a migration, or - if this is a CTE, an alias or a "
        "set-returning function - add it to KNOWN_NON_RELATIONS with a reason."
    )
