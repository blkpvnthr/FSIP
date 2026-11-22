"""Opt-in LIVE AWS S3 integration test.

Deselected by default (``-m 'not live'``). Runs only when explicitly requested AND
``FSDW_ENABLE_LIVE_AWS_TESTS=true`` with a reachable, approved bucket. It writes
ONLY under a disposable ``live-test/`` prefix and cleans up after itself; it never
touches published datasets or unrelated AWS resources.

Run with::

    FSDW_ENABLE_LIVE_AWS_TESTS=true FSDW_S3_BUCKET=<approved-bucket> \
        FSDW_STORAGE_BACKEND=s3 pytest -m live tests/test_cloud_live.py
"""

from __future__ import annotations

import uuid

import pytest
from ingestion.config import Settings

pytestmark = pytest.mark.live


def test_live_s3_roundtrip():
    settings = Settings.from_env()
    if not settings.enable_live_aws_tests or settings.storage_backend != "s3":
        pytest.skip("Live AWS tests disabled (set FSDW_ENABLE_LIVE_AWS_TESTS=true + s3 backend).")
    from ingestion.storage_backends.s3 import S3StorageBackend

    backend = S3StorageBackend(settings)
    key = f"live-test/{uuid.uuid4().hex}.txt"
    try:
        backend.put_bytes(key, b"fsip live roundtrip")
        assert backend.exists(key)
        assert backend.get_bytes(key) == b"fsip live roundtrip"
        assert len(backend.sha256(key)) == 64
    finally:
        backend.delete(key)
    assert not backend.exists(key)
