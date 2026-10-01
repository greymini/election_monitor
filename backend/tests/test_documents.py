"""Resolving a document by path, by registered digest, or by storage key.

This is what makes ingestion runnable from a laptop against a remote
DATABASE_URL: `--doc <sha256>` asks the database where the bytes are rather than
assuming a local `raw/` tree. The digest check on that path is the important
one - see test_a_substituted_object_is_refused.

The database is faked. `lookup_by_digest` and `advance_status` import
`common.db` lazily inside the function, which makes them substitutable without a
Postgres; every other behaviour here is filesystem or storage only.
"""

from __future__ import annotations

import pytest

from common.config import get_settings
from common.storage import LocalStorage, sha256_bytes
from ingest import documents
from ingest.documents import DocumentNotFound, open_document
from tests.paths import MIGRATIONS

PDF = b"%PDF-1.4\nform 20, giridih, 2024\n%%EOF"
OTHER = b"%PDF-1.4\na different document entirely\n%%EOF"


@pytest.fixture(autouse=True)
def _local_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _fake_rows(rows):
    """Replace the lazy `common.db.query` that lookup_by_digest imports."""
    import types

    module = types.ModuleType("common.db")
    module.query = lambda sql, params=None: [
        r for r in rows if r["sha256"].startswith((params or ("",))[0].rstrip("%"))
    ]
    module.execute = lambda sql, params=None: None
    return module


# --------------------------------------------------------------------------
# Naming a document
# --------------------------------------------------------------------------


def test_a_local_path_still_works(tmp_path):
    """Every command in RUN.md passes a positional path; none of them may break."""
    path = tmp_path / "form20.pdf"
    path.write_bytes(PDF)
    with open_document(pdf=str(path)) as (resolved, provenance):
        assert resolved == path
        assert provenance["sha256"] == sha256_bytes(PDF)
        assert provenance["storage_backend"] == "local"


def test_a_missing_local_path_is_refused(tmp_path):
    with pytest.raises(DocumentNotFound, match="no such file"):
        with open_document(pdf=str(tmp_path / "absent.pdf")):
            pass


def test_naming_nothing_is_refused():
    with pytest.raises(DocumentNotFound, match="exactly one"):
        with open_document():
            pass


def test_naming_two_ways_at_once_is_refused(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(PDF)
    with pytest.raises(DocumentNotFound, match="exactly one"):
        with open_document(pdf=str(path), key="form20/a.pdf"):
            pass


def test_a_storage_key_resolves_without_a_database():
    LocalStorage().put("form20/2024-VS/32/a.pdf", PDF)
    with open_document(key="form20/2024-VS/32/a.pdf", kind="form20") as (path, provenance):
        assert path.read_bytes() == PDF
        assert provenance["storage_key"] == "form20/2024-VS/32/a.pdf"
        assert provenance["doc_id"] is None


def test_a_missing_storage_key_is_refused():
    from common.storage import StorageError

    with pytest.raises(StorageError):
        with open_document(key="form20/absent.pdf", kind="form20"):
            pass


# --------------------------------------------------------------------------
# --doc: the laptop path
# --------------------------------------------------------------------------


def test_a_registered_digest_resolves_through_the_backend_the_row_records(monkeypatch):
    LocalStorage().put("form20/2024-VS/32/a.pdf", PDF)
    row = {
        "doc_id": 7, "kind": "form20", "filename": "a.pdf",
        "sha256": sha256_bytes(PDF),
        "storage_backend": "local", "storage_key": "form20/2024-VS/32/a.pdf",
        "parse_status": "extracted",
    }
    monkeypatch.setitem(__import__("sys").modules, "common.db", _fake_rows([row]))

    with open_document(doc=row["sha256"][:12]) as (path, provenance):
        assert path.read_bytes() == PDF
        assert provenance["doc_id"] == 7
        assert provenance["kind"] == "form20"


def test_an_unregistered_digest_says_how_to_register_it(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "common.db", _fake_rows([]))
    with pytest.raises(DocumentNotFound) as excinfo:
        with open_document(doc="deadbeef"):
            pass
    message = str(excinfo.value)
    assert "fetch_ceo" in message and "--register" in message


def test_an_ambiguous_digest_prefix_is_refused(monkeypatch):
    rows = [
        {"doc_id": 1, "kind": "form20", "filename": "a.pdf", "sha256": "abc111",
         "storage_backend": "local", "storage_key": "form20/a.pdf", "parse_status": "new"},
        {"doc_id": 2, "kind": "form20", "filename": "b.pdf", "sha256": "abc222",
         "storage_backend": "local", "storage_key": "form20/b.pdf", "parse_status": "new"},
    ]
    monkeypatch.setitem(__import__("sys").modules, "common.db", _fake_rows(rows))
    with pytest.raises(DocumentNotFound, match="matches 2 documents"):
        with open_document(doc="abc"):
            pass


def test_a_substituted_object_is_refused(monkeypatch):
    """The reason the digest is verified rather than trusted.

    If the stored object is not the document that was registered - overwritten
    key, eventual consistency, someone re-uploading under the same name - then
    loading it would put numbers into the serving tables that were never
    reconciled, under a source_doc reference that looks audited. Refuse.
    """
    LocalStorage().put("form20/a.pdf", OTHER)          # storage holds one thing
    row = {
        "doc_id": 7, "kind": "form20", "filename": "a.pdf",
        "sha256": sha256_bytes(PDF),                   # the row records another
        "storage_backend": "local", "storage_key": "form20/a.pdf",
        "parse_status": "extracted",
    }
    monkeypatch.setitem(__import__("sys").modules, "common.db", _fake_rows([row]))

    with pytest.raises(DocumentNotFound) as excinfo:
        with open_document(doc=row["sha256"]):
            pass
    message = str(excinfo.value)
    assert "do not load it" in message
    assert sha256_bytes(OTHER)[:12] in message
    assert sha256_bytes(PDF)[:12] in message


def test_verification_can_be_waived_explicitly(monkeypatch):
    """There is a legitimate case - inspecting a document precisely because it
    does not match - so the check is skippable, but only on purpose."""
    LocalStorage().put("form20/a.pdf", OTHER)
    row = {
        "doc_id": 7, "kind": "form20", "filename": "a.pdf", "sha256": sha256_bytes(PDF),
        "storage_backend": "local", "storage_key": "form20/a.pdf", "parse_status": "new",
    }
    monkeypatch.setitem(__import__("sys").modules, "common.db", _fake_rows([row]))
    with open_document(doc=row["sha256"], verify=False) as (path, _):
        assert path.read_bytes() == OTHER


# --------------------------------------------------------------------------
# CLI wiring
# --------------------------------------------------------------------------


def test_add_document_arguments_keeps_the_positional_path_optional():
    import argparse

    ap = argparse.ArgumentParser()
    documents.add_document_arguments(ap)

    assert ap.parse_args([]).pdf is None
    assert ap.parse_args(["some.pdf"]).pdf == "some.pdf"
    assert ap.parse_args(["--doc", "abc123"]).doc == "abc123"
    assert ap.parse_args(["--key", "form20/a.pdf"]).key == "form20/a.pdf"
    assert ap.parse_args(["--key", "form20/a.pdf", "--backend", "s3"]).backend == "s3"


def test_advance_status_rejects_an_unknown_status(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "common.db", _fake_rows([]))
    with pytest.raises(ValueError, match="unknown parse_status"):
        documents.advance_status("abc", "somewhere_else", "test")


@pytest.mark.parametrize("status", ["new", "extracted", "parsed", "validated", "loaded",
                                    "failed", "drifted"])
def test_advance_status_accepts_every_lifecycle_state(status, monkeypatch):
    """The states must match the CHECK constraint 0013 installs, or the UPDATE
    fails at runtime rather than here."""
    monkeypatch.setitem(__import__("sys").modules, "common.db", _fake_rows([]))
    documents.advance_status("abc", status, "test")  # must not raise

    sql = (MIGRATIONS / "0013_source_doc_storage.sql").read_text(encoding="utf-8")
    assert f"'{status}'" in sql
