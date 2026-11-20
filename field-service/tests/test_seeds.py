import psycopg
import pytest

from pulse.seed import apply_seeds

TOP = ["EVENT", "TRAVEL", "FOOD", "TEAM", "PUBLICITY", "OFFICE", "SOCIAL", "ASSIST", "DONATE", "WORKS", "PARTY", "OTHER"]


def _counts(conn):
    return (conn.execute("SELECT count(*) AS n FROM field.exp_bucket").fetchone()["n"],
            conn.execute("SELECT count(*) AS n FROM field.exp_keyword").fetchone()["n"])


def test_twelve_buckets_in_preset_order(conn):
    rows = conn.execute("SELECT code FROM field.exp_bucket WHERE parent_id IS NULL ORDER BY sort").fetchall()
    assert [r["code"] for r in rows] == TOP


def test_flags_and_statutory_head(conn):
    rows = {r["code"]: r for r in conn.execute("SELECT * FROM field.exp_bucket WHERE parent_id IS NULL")}
    assert {c for c, r in rows.items() if r["sensitive"]} == {"SOCIAL", "ASSIST", "DONATE", "WORKS"}
    assert {c for c, r in rows.items() if r["ask_reach"]} == {"EVENT", "SOCIAL", "ASSIST", "DONATE", "WORKS"}
    assert all(r["statutory_head"] is None for r in conn.execute("SELECT statutory_head FROM field.exp_bucket"))
    assert rows["FOOD"]["name_hi"] == "भोजन और नाश्ता" and rows["FOOD"]["name_en"] == "Food and hospitality"


def test_subheads(conn):
    subs = conn.execute(
        """SELECT p.code, count(*) AS n FROM field.exp_bucket s JOIN field.exp_bucket p ON p.bucket_id = s.parent_id
           GROUP BY p.code"""
    ).fetchall()
    counts = {r["code"]: r["n"] for r in subs}
    assert counts == {"EVENT": 5, "TRAVEL": 5, "FOOD": 3, "TEAM": 4, "PUBLICITY": 6, "OFFICE": 5,
                      "SOCIAL": 4, "ASSIST": 6, "DONATE": 5, "WORKS": 4, "PARTY": 4}


def test_keywords_cover_every_bucket_but_other_in_both_scripts(conn):
    rows = conn.execute(
        """SELECT b.code, k.script, count(*) AS n FROM field.exp_keyword k JOIN field.exp_bucket b USING (bucket_id)
           GROUP BY 1, 2"""
    ).fetchall()
    have = {(r["code"], r["script"]): r["n"] for r in rows}
    for code in TOP[:-1]:
        assert have.get((code, "latn"), 0) >= 10, code
        assert have.get((code, "deva"), 0) >= 10, code
    assert not any(code == "OTHER" for code, _ in have)


def test_seeding_is_idempotent_and_keeps_admin_changes(conn):
    before = _counts(conn)
    conn.execute("UPDATE field.exp_bucket SET statutory_head = 'Vehicles' WHERE code = 'TRAVEL'")
    try:
        apply_seeds(conn)
        apply_seeds(conn)
        assert _counts(conn) == before
        assert conn.execute("SELECT statutory_head FROM field.exp_bucket WHERE code = 'TRAVEL'").fetchone()["statutory_head"] == "Vehicles"
    finally:
        conn.execute("UPDATE field.exp_bucket SET statutory_head = NULL WHERE code = 'TRAVEL'")


def test_two_levels_only(conn):
    sub = conn.execute("SELECT bucket_id FROM field.exp_bucket WHERE code = 'FOOD.TEA'").fetchone()["bucket_id"]
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute("INSERT INTO field.exp_bucket (code, name_hi, name_en, parent_id) VALUES ('X.Y.Z', 'x', 'x', %s)", (sub,))
