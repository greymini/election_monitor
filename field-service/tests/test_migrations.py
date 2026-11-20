import shutil

import pytest

from pulse.migrate import MIGRATIONS_DIR, MigrationError, apply_migrations, discover


def test_migrations_are_numbered_field_sequence():
    names = [p.name for p in discover()]
    assert names[:2] == ["field_0001_team.sql", "field_0002_telegram.sql"]


def test_reapply_is_a_no_op(conn):
    assert apply_migrations(conn) == []


def test_all_tables_live_in_field_schema(conn):
    rows = conn.execute(
        """SELECT table_schema, table_name FROM information_schema.tables
           WHERE table_name IN ('member','member_scope','tg_link_token','tg_link','tg_update','tg_session')"""
    ).fetchall()
    assert len(rows) == 6
    assert {r["table_schema"] for r in rows} == {"field"}


def test_changed_applied_migration_is_refused(conn, tmp_path):
    for p in MIGRATIONS_DIR.glob("*.sql"):
        shutil.copy(p, tmp_path / p.name)
    first = tmp_path / "field_0001_team.sql"
    first.write_text(first.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="changed after it was applied"):
        apply_migrations(conn, tmp_path)


def test_crlf_checkout_is_not_a_change(conn, tmp_path):
    for p in MIGRATIONS_DIR.glob("*.sql"):
        text = p.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
        (tmp_path / p.name).write_bytes(text.encode("utf-8"))
    assert apply_migrations(conn, tmp_path) == []


def test_gap_in_numbers_is_refused(tmp_path):
    (tmp_path / "field_0001_a.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "field_0003_c.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError):
        discover(tmp_path)
