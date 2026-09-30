#!/usr/bin/env python
"""Static checks on db/migrations, as a stand-in for applying them.

`docker compose build` and a live Postgres are the real gate; where neither is
available (see DECISIONS.md D-002) this catches the mistakes that are cheap to
make and expensive to discover on the UAT box:

  1. Explicit transaction control. `db/apply_migrations.py` wraps each file in
     one transaction and commits on success, so a BEGIN or COMMIT inside a
     migration either nests or commits half the file.
  2. Unbalanced parentheses or dollar-quoted blocks - a truncated statement.
  3. Non-sequential or duplicated migration numbers, which decide apply order.
  4. A reference to a relation no earlier migration creates. This is a genuine
     check but a shallow one: it parses identifiers with regular expressions,
     not a SQL grammar, so it can miss a reference and can flag one that is
     fine. It is a smoke test, not a type checker.

    python scripts/lint_sql.py            # all migrations
    python scripts/lint_sql.py --verbose  # list what each file creates

Exit code 0 when clean, 1 when any check fails.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"

# Relations PostgreSQL and its extensions provide, which no migration creates.
BUILTIN = {
    "pg_roles", "pg_class", "pg_namespace", "pg_extension", "pg_indexes",
    "pg_stat_activity", "pg_matviews", "pg_tables", "pg_constraint", "pg_attribute",
    "information_schema.columns", "information_schema.tables", "spatial_ref_sys",
    "geometry_columns", "geography_columns",
}

CREATE_RE = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?(?:MATERIALIZED\s+)?(TABLE|VIEW|SEQUENCE)\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)
ALTER_RE = re.compile(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([a-z_][a-z0-9_]*)", re.IGNORECASE)
REFERENCES_RE = re.compile(r"\bREFERENCES\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)
FROM_JOIN_RE = re.compile(r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)
INDEX_ON_RE = re.compile(r"CREATE\s+(?:UNIQUE\s+)?INDEX[^;]*?\bON\s+([a-z_][a-z0-9_]*)",
                         re.IGNORECASE | re.DOTALL)
REFRESH_RE = re.compile(r"REFRESH\s+MATERIALIZED\s+VIEW\s+(?:CONCURRENTLY\s+)?"
                        r"([a-z_][a-z0-9_]*)", re.IGNORECASE)
NUMBER_RE = re.compile(r"^(\d{4})_")

# SQL keywords that follow FROM/JOIN and are not relations.
NOT_RELATIONS = {
    "select", "lateral", "unnest", "generate_series", "values", "only",
    "jsonb_each", "jsonb_array_elements", "json_each", "regexp_split_to_table",
    "string_to_table", "pg_catalog", "information_schema", "dual",
}


def strip_sql_comments(sql: str) -> str:
    """Remove -- line comments and /* */ blocks, leaving string literals alone.

    Needed because every migration in this repo opens with a comment header
    that names tables in prose; counting those as references produced false
    positives.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":  # string literal - copy verbatim, handling '' escapes
            out.append(ch)
            i += 1
            while i < n:
                out.append(sql[i])
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        out.append(sql[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                i += 1
            continue
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def check_balanced(name: str, sql: str) -> list[str]:
    problems = []
    if sql.count("(") != sql.count(")"):
        problems.append(
            f"{name}: unbalanced parentheses ({sql.count('(')} open, {sql.count(')')} close)"
        )
    if sql.count("$$") % 2 != 0:
        problems.append(f"{name}: odd number of $$ delimiters - a dollar-quoted block is unclosed")
    return problems


def check_no_transaction_control(name: str, sql: str) -> list[str]:
    problems = []
    for keyword in ("BEGIN", "COMMIT", "ROLLBACK", "START TRANSACTION"):
        # \bBEGIN\b also appears in PL/pgSQL bodies, which are inside $$ blocks;
        # only flag it outside one.
        outside = re.split(r"\$\$.*?\$\$", sql, flags=re.DOTALL)
        for chunk in outside:
            if re.search(rf"^\s*{keyword}\b", chunk, re.IGNORECASE | re.MULTILINE):
                problems.append(
                    f"{name}: contains {keyword}. apply_migrations.py already wraps each "
                    "file in a transaction; remove it."
                )
                break
    return problems


def check_numbering(paths: list[Path]) -> list[str]:
    problems = []
    seen: dict[int, str] = {}
    for path in paths:
        match = NUMBER_RE.match(path.name)
        if not match:
            problems.append(f"{path.name}: filename must start with a 4-digit number")
            continue
        number = int(match.group(1))
        if number in seen:
            problems.append(
                f"{path.name}: number {number:04d} already used by {seen[number]}. "
                "Apply order is by filename, so duplicates are ambiguous."
            )
        seen[number] = path.name
    if seen:
        expected = list(range(1, max(seen) + 1))
        missing = [n for n in expected if n not in seen]
        if missing:
            # Summarise rather than enumerate: one stray 9999_ file would
            # otherwise print several thousand numbers and bury the real faults.
            shown = ", ".join(f"{n:04d}" for n in missing[:10])
            if len(missing) > 10:
                shown += f", ... and {len(missing) - 10} more"
            problems.append(
                f"gap in migration numbering: {shown} missing. "
                "Not fatal, but check nothing was deleted."
            )
    return problems


def lint(verbose: bool = False) -> int:
    paths = sorted(MIGRATIONS.glob("*.sql"))
    if not paths:
        print(f"no migrations found in {MIGRATIONS}", file=sys.stderr)
        return 1

    problems: list[str] = check_numbering(paths)
    known: set[str] = set(BUILTIN)

    for path in paths:
        raw = path.read_text(encoding="utf-8")
        sql = strip_sql_comments(raw)
        name = path.name

        problems += check_balanced(name, sql)
        problems += check_no_transaction_control(name, sql)

        created = {m.group(2).lower() for m in CREATE_RE.finditer(sql)}
        known |= created
        if verbose:
            print(f"{name}: creates {', '.join(sorted(created)) or '(nothing)'}")

        referenced: set[str] = set()
        for pattern in (ALTER_RE, REFERENCES_RE, INDEX_ON_RE, REFRESH_RE):
            referenced |= {m.group(1).lower() for m in pattern.finditer(sql)}
        # FROM/JOIN only inside statements that actually query. In a GRANT or
        # REVOKE, 'FROM' is followed by a role name, not a relation
        # ("REVOKE ALL ON booth FROM giridih_ro"), and a role is not something
        # a migration creates with CREATE TABLE.
        for statement in sql.split(";"):
            head = statement.strip()[:24].upper()
            if head.startswith(("GRANT", "REVOKE", "ALTER DEFAULT", "CREATE ROLE", "DROP ROLE")):
                continue
            referenced |= {
                m.group(1).lower()
                for m in FROM_JOIN_RE.finditer(statement)
                if m.group(1).lower() not in NOT_RELATIONS
            }

        # Relations named in a GRANT/REVOKE ON clause are still worth checking.
        for m in re.finditer(r"\b(?:GRANT|REVOKE)\b[^;]*?\bON\s+(?:TABLE\s+)?"
                             r"([a-z_][a-z0-9_]*)", sql, re.IGNORECASE | re.DOTALL):
            candidate = m.group(1).lower()
            if candidate not in {"all", "schema", "database", "sequences", "tables", "functions"}:
                referenced.add(candidate)

        # A migration may reference what it creates itself, and CTEs look like
        # relations to a regex, so subtract names bound by WITH in this file.
        ctes = {m.lower() for m in re.findall(r"\b([a-z_][a-z0-9_]*)\s+AS\s*\(", sql, re.IGNORECASE)}
        unknown = sorted(referenced - known - ctes)
        for relation in unknown:
            problems.append(
                f"{name}: references relation '{relation}', which no earlier migration creates"
            )

    if problems:
        print(f"{len(problems)} problem(s):\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"{len(paths)} migration(s) linted, no problems found.")
    print("This is a static check only. It does not prove the SQL applies - "
          "run db.apply_migrations against a real Postgres for that.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verbose", action="store_true", help="list what each migration creates")
    args = ap.parse_args(argv)
    return lint(verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
