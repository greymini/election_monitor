"""Private object storage for media. Content-addressed: the key is derived from the sha256 of the
bytes, so the same file is stored once however many times it is sent. There are no public URLs;
signed, short-lived links come with the inbox in a later slice (LLD §11)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    bytes: int
    created: bool            # False when identical bytes were already stored


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def key_for(sha256: str) -> str:
    return f"sha256/{sha256[:2]}/{sha256}"


@runtime_checkable
class Storage(Protocol):
    def put(self, data: bytes) -> StoredObject: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...
