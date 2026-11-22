"""Opt-in live integration test against the real USAspending.gov API.

Deselected by default (``addopts = -m 'not live'`` in pyproject). Run explicitly::

    pytest -m live

Requires network access and downloads a small real NASA extract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ingestion.config import Settings
from ingestion.extract import run_extraction
from ingestion.models import ExtractRequest

pytestmark = pytest.mark.live


def test_live_nasa_october_2025_extraction(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_directories()
    request = ExtractRequest(agency="NASA", start_date="2025-10-01", end_date="2025-10-31")
    result = run_extraction(request, settings, force=True)

    assert result.parquet_path.exists()
    assert result.manifest_path.exists()
    assert result.raw_archive_path.exists()
    # Real data: there should be at least some valid NASA contract transactions.
    assert result.valid_row_count > 0
