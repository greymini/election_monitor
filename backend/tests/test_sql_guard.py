"""The chatbot SQL guard (LLD 8.2). Every rejection here is a query the model
could otherwise have run against the production database."""

import pytest

from chatbot.sql_guard import ALLOWED_TABLES, MAX_ROWS, SqlRejected, guard


def test_plain_select_passes_and_gets_a_limit():
    g = guard("SELECT booth_uid, jmm, bjp FROM mv_result_booth_wide")
    assert g.limit == MAX_ROWS
    assert "LIMIT 500" in g.sql.upper()
    assert g.tables == ["mv_result_booth_wide"]


def test_existing_small_limit_is_kept():
    g = guard("SELECT booth_uid FROM booth LIMIT 10")
    assert g.limit == 10
    assert g.rewritten is False


def test_oversized_limit_is_lowered():
    g = guard("SELECT booth_uid FROM booth LIMIT 100000")
    assert g.limit == MAX_ROWS
    assert g.rewritten is True


def test_cte_and_join_across_allowed_tables():
    g = guard(
        "WITH top AS (SELECT booth_uid, margin_pct FROM mv_booth_priority) "
        "SELECT t.booth_uid, a.name_hi, t.margin_pct FROM top t "
        "JOIN booth b ON b.booth_uid = t.booth_uid JOIN area a ON a.area_id = b.area_id"
    )
    assert set(g.tables) <= ALLOWED_TABLES
    assert "top" not in g.tables


@pytest.mark.parametrize("sql", [
    "INSERT INTO booth (booth_uid) VALUES ('X')",
    "UPDATE booth SET building = 'x'",
    "DELETE FROM booth",
    "DROP TABLE booth",
    "CREATE TABLE evil (a int)",
    "ALTER TABLE booth ADD COLUMN x int",
])
def test_writes_are_rejected(sql):
    with pytest.raises(SqlRejected):
        guard(sql)


def test_multiple_statements_are_rejected():
    with pytest.raises(SqlRejected, match="one statement"):
        guard("SELECT 1 FROM booth; DROP TABLE booth")


@pytest.mark.parametrize("table", ["app_user", "llm_prompt_log", "auth_otp", "news_item",
                                   "ground_report", "review_queue", "caste_survey"])
def test_tables_outside_the_allow_list_are_rejected(table):
    """These hold personal data, prompts, credentials or free-text ground
    reports. News and ground notes are reached through search_news, which
    applies its own filters, never through raw SQL."""
    with pytest.raises(SqlRejected, match="not available"):
        guard(f"SELECT * FROM {table}")


@pytest.mark.parametrize("sql", [
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT * FROM dblink('host=evil', 'select 1') AS t(a int)",
    "SELECT pg_sleep(30) FROM booth",
])
def test_file_and_network_functions_are_rejected(sql):
    with pytest.raises(SqlRejected):
        guard(sql)


def test_unknown_function_is_rejected():
    with pytest.raises(SqlRejected, match="not available to the assistant"):
        guard("SELECT some_custom_fn(votes) FROM mv_result_booth_party")


def test_allowed_analytic_functions_pass():
    g = guard(
        "SELECT booth_uid, RANK() OVER (ORDER BY margin_pct) AS r, "
        "ROUND(AVG(margin_pct) OVER (), 2) AS avg_margin FROM mv_booth_priority"
    )
    assert g.limit == MAX_ROWS


def test_other_schemas_are_rejected():
    with pytest.raises(SqlRejected):
        guard("SELECT * FROM pg_catalog.pg_tables")


def test_subquery_tables_are_checked_too():
    with pytest.raises(SqlRejected, match="not available"):
        guard("SELECT booth_uid FROM booth WHERE booth_uid IN (SELECT ref FROM review_queue)")


def test_guard_matches_the_database_grant_list():
    """If the guard and the GRANT drift apart they disagree about what is
    readable, and one of them is wrong."""
    import re

    from tests.paths import MIGRATIONS

    granted: set[str] = set()
    for name in ("0012_roles_grants.sql", "0021_readonly_grants.sql"):
        body = (MIGRATIONS / name).read_text(encoding="utf-8")
        block = re.search(r"GRANT SELECT ON(.*?)TO giridih_ro;", body, re.S)
        assert block, f"GRANT block not found in {name}"
        granted |= {
            line.strip().rstrip(",")
            for line in block.group(1).splitlines()
            if line.strip() and not line.strip().startswith("--")
        }
    assert granted == ALLOWED_TABLES, (
        f"only in GRANT: {sorted(granted - ALLOWED_TABLES)}; "
        f"only in guard: {sorted(ALLOWED_TABLES - granted)}"
    )
