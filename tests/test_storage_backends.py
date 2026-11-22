"""Storage-backend tests: local + S3 (moto) parity and conditional writes."""

from __future__ import annotations

import contextlib
from dataclasses import replace
from pathlib import Path

import boto3
import pytest
from ingestion.config import Settings
from ingestion.exceptions import ObjectNotFoundError, PreconditionFailedError
from ingestion.storage_backends import ABSENT, LocalStorageBackend
from ingestion.storage_backends.s3 import S3StorageBackend
from moto import mock_aws


@contextlib.contextmanager
def _backend(kind: str, tmp_path: Path):
    if kind == "local":
        yield LocalStorageBackend(tmp_path / "lake")
    else:
        with mock_aws():
            boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="fsip-test")
            s = Settings(
                storage_backend="s3", s3_bucket="fsip-test", s3_prefix="dev", aws_region="us-east-1"
            )
            yield S3StorageBackend(s)


@pytest.mark.parametrize("kind", ["local", "s3"])
def test_put_get_exists_stat(kind, tmp_path):
    with _backend(kind, tmp_path) as b:
        meta = b.put_bytes("raw/a.txt", b"hello")
        assert meta.size == 5
        assert b.exists("raw/a.txt")
        assert b.get_bytes("raw/a.txt") == b"hello"
        assert b.stat("raw/a.txt").size == 5
        assert not b.exists("raw/missing.txt")


@pytest.mark.parametrize("kind", ["local", "s3"])
def test_sha256_matches_hashlib(kind, tmp_path):
    import hashlib

    data = b"federal spending" * 100
    with _backend(kind, tmp_path) as b:
        b.put_bytes("x", data)
        assert b.sha256("x") == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("kind", ["local", "s3"])
def test_missing_object_raises(kind, tmp_path):
    with _backend(kind, tmp_path) as b, pytest.raises(ObjectNotFoundError):
        b.get_bytes("nope")


@pytest.mark.parametrize("kind", ["local", "s3"])
def test_conditional_create_only(kind, tmp_path):
    with _backend(kind, tmp_path) as b:
        b.put_bytes("k", b"1", precondition=ABSENT)
        with pytest.raises(PreconditionFailedError):
            b.put_bytes("k", b"2", precondition=ABSENT)


@pytest.mark.parametrize("kind", ["local", "s3"])
def test_conditional_compare_and_swap(kind, tmp_path):
    with _backend(kind, tmp_path) as b:
        m = b.put_bytes("k", b"1")
        b.put_bytes("k", b"2", precondition=m.etag)  # matching etag OK
        with pytest.raises(PreconditionFailedError):
            b.put_bytes("k", b"3", precondition="wrong-etag")


@pytest.mark.parametrize("kind", ["local", "s3"])
def test_upload_download_copy_list_delete(kind, tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"A" * (1024 * 200))
    with _backend(kind, tmp_path) as b:
        b.upload_file(src, "staging/part.bin")
        assert b.stat("staging/part.bin").size == 1024 * 200
        b.copy("staging/part.bin", "curated/part.bin", precondition=ABSENT)
        assert b.exists("curated/part.bin")
        keys = set(b.list(""))
        assert {"staging/part.bin", "curated/part.bin"} <= keys
        out = tmp_path / "out.bin"
        b.download_file("curated/part.bin", out)
        assert out.read_bytes() == src.read_bytes()
        b.delete("staging/part.bin")
        assert not b.exists("staging/part.bin")


@pytest.mark.parametrize("kind", ["local", "s3"])
def test_list_prefix_filtering(kind, tmp_path):
    with _backend(kind, tmp_path) as b:
        b.put_bytes("raw/x", b"1")
        b.put_bytes("curated/y", b"2")
        assert list(b.list("raw/")) == ["raw/x"]


def test_s3_prefix_in_uri(s3_backend):
    assert s3_backend.uri("raw/a").startswith("s3://fsip-test-bucket/dev/raw/a")


def test_s3_uses_encryption_param(settings):
    # SSE is included on every write (SSE-S3 by default).
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="enc")
        s = replace(settings, storage_backend="s3", s3_bucket="enc", aws_region="us-east-1")
        b = S3StorageBackend(s)
        b.put_bytes("k", b"data")
        head = b.client.head_object(Bucket="enc", Key="dev/k")
        assert head["ServerSideEncryption"] == "AES256"
