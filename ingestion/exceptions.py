"""Typed exceptions for the ingestion pipeline.

Keeping a small, explicit exception hierarchy lets callers (and tests)
distinguish *transient* transport failures from *permanent* contract failures
without string-matching error messages.
"""

from __future__ import annotations


class IngestionError(Exception):
    """Base class for all ingestion errors."""


class APIError(IngestionError):
    """Base class for USAspending API transport/protocol errors."""


class TransientAPIError(APIError):
    """A retryable error (timeout, connection reset, 429, or 5xx).

    The client's backoff loop treats these as retryable; everything else is
    surfaced immediately.
    """


class PermanentAPIError(APIError):
    """A non-retryable error (4xx other than 429, or a malformed response)."""


class DownloadJobError(APIError):
    """The asynchronous bulk-download job reported ``status == "failed"``."""


class DownloadJobTimeout(APIError):
    """The asynchronous bulk-download job did not finish within the budget."""


class UnsafeArchiveError(IngestionError):
    """A ZIP member failed path-traversal / size safety checks (zip-slip or bomb)."""


class SchemaContractError(IngestionError):
    """The source file is missing one or more columns the data contract requires."""


class ValidationError(IngestionError):
    """A non-recoverable validation failure (distinct from per-row quarantine)."""


# --------------------------------------------------------------------------- #
# Milestone 3: storage-backend errors
# --------------------------------------------------------------------------- #
class StorageError(IngestionError):
    """Base class for storage-backend errors (local or S3)."""


class ObjectNotFoundError(StorageError):
    """A requested object key does not exist."""


class PreconditionFailedError(StorageError):
    """A conditional write failed (object already exists, or etag mismatch).

    Used for optimistic-concurrency control on the published-catalog pointer and
    for create-only writes of immutable raw / curated snapshot objects.
    """


class StorageConfigError(StorageError):
    """Cloud/storage configuration is missing or invalid."""


class CloudPublicationError(IngestionError):
    """A cloud publication could not be completed safely."""


class IntegrityError(IngestionError):
    """A dataset-wide transaction-uniqueness or reconciliation invariant was violated."""
