"""Document storage: per-kind backends, and the invariant that keeps electoral
rolls off remote object storage.

The compliance rule being tested is not "rolls default to local". It is "rolls
cannot be anywhere but local". A roll PDF holds every elector's name, EPIC
number, relative's name, house number and age; putting one in a bucket moves
that exposure into a third party's storage, access logs, versioning history and
backups. So `STORAGE_BACKEND=s3` set carelessly must fail loudly rather than
quietly uploading a roll, and that is what most of this file checks.

The S3 backend is exercised against an in-memory stub rather than a live bucket.
The stub implements the four boto3 calls the backend makes with the same
signatures and the same exception behaviour, which covers key construction,
prefixing, sha256 metadata and error wrapping. It does not and cannot prove that
real credentials work - see UAT_READINESS.md for the command the operator runs
for that.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from common import storage
from common.config import get_settings
from common.storage import (
    LOCAL,
    ROLL_KINDS,
    S3,
    LocalStorage,
    RollStorageViolation,
    S3Storage,
    StorageError,
    StoredDoc,
    backend_name_for_kind,
    for_kind,
    get_backend,
    materialise,
    sha256_bytes,
    storage_key,
)

PDF = b"%PDF-1.4\nfake form 20 bytes\n%%EOF"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """`get_settings` is lru_cached, so every test that sets an environment
    variable has to clear it - before, so it does not inherit a neighbour's
    cache, and after, so it does not leak into one."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def local(tmp_path: Path) -> LocalStorage:
    return LocalStorage(root=tmp_path)


# --------------------------------------------------------------------------
# The invariant
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(ROLL_KINDS))
def test_roll_kinds_refuse_a_remote_backend_even_when_configured(kind, monkeypatch):
    """The whole point. STORAGE_BACKEND=s3 must not carry rolls with it."""
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "some-bucket")
    get_settings.cache_clear()

    with pytest.raises(RollStorageViolation) as excinfo:
        backend_name_for_kind(kind)

    message = str(excinfo.value)
    assert kind in message
    # The error has to tell the operator how to fix it, or they will reach for
    # the nearest override and set it wrongly.
    assert "local" in message
    assert "EPIC" in message


@pytest.mark.parametrize("kind", sorted(ROLL_KINDS))
def test_roll_kinds_refuse_an_explicit_per_kind_override(kind, monkeypatch):
    """Not even a deliberate per-kind override gets through."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv(f"STORAGE_BACKEND_{kind.upper()}", "s3")
    monkeypatch.setenv("S3_BUCKET", "some-bucket")
    get_settings.cache_clear()

    with pytest.raises(RollStorageViolation):
        backend_name_for_kind(kind)


@pytest.mark.parametrize("kind", sorted(ROLL_KINDS))
def test_for_kind_never_returns_a_remote_store_for_a_roll(kind, monkeypatch):
    """`for_kind` is what the loaders call, so the invariant must hold there and
    not only in the resolver it delegates to."""
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "some-bucket")
    get_settings.cache_clear()
    with pytest.raises(RollStorageViolation):
        for_kind(kind)


def test_assert_local_only_passes_for_local_and_for_other_kinds():
    storage.assert_local_only("roll_mother", LOCAL)
    storage.assert_local_only("form20", S3)
    storage.assert_local_only("ps_list", S3)


def test_roll_kinds_match_the_schema_constraint():
    """If a new roll-bearing kind is added to source_doc.kind and not to
    ROLL_KINDS, the invariant silently stops covering it."""
    sql = Path("db/migrations/0008_ops.sql").read_text(encoding="utf-8")
    assert "'roll_mother'" in sql and "'roll_supplement'" in sql
    assert ROLL_KINDS == {"roll_mother", "roll_supplement"}


# --------------------------------------------------------------------------
# Per-kind routing
# --------------------------------------------------------------------------


def test_form20_goes_to_s3_when_configured(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_BACKEND_FORM20", "s3")
    get_settings.cache_clear()
    assert backend_name_for_kind("form20") == S3
    assert backend_name_for_kind("ps_list") == LOCAL  # no override, falls to default


def test_the_env_example_configuration_routes_as_the_operator_asked(monkeypatch):
    """The exact combination .env.example ships: rolls local, Form 20 remote."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_BACKEND_FORM20", "s3")
    monkeypatch.setenv("STORAGE_BACKEND_ROLL_MOTHER", "local")
    monkeypatch.setenv("STORAGE_BACKEND_ROLL_SUPPLEMENT", "local")
    get_settings.cache_clear()

    assert backend_name_for_kind("form20") == S3
    assert backend_name_for_kind("roll_mother") == LOCAL
    assert backend_name_for_kind("roll_supplement") == LOCAL


def test_default_is_local_when_nothing_is_set(monkeypatch):
    for key in ("STORAGE_BACKEND", "STORAGE_BACKEND_FORM20"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    assert backend_name_for_kind("form20") == LOCAL


def test_unknown_backend_is_rejected_rather_than_defaulted(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "gcs")
    get_settings.cache_clear()
    with pytest.raises(StorageError, match="unknown storage backend"):
        backend_name_for_kind("form20")


def test_get_backend_by_name():
    assert isinstance(get_backend("local"), LocalStorage)
    assert get_backend("LOCAL").name == LOCAL
    with pytest.raises(StorageError):
        get_backend("azure")


# --------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------


def test_storage_key_shape():
    assert storage_key("form20", "giridih.pdf", ac_number=32, year=2024,
                       election_type="VS") == "form20/2024-VS/32/giridih.pdf"
    assert storage_key("ps_list", "ps.pdf", ac_number=32) == "ps_list/32/ps.pdf"
    assert storage_key("other", "note.pdf") == "other/note.pdf"


def test_storage_key_strips_any_directory_from_the_filename():
    """Filenames come from portal URLs, which we do not control."""
    assert storage_key("form20", "../../secrets/x.pdf") == "form20/x.pdf"
    assert storage_key("form20", "/etc/passwd") == "form20/passwd"


@pytest.mark.parametrize("bad", [
    "../escape.pdf",
    "form20/../../escape.pdf",
    "/absolute/path.pdf",
    "C:/windows/system32/x.pdf",
    "",
    "   ",
    "/",
    "./x.pdf",
])
def test_bad_keys_are_refused(bad, local):
    with pytest.raises(StorageError):
        local.put(bad, PDF)


def test_local_put_cannot_escape_the_root(local, tmp_path):
    outside = tmp_path.parent / "escaped.pdf"
    with pytest.raises(StorageError):
        local.put("../escaped.pdf", PDF)
    assert not outside.exists()


# --------------------------------------------------------------------------
# LocalStorage
# --------------------------------------------------------------------------


def test_local_round_trip(local):
    stored = local.put("form20/2024-VS/32/a.pdf", PDF)
    assert stored.backend == LOCAL
    assert stored.key == "form20/2024-VS/32/a.pdf"
    assert stored.sha256 == sha256_bytes(PDF)
    assert stored.bytes == len(PDF)
    assert stored.uri == "local://form20/2024-VS/32/a.pdf"

    assert local.exists("form20/2024-VS/32/a.pdf")
    assert local.get("form20/2024-VS/32/a.pdf") == PDF
    assert local.local_path("form20/2024-VS/32/a.pdf").read_bytes() == PDF


def test_local_get_missing_raises_rather_than_returning_empty(local):
    with pytest.raises(StorageError, match="not in local storage"):
        local.get("form20/nope.pdf")
    assert local.exists("form20/nope.pdf") is False
    assert local.local_path("form20/nope.pdf") is None


def test_local_put_is_idempotent_and_overwrites_cleanly(local):
    local.put("form20/a.pdf", PDF)
    second = local.put("form20/a.pdf", PDF + b"more")
    assert local.get("form20/a.pdf") == PDF + b"more"
    assert second.bytes == len(PDF) + len(b"more")


def test_local_put_leaves_no_partial_file_behind(local, tmp_path):
    local.put("form20/a.pdf", PDF)
    assert list(tmp_path.rglob("*.partial")) == []


def test_local_list_is_relative_and_sorted(local):
    local.put("form20/b.pdf", PDF)
    local.put("form20/a.pdf", PDF)
    local.put("ps_list/c.pdf", PDF)
    assert local.list() == ["form20/a.pdf", "form20/b.pdf", "ps_list/c.pdf"]
    assert local.list("form20") == ["form20/a.pdf", "form20/b.pdf"]
    assert local.list("nothing-here") == []


def test_local_list_ignores_partial_writes(local, tmp_path):
    local.put("form20/a.pdf", PDF)
    (tmp_path / "form20" / "b.pdf.partial").write_bytes(b"half")
    assert local.list() == ["form20/a.pdf"]


def test_local_uses_raw_dir_when_no_root_is_given(monkeypatch, tmp_path):
    monkeypatch.setenv("RAW_DIR", str(tmp_path / "somewhere"))
    get_settings.cache_clear()
    store = LocalStorage()
    store.put("form20/a.pdf", PDF)
    assert (tmp_path / "somewhere" / "form20" / "a.pdf").exists()


# --------------------------------------------------------------------------
# S3Storage, against a stub
# --------------------------------------------------------------------------


class StubS3:
    """The four boto3 calls S3Storage makes. Raises on a missing key the way
    botocore does, so the backend's error handling is exercised."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict] = {}
        self.calls: list[tuple] = []

    def put_object(self, Bucket, Key, Body, Metadata=None):  # noqa: N803
        self.calls.append(("put", Bucket, Key))
        self.objects[Key] = Body
        self.metadata[Key] = Metadata or {}
        return {}

    def get_object(self, Bucket, Key):  # noqa: N803
        self.calls.append(("get", Bucket, Key))
        if Key not in self.objects:
            raise KeyError(f"NoSuchKey: {Key}")

        class _Body:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

        return {"Body": _Body(self.objects[Key])}

    def head_object(self, Bucket, Key):  # noqa: N803
        self.calls.append(("head", Bucket, Key))
        if Key not in self.objects:
            raise KeyError(f"404: {Key}")
        return {"ContentLength": len(self.objects[Key])}

    def get_paginator(self, name):
        stub = self

        class _Paginator:
            def paginate(self, Bucket, Prefix=""):  # noqa: N803
                contents = [{"Key": k} for k in sorted(stub.objects) if k.startswith(Prefix)]
                yield {"Contents": contents}

        return _Paginator()


@pytest.fixture
def s3() -> tuple[S3Storage, StubS3]:
    stub = StubS3()
    return S3Storage(bucket="test-bucket", prefix="raw", client=stub), stub


def test_s3_round_trip_and_prefixing(s3):
    store, stub = s3
    stored = store.put("form20/2024-VS/32/a.pdf", PDF)

    assert stored.backend == S3
    assert stored.key == "form20/2024-VS/32/a.pdf"
    assert stored.sha256 == sha256_bytes(PDF)
    # The prefix is applied to the object key but not to the recorded key, so
    # changing S3_PREFIX does not orphan rows already in source_doc.
    assert "raw/form20/2024-VS/32/a.pdf" in stub.objects
    assert store.get("form20/2024-VS/32/a.pdf") == PDF
    assert store.exists("form20/2024-VS/32/a.pdf") is True


def test_s3_records_the_digest_as_object_metadata(s3):
    store, stub = s3
    store.put("form20/a.pdf", PDF)
    assert stub.metadata["raw/form20/a.pdf"]["sha256"] == sha256_bytes(PDF)


def test_s3_works_without_a_prefix():
    stub = StubS3()
    store = S3Storage(bucket="b", prefix="", client=stub)
    store.put("form20/a.pdf", PDF)
    assert "form20/a.pdf" in stub.objects


def test_s3_missing_key_raises_storage_error_not_a_boto_error(s3):
    store, _ = s3
    with pytest.raises(StorageError, match="could not get"):
        store.get("form20/absent.pdf")
    assert store.exists("form20/absent.pdf") is False


def test_s3_list_strips_the_prefix(s3):
    store, _ = s3
    store.put("form20/b.pdf", PDF)
    store.put("form20/a.pdf", PDF)
    assert store.list() == ["form20/a.pdf", "form20/b.pdf"]
    assert store.list("form20") == ["form20/a.pdf", "form20/b.pdf"]


def test_s3_has_no_local_path(s3):
    store, _ = s3
    store.put("form20/a.pdf", PDF)
    assert store.local_path("form20/a.pdf") is None


def test_s3_without_a_bucket_fails_with_an_actionable_message(monkeypatch):
    monkeypatch.delenv("S3_BUCKET", raising=False)
    get_settings.cache_clear()
    with pytest.raises(StorageError) as excinfo:
        S3Storage(client=StubS3())
    assert "S3_BUCKET" in str(excinfo.value)
    assert "STORAGE_BACKEND=local" in str(excinfo.value)


def test_s3_key_validation_applies_before_any_call(s3):
    store, stub = s3
    with pytest.raises(StorageError):
        store.put("../escape.pdf", PDF)
    assert stub.calls == []


# --------------------------------------------------------------------------
# materialise
# --------------------------------------------------------------------------


def test_materialise_yields_the_local_file_in_place(monkeypatch, tmp_path):
    """A roll PDF must exist exactly once on the host - materialise must not
    copy it to a temp directory and thereby double the exposure."""
    monkeypatch.setenv("RAW_DIR", str(tmp_path))
    get_settings.cache_clear()
    LocalStorage().put("roll_mother/r.pdf", PDF)
    original = tmp_path / "roll_mother" / "r.pdf"

    with materialise(LOCAL, "roll_mother/r.pdf") as path:
        assert path == original
        assert path.read_bytes() == PDF
    assert original.exists()


def test_materialise_downloads_and_then_deletes_a_remote_document(monkeypatch):
    stub = StubS3()
    stub.objects["raw/form20/a.pdf"] = PDF
    monkeypatch.setenv("S3_BUCKET", "b")
    monkeypatch.setenv("S3_PREFIX", "raw")
    get_settings.cache_clear()
    monkeypatch.setattr(storage, "S3Storage",
                        lambda *a, **k: S3Storage(bucket="b", prefix="raw", client=stub))

    with materialise(S3, "form20/a.pdf") as path:
        assert path.read_bytes() == PDF
        assert path.name == "a.pdf"
        tmp_dir = path.parent
        assert tmp_dir.exists()

    assert not tmp_dir.exists(), "temporary download was not cleaned up"


def test_materialise_cleans_up_even_when_the_caller_raises(monkeypatch):
    stub = StubS3()
    stub.objects["form20/a.pdf"] = PDF
    monkeypatch.setenv("S3_BUCKET", "b")
    get_settings.cache_clear()
    monkeypatch.setattr(storage, "S3Storage",
                        lambda *a, **k: S3Storage(bucket="b", prefix="", client=stub))

    seen: list[Path] = []
    with pytest.raises(ValueError):
        with materialise(S3, "form20/a.pdf") as path:
            seen.append(path.parent)
            raise ValueError("parser blew up")
    assert not seen[0].exists()


def test_materialise_on_a_missing_local_document_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("RAW_DIR", str(tmp_path))
    get_settings.cache_clear()
    with pytest.raises(StorageError):
        with materialise(LOCAL, "form20/absent.pdf"):
            pass


# --------------------------------------------------------------------------
# Digests
# --------------------------------------------------------------------------


def test_sha256_helpers_agree(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(PDF)
    assert storage.sha256_path(path) == sha256_bytes(PDF)


def test_stored_doc_is_immutable():
    """Frozen so a caller cannot rewrite the key after the bytes are written and
    leave source_doc pointing at an object that is not there."""
    doc = StoredDoc(LOCAL, "form20/a.pdf", "abc", 3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        doc.key = "other"  # type: ignore[misc]
