"""Local-disk backend: files under <root>/sha256/<2 chars>/<sha256>. The root lives outside the
package and is git-ignored (MEDIA_DIR, default ./media). Writes go to a temporary file first and
are renamed into place, so a crash never leaves a half-written object under its final name."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pulse.common.storage.base import StoredObject, key_for, sha256_hex


class LocalDiskStorage:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("storage key escapes the media root")
        return path

    def put(self, data: bytes) -> StoredObject:
        digest = sha256_hex(data)
        key = key_for(digest)
        path = self._path(key)
        if path.exists():
            return StoredObject(key=key, sha256=digest, bytes=len(data), created=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return StoredObject(key=key, sha256=digest, bytes=len(data), created=True)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()
