"""The SQL that people and the chatbot copy from must actually run.

analytics/metrics.sql (the analyst crib sheet) and the example queries in
chatbot/prompts/schema_doc.md were written against the pre-0014/0015 views and
named columns that no longer exist (votes_counted, electors_now, an area_id on
mv_new_voter_share). Nothing executed them, so they rotted unnoticed - and the
schema doc is what the assistant writes its queries from.
"""

from __future__ import annotations

import re

import pytest

from tests.e2e.conftest import requires_db
from tests.paths import BACKEND

pytestmark = requires_db


def _statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def _crib_sheet():
    return _statements((BACKEND / "analytics" / "metrics.sql").read_text(encoding="utf-8"))


def _schema_doc_examples():
    doc = (BACKEND / "chatbot" / "prompts" / "schema_doc.md").read_text(encoding="utf-8")
    out = []
    for block in re.findall(r"```sql\n(.*?)```", doc, re.S):
        out.extend(_statements(block))
    return out


CASES = ([("metrics.sql", i, q) for i, q in enumerate(_crib_sheet(), 1)]
         + [("schema_doc.md", i, q) for i, q in enumerate(_schema_doc_examples(), 1)])


@pytest.mark.parametrize("source, number, statement", CASES,
                         ids=[f"{s}#{n}" for s, n, _ in CASES])
def test_the_reference_query_runs(conn, loaded_dataset, source, number, statement):
    assert statement.lstrip().upper().startswith(("SELECT", "WITH")), statement[:80]
    with conn.cursor() as cur:
        cur.execute("BEGIN READ ONLY")
        try:
            cur.execute(statement)
            cur.fetchall()
        finally:
            cur.execute("ROLLBACK")
