"""Document storage, one backend per document kind (spec §5.3).

Source PDFs are the audit trail: `RUN.md` §8 says they are kept forever, and
every loaded row carries the document and page it came from. Where those bytes
live differs by kind, and the difference is a compliance boundary rather than a
deployment preference:

  * **Electoral rolls stay on the local host.** A roll PDF contains every
    elector's name, EPIC number, relative's name, house number and age. Putting
    one in third-party object storage would move the entire personal-data
    exposure off a box we control and into a bucket, its access logs, its
    versioning history and its backups. `assert_local_only()` refuses it
    unconditionally - not as a default, as an invariant. A mistyped
    STORAGE_BACKEND cannot ship an electoral roll to S3.

  * **Everything else may live in object storage.** Form 20, PS lists, SEC
    results and press notes are published documents. They are also the bulky,
    re-parsed, multi-operator ones, so a shared bucket is genuinely useful: an
    analyst on a laptop can re-parse a Form 20 against a remote database without
    first downloading 300 MB of PDFs by hand.

Configuration (see `.env.example`):

    STORAGE_BACKEND=local          default backend for kinds with no override
    STORAGE_BACKEND_FORM20=s3      per-kind override, one per source_doc.kind
    S3_ENDPOINT=https://s3.eu-central-003.backblazeb2.com
    S3_BUCKET=giridih-monitor
    S3_ACCESS_KEY=...
    S3_SECRET_KEY=...
    S3_PREFIX=raw/

`S3_ENDPOINT` is configurable so this works against AWS S3, Backblaze B2, MinIO
or Wasabi; leave it empty for AWS and boto3 resolves the endpoint itself.

The parsers need a real local path - `pdfplumber.open()` and the sha256 digest
both read a file - so `materialise()` is the seam: it returns a path that is
either the original local file or a temporary download, and a context manager
cleans the temporary one up. Nothing in the parsers needs to know which.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from common.config import get_settings
from common.logging_setup import get_logger

log = get_logger(__name__)

# source_doc.kind values that hold individual voter data. These may never be
# written to a remote backend. Keep in step with the CHECK constraint on
# source_doc.kind (db/migrations/0008_ops.sql) and with parse_roll.
ROLL_KINDS = frozenset({"roll_mother", "roll_supplement"})

LOCAL = "local"
S3 = "s3"
BACKENDS = (LOCAL, S3)


class StorageError(RuntimeError):
    """Storage could not satisfy the request. Never swallowed: a document we
    cannot store or retrieve must stop the load, not degrade it."""


class RollStorageViolation(StorageError):
    """A roll document was routed at a remote backend. Refused."""


@dataclass(frozen=True)
class StoredDoc:
    """Where a document actually ended up. Persisted on `source_doc` as
    (storage_backend, storage_key) so a later parse can find the bytes without
    guessing at a directory layout."""

    backend: str
    key: str
    sha256: str
    bytes: int

    @property
    def uri(self) -> str:
        return f"{self.backend}://{self.key}"


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Storage(Protocol):
    """One document store. Keys are POSIX-style relative paths
    ('form20/2024-VS/32/giridih-form20.pdf'), never absolute, never with '..'."""

    name: str

    def put(self, key: str, payload: bytes) -> StoredDoc: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def local_path(self, key: str) -> Path | None:
        """A real path if the backend has one, else None (caller must
        materialise)."""
        ...

    def list(self, prefix: str = "") -> list[str]: ...


def _validate_key(key: str) -> str:
    """Storage keys come from portal filenames, which we do not control. A
    filename of '../../etc/passwd' or an absolute path must not escape the
    storage root on the local backend or the prefix on S3."""
    if not key or key.strip() != key:
        raise StorageError(f"empty or padded storage key: {key!r}")

    slashed = key.replace("\\", "/")

    # Test for absoluteness before stripping separators, not after: stripping
    # first turns '/etc/passwd' into a perfectly valid relative key and the
    # check becomes dead code. A caller passing an absolute path has a bug, and
    # silently reinterpreting it relative to the storage root would write the
    # bytes somewhere nobody intended. Refuse instead.
    if slashed.startswith("/") or (len(slashed) > 1 and slashed[1] == ":"):
        raise StorageError(f"storage key must be relative, not absolute: {key!r}")

    parts = [p for p in slashed.split("/") if p]
    if not parts:
        raise StorageError(f"storage key has no path segments: {key!r}")
    for part in parts:
        if part in {".", ".."}:
            raise StorageError(f"storage key may not traverse directories: {key!r}")
    return "/".join(parts)


class LocalStorage:
    """Files under a root directory, one subdirectory per key segment.

    The root is `RAW_DIR`. Directories are created 0700 and files 0600 so a roll
    PDF is readable only by the account that wrote it, which is what LLD §12
    describes ("stored in raw/ with filesystem permissions to worker only") and
    what the audit found was not actually done. On Windows the chmod is a no-op;
    that is acceptable because the compliance target is the Linux host.
    """

    name = LOCAL

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root if root is not None else get_settings().raw_dir)

    def _resolve(self, key: str) -> Path:
        safe = _validate_key(key)
        path = (self.root / safe).resolve()
        root = self.root.resolve()
        # Belt and braces: even with _validate_key, confirm containment, since a
        # symlink inside the root could still point outside it.
        if root != path and root not in path.parents:
            raise StorageError(f"storage key escapes the root: {key!r}")
        return path

    def put(self, key: str, payload: bytes) -> StoredDoc:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        _restrict(path.parent, directory=True)
        # Write to a sibling temp file and replace, so an interrupted write can
        # never leave a truncated PDF that looks complete.
        tmp = path.with_name(path.name + ".partial")
        try:
            tmp.write_bytes(payload)
            _restrict(tmp, directory=False)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return StoredDoc(self.name, _validate_key(key), sha256_bytes(payload), len(payload))

    def get(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.exists():
            raise StorageError(f"not in local storage: {key}")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._resolve(key).exists()

    def local_path(self, key: str) -> Path | None:
        path = self._resolve(key)
        return path if path.exists() else None

    def list(self, prefix: str = "") -> list[str]:
        base = self.root / prefix.replace("\\", "/").strip("/") if prefix else self.root
        if not base.exists():
            return []
        root = self.root.resolve()
        return sorted(
            p.resolve().relative_to(root).as_posix()
            for p in base.rglob("*")
            if p.is_file() and not p.name.endswith(".partial")
        )


def _restrict(path: Path, directory: bool) -> None:
    """0700 on directories, 0600 on files. Best effort: os.chmod on Windows
    only toggles the read-only bit, and a permission error on a mounted volume
    should not fail an otherwise good write."""
    try:
        os.chmod(path, 0o700 if directory else 0o600)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover
        log.debug("could not restrict permissions on %s: %s", path, exc)


class S3Storage:
    """Any S3-compatible object store, addressed through boto3.

    `endpoint_url` is passed through so the same class serves AWS S3, Backblaze
    B2, MinIO and Wasabi. boto3 is imported lazily and is not in
    requirements-api.txt: the API never touches document storage, and the audit
    was right that the API image should not grow dependencies it cannot use.
    """

    name = S3

    def __init__(
        self,
        bucket: str | None = None,
        prefix: str | None = None,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str | None = None,
        client=None,
    ) -> None:
        s = get_settings()
        self.bucket = bucket if bucket is not None else s.s3_bucket
        self.prefix = (prefix if prefix is not None else s.s3_prefix).strip("/")
        self._endpoint = endpoint_url if endpoint_url is not None else s.s3_endpoint
        self._access_key = access_key if access_key is not None else s.s3_access_key
        self._secret_key = secret_key if secret_key is not None else s.s3_secret_key
        self._region = region if region is not None else s.s3_region
        self._client = client
        if not self.bucket:
            raise StorageError(
                "S3_BUCKET is not set. Either set it, or set STORAGE_BACKEND=local "
                "to keep every document on this host."
            )

    @property
    def client(self):
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - depends on the image
                raise StorageError(
                    "boto3 is not installed, so the s3 storage backend is unavailable. "
                    "It ships in requirements-worker.txt; install it, or run with "
                    "STORAGE_BACKEND=local."
                ) from exc
            kwargs: dict = {}
            if self._endpoint:
                kwargs["endpoint_url"] = self._endpoint
            if self._region:
                kwargs["region_name"] = self._region
            if self._access_key and self._secret_key:
                kwargs["aws_access_key_id"] = self._access_key
                kwargs["aws_secret_access_key"] = self._secret_key
            # With no explicit keys, boto3 falls back to its own credential
            # chain (profile, instance role), which is what an EC2/ECS
            # deployment wants.
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def _object_key(self, key: str) -> str:
        safe = _validate_key(key)
        return f"{self.prefix}/{safe}" if self.prefix else safe

    def put(self, key: str, payload: bytes) -> StoredDoc:
        digest = sha256_bytes(payload)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self._object_key(key),
                Body=payload,
                # Lets the store verify the bytes it received, and lets a later
                # HEAD confirm integrity without downloading the object.
                Metadata={"sha256": digest},
            )
        except Exception as exc:  # boto3 raises ClientError subclasses
            raise StorageError(f"could not put {key} in s3://{self.bucket}: {exc}") from exc
        return StoredDoc(self.name, _validate_key(key), digest, len(payload))

    def get(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._object_key(key))
            return response["Body"].read()
        except Exception as exc:
            raise StorageError(f"could not get {key} from s3://{self.bucket}: {exc}") from exc

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._object_key(key))
            return True
        except Exception:
            return False

    def local_path(self, key: str) -> Path | None:
        return None

    def list(self, prefix: str = "") -> list[str]:
        full = self._object_key(prefix) if prefix else (self.prefix or "")
        keys: list[str] = []
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=full):
                for item in page.get("Contents", []):
                    key = item["Key"]
                    if self.prefix and key.startswith(self.prefix + "/"):
                        key = key[len(self.prefix) + 1 :]
                    keys.append(key)
        except Exception as exc:
            raise StorageError(f"could not list s3://{self.bucket}/{full}: {exc}") from exc
        return sorted(keys)


def assert_local_only(kind: str, backend: str) -> None:
    """The invariant. Roll documents may not leave the host, whatever the
    configuration says."""
    if kind in ROLL_KINDS and backend != LOCAL:
        raise RollStorageViolation(
            f"refusing to store a {kind} document on the {backend!r} backend. "
            "Electoral rolls carry names, EPIC numbers, relatives' names and "
            "addresses, and must stay on a host we control (LLD 12). Set "
            f"STORAGE_BACKEND_{kind.upper()}=local, or leave it unset."
        )


def backend_name_for_kind(kind: str) -> str:
    """Resolve the configured backend for a document kind, then apply the
    invariant. Per-kind override first, then STORAGE_BACKEND."""
    s = get_settings()
    chosen = s.storage_backend_by_kind.get(kind) or s.storage_backend
    chosen = (chosen or LOCAL).strip().lower()
    if chosen not in BACKENDS:
        raise StorageError(
            f"unknown storage backend {chosen!r} for kind {kind!r}; expected one of {BACKENDS}"
        )
    if kind in ROLL_KINDS and chosen != LOCAL:
        # Refuse rather than silently downgrade: a misconfiguration here means
        # somebody believes rolls are going somewhere they are not, and that
        # belief needs correcting loudly.
        assert_local_only(kind, chosen)
    return chosen


def for_kind(kind: str) -> Storage:
    """The storage backend a document of this kind belongs in."""
    name = backend_name_for_kind(kind)
    return LocalStorage() if name == LOCAL else S3Storage()


def get_backend(name: str) -> Storage:
    """A backend by name, for reading a document back from whatever
    `source_doc.storage_backend` recorded."""
    normalised = (name or LOCAL).strip().lower()
    if normalised == LOCAL:
        return LocalStorage()
    if normalised == S3:
        return S3Storage()
    raise StorageError(f"unknown storage backend {name!r}; expected one of {BACKENDS}")


def storage_key(kind: str, filename: str, ac_number: int | None = None,
                year: int | None = None, election_type: str | None = None) -> str:
    """A stable, legible key: kind/[year-type]/[ac]/filename.

    Spec §5.3 asks for `raw/{state}/{ac}/{doc_type}/{year}/`. The state is
    implicit while everything is Jharkhand; `ac_number` is optional because a
    PC-level Form 20 covers several ACs and a press note may cover none.
    """
    segments = [kind]
    if year and election_type:
        segments.append(f"{year}-{election_type}")
    elif year:
        segments.append(str(year))
    if ac_number is not None:
        segments.append(str(ac_number))
    segments.append(Path(filename).name)
    return _validate_key("/".join(segments))


@contextmanager
def materialise(backend: str, key: str) -> Iterator[Path]:
    """Yield a local path for a stored document.

    Local documents are yielded in place - no copy, so nothing is duplicated on
    the host and a roll PDF exists exactly once. Remote documents are downloaded
    to a temporary file and deleted on exit, so a Form 20 pulled from S3 leaves
    nothing behind.
    """
    store = get_backend(backend)
    existing = store.local_path(key)
    if existing is not None:
        yield existing
        return

    payload = store.get(key)
    tmp_dir = Path(tempfile.mkdtemp(prefix="giridih-doc-"))
    _restrict(tmp_dir, directory=True)
    tmp_path = tmp_dir / Path(key).name
    try:
        tmp_path.write_bytes(payload)
        _restrict(tmp_path, directory=False)
        yield tmp_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
