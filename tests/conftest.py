"""Shared pytest fixtures."""

from __future__ import annotations

import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from ingestion.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_CSV = FIXTURES / "sample_contracts.csv"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pointed at an isolated per-test data directory, fast retries."""
    base = Settings(
        data_dir=tmp_path / "data",
        max_retries=3,
        backoff_base_seconds=0.0,
        backoff_max_seconds=0.0,
        download_poll_interval_seconds=0.0,
        download_poll_timeout_seconds=5.0,
    )
    base.ensure_directories()
    return base


@pytest.fixture
def sample_csv_path() -> Path:
    return SAMPLE_CSV


@pytest.fixture
def sample_zip(tmp_path: Path) -> Path:
    """A ZIP wrapping the controlled sample CSV (the shape the API returns)."""
    zip_path = tmp_path / "sample.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(SAMPLE_CSV, arcname="All_Contracts_PrimeTransactions_sample_1.csv")
    return zip_path


@pytest.fixture
def no_sleep_settings(settings: Settings) -> Settings:
    """Settings guaranteed to never introduce real delays."""
    return replace(
        settings,
        backoff_base_seconds=0.0,
        backoff_max_seconds=0.0,
        download_poll_interval_seconds=0.0,
    )
