"""Environment-driven configuration.

All tunables come from environment variables (optionally loaded from a local
``.env`` via python-dotenv). No secrets are embedded in code, and the public
USAspending API requires none. The ``Settings`` dataclass is the single source
of truth passed explicitly into the client and pipeline (dependency injection),
which keeps everything testable without monkeypatching globals.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    return raw if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings for the ingestion pipeline."""

    api_base_url: str = "https://api.usaspending.gov"
    http_timeout_seconds: float = 60.0
    max_retries: int = 5
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 30.0
    download_poll_timeout_seconds: float = 1800.0
    download_poll_interval_seconds: float = 5.0

    data_dir: Path = field(default_factory=lambda: Path("data"))
    log_level: str = "INFO"
    log_format: str = "json"

    max_extract_bytes: int = 2 * 1024 * 1024 * 1024
    max_extract_files: int = 256

    # Derived data-lake sub-directories (kept as properties so a single
    # ``data_dir`` override repositions the whole lake).
    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "parquet"

    @property
    def quarantine_dir(self) -> Path:
        return self.data_dir / "quarantine"

    @property
    def manifest_dir(self) -> Path:
        return self.data_dir / "manifests"

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> Settings:
        """Build settings from the process environment (and optionally ``.env``)."""
        if load_dotenv_file:
            load_dotenv(override=False)
        return cls(
            api_base_url=_env_str("USASPENDING_API_BASE_URL", cls.api_base_url).rstrip("/"),
            http_timeout_seconds=_env_float(
                "USASPENDING_HTTP_TIMEOUT_SECONDS", cls.http_timeout_seconds
            ),
            max_retries=_env_int("USASPENDING_MAX_RETRIES", cls.max_retries),
            backoff_base_seconds=_env_float(
                "USASPENDING_BACKOFF_BASE_SECONDS", cls.backoff_base_seconds
            ),
            backoff_max_seconds=_env_float(
                "USASPENDING_BACKOFF_MAX_SECONDS", cls.backoff_max_seconds
            ),
            download_poll_timeout_seconds=_env_float(
                "USASPENDING_DOWNLOAD_POLL_TIMEOUT_SECONDS", cls.download_poll_timeout_seconds
            ),
            download_poll_interval_seconds=_env_float(
                "USASPENDING_DOWNLOAD_POLL_INTERVAL_SECONDS", cls.download_poll_interval_seconds
            ),
            data_dir=Path(_env_str("FSDW_DATA_DIR", "data")),
            log_level=_env_str("FSDW_LOG_LEVEL", cls.log_level),
            log_format=_env_str("FSDW_LOG_FORMAT", cls.log_format),
            max_extract_bytes=_env_int("FSDW_MAX_EXTRACT_BYTES", cls.max_extract_bytes),
            max_extract_files=_env_int("FSDW_MAX_EXTRACT_FILES", cls.max_extract_files),
        )

    def ensure_directories(self) -> None:
        """Create the data-lake directory layout if it does not yet exist."""
        for path in (self.raw_dir, self.parquet_dir, self.quarantine_dir, self.manifest_dir):
            path.mkdir(parents=True, exist_ok=True)
