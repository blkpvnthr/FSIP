"""USAspending.gov API client.

A small, dependency-injected HTTP client for the public USAspending bulk-download
API. It is deliberately transport-only: it knows how to *submit* an async
bulk-download job, *poll* it to completion, *stream* the resulting ZIP to disk,
*checksum* it, and *safely extract* it — but it holds no opinion about schemas or
business rules (those live in :mod:`ingestion.validate` / :mod:`ingestion.storage`).

Design choices
--------------
* **Bounded exponential backoff with full jitter.** Transient failures (timeouts,
  connection resets, HTTP 429/5xx) are retried up to ``settings.max_retries``
  attempts; a 429 ``Retry-After`` header is honored when present. Permanent
  failures (other 4xx) are raised immediately — retrying them is pointless.
* **Injected ``sleep``/``monotonic``.** Passing these in keeps the retry and
  poll loops fully testable without real wall-clock delays.
* **Streaming downloads + incremental SHA-256.** The ZIP is never fully buffered
  in memory; the checksum is computed as bytes arrive.
* **Safe extraction.** Guards against zip-slip (path traversal) and zip-bombs
  (uncompressed-size / file-count caps) before writing any member to disk.
"""

from __future__ import annotations

import hashlib
import os
import random
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import requests

from .config import Settings
from .exceptions import (
    DownloadJobError,
    DownloadJobTimeout,
    PermanentAPIError,
    TransientAPIError,
    UnsafeArchiveError,
)
from .logging_config import get_logger

log = get_logger(__name__)

_CHUNK_SIZE = 1024 * 1024  # 1 MiB streaming chunks
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

BULK_DOWNLOAD_PATH = "/api/v2/bulk_download/awards/"


class UsaspendingClient:
    """Transport client for the USAspending bulk-download API."""

    def __init__(
        self,
        settings: Settings,
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self._sleep = sleep
        self._monotonic = monotonic

    # ------------------------------------------------------------------ #
    # Low-level transport with bounded exponential backoff
    # ------------------------------------------------------------------ #
    def _backoff_seconds(self, attempt: int, retry_after: float | None = None) -> float:
        """Full-jitter exponential backoff, capped, honoring ``Retry-After``."""
        if retry_after is not None:
            return min(retry_after, self.settings.backoff_max_seconds)
        ceiling = min(
            self.settings.backoff_base_seconds * (2 ** (attempt - 1)),
            self.settings.backoff_max_seconds,
        )
        return random.uniform(0, ceiling)

    @staticmethod
    def _parse_retry_after(response: requests.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None  # HTTP-date form is uncommon here; fall back to backoff.

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Issue an HTTP request with retries on transient failures.

        Raises:
            TransientAPIError: retries exhausted on a retryable condition.
            PermanentAPIError: a non-retryable 4xx (other than 429).
        """
        kwargs.setdefault("timeout", self.settings.http_timeout_seconds)
        attempts = max(1, self.settings.max_retries)
        last_exc: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = self.session.request(method, url, **kwargs)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                log.warning(
                    "http.transient_exception",
                    extra={
                        "context": {
                            "url": url,
                            "attempt": attempt,
                            "max": attempts,
                            "error": type(exc).__name__,
                        }
                    },
                )
                if attempt < attempts:
                    self._sleep(self._backoff_seconds(attempt))
                continue

            if response.status_code in _RETRYABLE_STATUS:
                last_exc = TransientAPIError(f"{response.status_code} from {url}")
                log.warning(
                    "http.transient_status",
                    extra={
                        "context": {
                            "url": url,
                            "status": response.status_code,
                            "attempt": attempt,
                            "max": attempts,
                        }
                    },
                )
                if attempt < attempts:
                    self._sleep(self._backoff_seconds(attempt, self._parse_retry_after(response)))
                continue

            if 400 <= response.status_code < 500:
                raise PermanentAPIError(f"{response.status_code} from {url}: {response.text[:500]}")

            return response

        raise TransientAPIError(f"Exhausted {attempts} attempt(s) for {method} {url}") from last_exc

    # ------------------------------------------------------------------ #
    # Bulk-download job lifecycle
    # ------------------------------------------------------------------ #
    def submit_bulk_download(self, payload: dict) -> dict:
        """Submit an async bulk-download job.

        Returns the job descriptor containing ``status_url``, ``file_name`` and
        ``file_url``.
        """
        url = f"{self.settings.api_base_url}{BULK_DOWNLOAD_PATH}"
        log.info("download.submit", extra={"context": {"url": url}})
        response = self._request("POST", url, json=payload)
        data = response.json()
        log.info(
            "download.submitted",
            extra={"context": {"file_name": data.get("file_name")}},
        )
        return data

    def poll_download(self, status_url: str) -> dict:
        """Poll an async job until it finishes, fails, or times out.

        Raises:
            DownloadJobError: the job reported ``status == "failed"``.
            DownloadJobTimeout: the job did not finish within the budget.
        """
        deadline = self._monotonic() + self.settings.download_poll_timeout_seconds
        poll = 0
        while True:
            poll += 1
            response = self._request("GET", status_url)
            status_body = response.json()
            status = status_body.get("status")
            log.info(
                "download.poll",
                extra={"context": {"poll": poll, "status": status}},
            )
            if status == "finished":
                return status_body
            if status == "failed":
                raise DownloadJobError(f"Bulk-download job failed: {status_body.get('message')}")
            if self._monotonic() >= deadline:
                raise DownloadJobTimeout(
                    f"Job did not finish within "
                    f"{self.settings.download_poll_timeout_seconds}s "
                    f"(last status={status!r})"
                )
            self._sleep(self.settings.download_poll_interval_seconds)

    # ------------------------------------------------------------------ #
    # Streaming download + checksum
    # ------------------------------------------------------------------ #
    def download_file(self, file_url: str, dest_path: Path) -> tuple[int, str]:
        """Stream ``file_url`` to ``dest_path``; return ``(bytes_written, sha256)``.

        The file is streamed in chunks (never fully buffered) and the SHA-256 is
        computed incrementally as bytes arrive.
        """
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        hasher = hashlib.sha256()
        bytes_written = 0
        log.info("download.stream.start", extra={"context": {"url": file_url}})

        # Download is a single streamed GET; wrap transient failures in the same
        # bounded-retry policy as other requests.
        attempts = max(1, self.settings.max_retries)
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                hasher = hashlib.sha256()
                bytes_written = 0
                with self.session.get(
                    file_url, stream=True, timeout=self.settings.http_timeout_seconds
                ) as response:
                    if response.status_code in _RETRYABLE_STATUS:
                        raise TransientAPIError(f"{response.status_code} from {file_url}")
                    if 400 <= response.status_code < 500:
                        raise PermanentAPIError(f"{response.status_code} from {file_url}")
                    response.raise_for_status()
                    with open(dest_path, "wb") as fh:
                        for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                            if not chunk:
                                continue
                            fh.write(chunk)
                            hasher.update(chunk)
                            bytes_written += len(chunk)
                break
            except (requests.Timeout, requests.ConnectionError, TransientAPIError) as exc:
                last_exc = exc
                log.warning(
                    "download.stream.transient",
                    extra={"context": {"attempt": attempt, "error": type(exc).__name__}},
                )
                if attempt < attempts:
                    self._sleep(self._backoff_seconds(attempt))
                continue
        else:
            raise TransientAPIError(
                f"Exhausted {attempts} attempt(s) downloading {file_url}"
            ) from last_exc

        checksum = hasher.hexdigest()
        log.info(
            "download.stream.done",
            extra={"context": {"bytes": bytes_written, "sha256": checksum}},
        )
        return bytes_written, checksum


# --------------------------------------------------------------------------- #
# File helpers (module-level; pure functions, independently testable)
# --------------------------------------------------------------------------- #
def sha256_file(path: Path, chunk_size: int = _CHUNK_SIZE) -> str:
    """Return the hex SHA-256 digest of a file, read in chunks."""
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _is_within_directory(directory: Path, target: Path) -> bool:
    """True iff ``target`` resolves to a path inside ``directory``."""
    directory = directory.resolve()
    target = target.resolve()
    return directory == target or directory in target.parents


def safe_extract_zip(zip_path: Path, dest_dir: Path, settings: Settings) -> list[Path]:
    """Safely extract a ZIP, guarding against zip-slip and zip-bomb attacks.

    Checks performed *before* writing any member:

    * **Path traversal (zip-slip):** absolute paths or paths that escape
      ``dest_dir`` via ``..`` are rejected.
    * **File-count cap:** at most ``settings.max_extract_files`` members.
    * **Uncompressed-size cap:** total declared uncompressed size must not exceed
      ``settings.max_extract_bytes``.

    Returns the list of extracted file paths.

    Raises:
        UnsafeArchiveError: any guardrail is violated.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []

    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > settings.max_extract_files:
            raise UnsafeArchiveError(
                f"Archive has {len(infos)} members > cap {settings.max_extract_files}"
            )
        total_uncompressed = sum(i.file_size for i in infos)
        if total_uncompressed > settings.max_extract_bytes:
            raise UnsafeArchiveError(
                f"Archive uncompressed size {total_uncompressed} bytes > cap "
                f"{settings.max_extract_bytes}"
            )

        for info in infos:
            name = info.filename
            if os.path.isabs(name) or ".." in Path(name).parts:
                raise UnsafeArchiveError(f"Unsafe member path in archive: {name!r}")
            target = dest_dir / name
            if not _is_within_directory(dest_dir, target):
                raise UnsafeArchiveError(f"Member escapes extraction dir: {name!r}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                for chunk in iter(lambda s=src: s.read(_CHUNK_SIZE), b""):
                    dst.write(chunk)
            extracted.append(target)

    log.info(
        "archive.extracted",
        extra={"context": {"zip": str(zip_path), "files": len(extracted)}},
    )
    return extracted
