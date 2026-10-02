"""`extract_pdf --register` writes a source_doc row the schema accepts.

0013 made source_doc.storage_key NOT NULL, and register_source_doc never
supplied it, so every `--register` failed with a NOT NULL violation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ingest import extract_pdf


@pytest.fixture
def captured(monkeypatch):
    seen: dict = {}

    def fake_query_one(sql, params=()):
        seen["sql"], seen["params"] = sql, params
        return {"doc_id": 7}

    import common.db
    monkeypatch.setattr(common.db, "query_one", fake_query_one)
    return seen


def _pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4 test")
    return path


def test_register_supplies_storage_backend_and_key(tmp_path, monkeypatch, captured):
    monkeypatch.setenv("RAW_DIR", str(tmp_path / "raw"))
    from common.config import get_settings
    get_settings.cache_clear()
    pdf = _pdf(tmp_path / "raw" / "form20" / "2024-VS" / "32" / "giridih.pdf")

    assert extract_pdf.register_source_doc(pdf, "form20", []) == 7

    assert "storage_key" in captured["sql"] and "storage_backend" in captured["sql"]
    assert "local" in captured["params"]
    assert "form20/2024-VS/32/giridih.pdf" in captured["params"]
    get_settings.cache_clear()


def test_a_file_outside_raw_dir_gets_the_kind_and_filename_key(tmp_path, monkeypatch, captured):
    monkeypatch.setenv("RAW_DIR", str(tmp_path / "raw"))
    from common.config import get_settings
    get_settings.cache_clear()
    pdf = _pdf(tmp_path / "elsewhere" / "list.pdf")

    extract_pdf.register_source_doc(pdf, "ps_list", [])

    assert "ps_list/list.pdf" in captured["params"]
    get_settings.cache_clear()
