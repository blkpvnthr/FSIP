"""Pluggable storage backends (local filesystem / AWS S3).

The local backend is the default and requires no AWS dependencies. S3 is selected
only by explicit configuration (``storage_backend == "s3"``) — never implicitly by
the mere presence of AWS credentials.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Settings
from ..exceptions import StorageConfigError
from .base import ABSENT, ObjectMetadata, StorageBackend
from .local import LocalStorageBackend

__all__ = [
    "ABSENT",
    "LocalStorageBackend",
    "ObjectMetadata",
    "StorageBackend",
    "get_storage_backend",
]


def get_storage_backend(settings: Settings, *, local_root: Path | None = None) -> StorageBackend:
    """Return the configured storage backend.

    Args:
        settings: runtime settings (``storage_backend`` selects local vs s3).
        local_root: override the local backend's root (defaults to the cloud-lake
            emulation directory). Ignored for S3.

    Raises:
        StorageConfigError: an unknown backend name was configured.
    """
    backend = settings.storage_backend.lower()
    if backend == "local":
        return LocalStorageBackend(local_root or settings.cloud_local_root)
    if backend == "s3":
        from .s3 import S3StorageBackend  # lazy: avoids importing boto3 for local use

        return S3StorageBackend(settings)
    raise StorageConfigError(
        f"Unknown storage backend {settings.storage_backend!r} (expected 'local' or 's3')."
    )
