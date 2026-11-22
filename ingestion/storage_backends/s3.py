"""AWS S3 storage backend (Boto3).

Maps logical keys to ``s3://<bucket>/<prefix>/<key>``. Uses Boto3 (never manual
AWS signing), the standard credential provider chain (profile or temporary
credentials), server-side encryption on every write, and multipart transfers for
large objects so multi-gigabyte archives are never buffered in memory.

Conditional writes use S3's native ``If-None-Match`` (create-only) and
``If-Match`` (compare-and-swap) preconditions, which back the immutable-raw and
optimistic-concurrency catalog-pointer guarantees. Boto3 is imported lazily so the
default local backend never requires it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from ..config import Settings
from ..exceptions import (
    ObjectNotFoundError,
    PreconditionFailedError,
    StorageConfigError,
    StorageError,
)
from ..logging_config import get_logger
from .base import ABSENT, ObjectMetadata, StorageBackend

if TYPE_CHECKING:  # pragma: no cover
    pass

log = get_logger(__name__)


class S3StorageBackend(StorageBackend):
    """S3-backed storage using Boto3."""

    name = "s3"

    def __init__(self, settings: Settings, *, client=None) -> None:
        settings.validate_cloud()
        self.settings = settings
        self.bucket = settings.s3_bucket
        self.prefix = settings.s3_prefix.strip("/")
        self._client = client
        self._transfer_config = None

    # ------------------------------------------------------------------ #
    # Boto3 wiring (lazy)
    # ------------------------------------------------------------------ #
    @property
    def client(self):
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config as BotoConfig
            except ImportError as exc:  # pragma: no cover - env-dependent
                raise StorageConfigError(
                    "The S3 backend requires boto3. Install with: pip install '.[aws]'"
                ) from exc
            session = boto3.Session(
                profile_name=self.settings.aws_profile or None,
                region_name=self.settings.aws_region,
            )
            cfg = BotoConfig(
                retries={"max_attempts": self.settings.s3_max_retries, "mode": "standard"},
                connect_timeout=self.settings.s3_request_timeout_seconds,
                read_timeout=self.settings.s3_request_timeout_seconds,
            )
            self._client = session.client("s3", config=cfg)
        return self._client

    @property
    def transfer_config(self):
        if self._transfer_config is None:
            from boto3.s3.transfer import TransferConfig

            mb = 1024 * 1024
            self._transfer_config = TransferConfig(
                multipart_threshold=self.settings.s3_multipart_threshold_mb * mb,
                multipart_chunksize=self.settings.s3_multipart_chunk_mb * mb,
                max_concurrency=self.settings.s3_max_concurrency,
                use_threads=True,
            )
        return self._transfer_config

    def _sse_args(self) -> dict:
        args: dict = {"ServerSideEncryption": self.settings.s3_encryption}
        if self.settings.s3_encryption == "aws:kms" and self.settings.s3_kms_key_id:
            args["SSEKMSKeyId"] = self.settings.s3_kms_key_id
        return args

    def _full_key(self, key: str) -> str:
        key = key.lstrip("/")
        return f"{self.prefix}/{key}" if self.prefix else key

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{self._full_key(key)}"

    # ------------------------------------------------------------------ #
    # Error mapping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _status(exc) -> int:
        return exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)

    def _raise(self, exc, key: str):
        from botocore.exceptions import ClientError

        if not isinstance(exc, ClientError):
            raise StorageError(str(exc)) from exc
        code = exc.response.get("Error", {}).get("Code", "")
        status = self._status(exc)
        if code in {"NoSuchKey", "404", "NotFound"} or status == 404:
            raise ObjectNotFoundError(self.uri(key)) from exc
        if code in {"PreconditionFailed", "412"} or status == 412:
            raise PreconditionFailedError(self.uri(key)) from exc
        raise StorageError(f"{code or status}: {self.uri(key)}") from exc

    # ------------------------------------------------------------------ #
    # Operations
    # ------------------------------------------------------------------ #
    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=self._full_key(key))
            return True
        except ClientError as exc:
            if self._status(exc) == 404 or exc.response.get("Error", {}).get("Code") in {
                "404",
                "NoSuchKey",
                "NotFound",
            }:
                return False
            self._raise(exc, key)

    def stat(self, key: str) -> ObjectMetadata:
        from botocore.exceptions import ClientError

        try:
            resp = self.client.head_object(Bucket=self.bucket, Key=self._full_key(key))
        except ClientError as exc:
            self._raise(exc, key)
        return ObjectMetadata(
            key=key,
            size=resp["ContentLength"],
            etag=resp["ETag"].strip('"'),
            last_modified=str(resp.get("LastModified")),
            content_type=resp.get("ContentType"),
        )

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        precondition: str | None = None,
    ) -> ObjectMetadata:
        from botocore.exceptions import ClientError

        params = {"Bucket": self.bucket, "Key": self._full_key(key), "Body": data}
        params.update(self._sse_args())
        if content_type:
            params["ContentType"] = content_type
        if precondition == ABSENT:
            params["IfNoneMatch"] = "*"
        elif precondition is not None:
            params["IfMatch"] = precondition
        try:
            self.client.put_object(**params)
        except ClientError as exc:
            self._raise(exc, key)
        return self.stat(key)

    def get_bytes(self, key: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=self._full_key(key))
            return resp["Body"].read()
        except ClientError as exc:
            self._raise(exc, key)

    def open_read(self, key: str) -> BinaryIO:
        from botocore.exceptions import ClientError

        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=self._full_key(key))
            return resp["Body"]  # botocore StreamingBody: supports chunked .read()
        except ClientError as exc:
            self._raise(exc, key)

    def upload_file(
        self, local_path: Path, key: str, *, precondition: str | None = None
    ) -> ObjectMetadata:
        # Content-addressed raw / snapshot objects use precondition="absent":
        # skip the upload if an identical key already exists (idempotent, no dup).
        if precondition == ABSENT and self.exists(key):
            raise PreconditionFailedError(self.uri(key))
        extra = self._sse_args()
        self.client.upload_file(
            str(local_path),
            self.bucket,
            self._full_key(key),
            ExtraArgs=extra,
            Config=self.transfer_config,
        )
        return self.stat(key)

    def download_file(self, key: str, local_path: Path) -> None:
        from botocore.exceptions import ClientError

        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.client.download_file(
                self.bucket,
                self._full_key(key),
                str(local_path),
                Config=self.transfer_config,
            )
        except ClientError as exc:
            self._raise(exc, key)

    def list(self, prefix: str) -> Iterator[str]:
        full_prefix = self._full_key(prefix) if prefix else self.prefix
        strip = (self.prefix + "/") if self.prefix else ""
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                full = obj["Key"]
                yield full[len(strip) :] if strip and full.startswith(strip) else full

    def copy(
        self, src_key: str, dst_key: str, *, precondition: str | None = None
    ) -> ObjectMetadata:
        from botocore.exceptions import ClientError

        if precondition == ABSENT and self.exists(dst_key):
            raise PreconditionFailedError(self.uri(dst_key))
        params = {
            "Bucket": self.bucket,
            "Key": self._full_key(dst_key),
            "CopySource": {"Bucket": self.bucket, "Key": self._full_key(src_key)},
        }
        params.update(self._sse_args())
        try:
            self.client.copy_object(**params)
        except ClientError as exc:
            self._raise(exc, dst_key)
        return self.stat(dst_key)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._full_key(key))
