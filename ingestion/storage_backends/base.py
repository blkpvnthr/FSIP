"""Storage-backend interface shared by the local filesystem and AWS S3.

The ingestion and publication layers depend only on this abstract interface, so
the same code path writes to a local data lake (the default) or an S3 bucket
without change. Keys are always ``/``-separated logical paths; each backend maps
a key to a concrete location (a file under a root dir, or ``s3://bucket/prefix/key``).

Design rules
------------
* **Streaming, bounded memory.** ``upload_file``/``download_file``/``open_read``
  never load a whole multi-gigabyte object into memory; large uploads use
  multipart transfers.
* **Conditional writes.** ``put_bytes``/``upload_file`` accept a ``precondition``:
  ``"absent"`` (create-only — used for immutable raw and curated snapshot
  objects) or an etag string (compare-and-swap — used for the catalog pointer).
  A violated precondition raises :class:`PreconditionFailedError`.
* **Checksums.** ``sha256`` streams an object and returns its hex digest, so the
  same integrity check works locally and in the cloud.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

_CHUNK = 1024 * 1024  # 1 MiB streaming chunks

#: Sentinel for a create-only conditional write.
ABSENT = "absent"


@dataclass(frozen=True)
class ObjectMetadata:
    """Metadata about a stored object."""

    key: str
    size: int
    etag: str  # opaque version token (S3 ETag, or local content hash)
    last_modified: str | None = None
    content_type: str | None = None


class StorageBackend(ABC):
    """Abstract storage backend. Subclasses: local filesystem and AWS S3."""

    #: Short name ("local" | "s3") for logging / reporting.
    name: str = "base"

    @abstractmethod
    def uri(self, key: str) -> str:
        """Return a human-readable location for ``key`` (path or s3:// URI)."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return True iff an object exists at ``key``."""

    @abstractmethod
    def stat(self, key: str) -> ObjectMetadata:
        """Return metadata for ``key``. Raises :class:`ObjectNotFoundError`."""

    @abstractmethod
    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        precondition: str | None = None,
    ) -> ObjectMetadata:
        """Write ``data`` to ``key``.

        ``precondition``: ``"absent"`` to create only if absent, or an etag string
        to replace only if the current etag matches. Violation -> PreconditionFailed.
        """

    @abstractmethod
    def get_bytes(self, key: str) -> bytes:
        """Read and return the full object at ``key``. Raises ObjectNotFoundError."""

    @abstractmethod
    def open_read(self, key: str) -> BinaryIO:
        """Open ``key`` for streaming binary read."""

    @abstractmethod
    def upload_file(
        self, local_path: Path, key: str, *, precondition: str | None = None
    ) -> ObjectMetadata:
        """Upload a local file to ``key`` (multipart for large files)."""

    @abstractmethod
    def download_file(self, key: str, local_path: Path) -> None:
        """Stream ``key`` to a local file."""

    @abstractmethod
    def list(self, prefix: str) -> Iterator[str]:
        """Yield every object key under ``prefix`` (paginated internally)."""

    @abstractmethod
    def copy(
        self, src_key: str, dst_key: str, *, precondition: str | None = None
    ) -> ObjectMetadata:
        """Copy an object within the backend (server-side on S3). ``precondition``
        ``"absent"`` makes the destination write create-only (immutable publish)."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete ``key`` (callers restrict this to authorized temporary objects)."""

    # ------------------------------------------------------------------ #
    # Shared concrete helpers
    # ------------------------------------------------------------------ #
    def sha256(self, key: str) -> str:
        """Return the hex SHA-256 of an object, computed by streaming it."""
        hasher = hashlib.sha256()
        with self.open_read(key) as fh:
            for chunk in iter(lambda: fh.read(_CHUNK), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def verify_sha256(self, key: str, expected: str) -> bool:
        """Return True iff the object's SHA-256 equals ``expected``."""
        return self.sha256(key) == expected
