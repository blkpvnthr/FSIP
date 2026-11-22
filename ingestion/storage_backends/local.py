"""Local filesystem storage backend (the default).

Maps logical keys to files under a root directory. Conditional writes are
emulated: ``"absent"`` uses an atomic ``O_EXCL`` create; an etag precondition
compares the current content hash (the etag is the content's SHA-256). Writes are
atomic (temp file + ``os.replace``), so a crashed write never leaves a partial
object visible.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from ..exceptions import ObjectNotFoundError, PreconditionFailedError
from ..logging_config import get_logger
from .base import ABSENT, ObjectMetadata, StorageBackend

log = get_logger(__name__)
_CHUNK = 1024 * 1024


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class LocalStorageBackend(StorageBackend):
    """Filesystem-backed storage rooted at a directory."""

    name = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        key = key.lstrip("/")
        target = (self.root / key).resolve()
        root = self.root.resolve()
        if root != target and root not in target.parents:
            raise ValueError(f"Key escapes storage root: {key!r}")
        return target

    def uri(self, key: str) -> str:
        return str(self._path(key))

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def stat(self, key: str) -> ObjectMetadata:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFoundError(self.uri(key))
        st = path.stat()
        return ObjectMetadata(
            key=key,
            size=st.st_size,
            etag=_sha256_file(path),
            last_modified=str(int(st.st_mtime)),
        )

    def _check_precondition(self, key: str, precondition: str | None) -> None:
        if precondition is None:
            return
        exists = self.exists(key)
        if precondition == ABSENT:
            if exists:
                raise PreconditionFailedError(f"Object already exists: {self.uri(key)}")
        else:  # etag compare-and-swap
            if not exists or self.stat(key).etag != precondition:
                raise PreconditionFailedError(f"etag precondition failed for {self.uri(key)}")

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        precondition: str | None = None,
    ) -> ObjectMetadata:
        self._check_precondition(key, precondition)
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if precondition == ABSENT:
            # Atomic create-only: O_EXCL guarantees we lose a concurrent race.
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError as exc:
                raise PreconditionFailedError(f"Object already exists: {self.uri(key)}") from exc
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
        else:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        return self.stat(key)

    def get_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFoundError(self.uri(key))
        return path.read_bytes()

    def open_read(self, key: str) -> BinaryIO:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFoundError(self.uri(key))
        return open(path, "rb")

    def upload_file(
        self, local_path: Path, key: str, *, precondition: str | None = None
    ) -> ObjectMetadata:
        self._check_precondition(key, precondition)
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".uploading")
        shutil.copyfile(local_path, tmp)
        if precondition == ABSENT and dest.exists():
            tmp.unlink(missing_ok=True)
            raise PreconditionFailedError(f"Object already exists: {self.uri(key)}")
        tmp.replace(dest)
        return self.stat(key)

    def download_file(self, key: str, local_path: Path) -> None:
        src = self._path(key)
        if not src.is_file():
            raise ObjectNotFoundError(self.uri(key))
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, local_path)

    def list(self, prefix: str) -> Iterator[str]:
        base = self._path(prefix) if prefix else self.root
        root = self.root.resolve()
        if base.is_file():
            yield base.resolve().relative_to(root).as_posix()
            return
        if not base.exists():
            return
        for path in sorted(base.rglob("*")):
            if path.is_file() and not path.name.endswith((".tmp", ".uploading")):
                yield path.resolve().relative_to(root).as_posix()

    def copy(
        self, src_key: str, dst_key: str, *, precondition: str | None = None
    ) -> ObjectMetadata:
        src = self._path(src_key)
        if not src.is_file():
            raise ObjectNotFoundError(self.uri(src_key))
        self._check_precondition(dst_key, precondition)
        dst = self._path(dst_key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(dst.suffix + ".copying")
        shutil.copyfile(src, tmp)
        if precondition == ABSENT and dst.exists():
            tmp.unlink(missing_ok=True)
            raise PreconditionFailedError(f"Object already exists: {self.uri(dst_key)}")
        tmp.replace(dst)
        return self.stat(dst_key)

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()
