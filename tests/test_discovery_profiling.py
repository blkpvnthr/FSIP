"""Tests for dataset discovery and historical profiling."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from ingestion.archive import ingest_archive
from ingestion.config import Settings
from ingestion.discovery import discover_datasets
from ingestion.profiling import profile_dataset

pytestmark = pytest.mark.integration


def _place_archive(archive: Path, settings: Settings, fy: int) -> Path:
    dest = settings.archive_root / "nasa" / f"fy{fy}" / archive.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(archive, dest)
    return dest


def test_discovery_reports_processed_status(nasa_fy2026_archive: Path, settings: Settings):
    placed = _place_archive(nasa_fy2026_archive, settings, 2026)
    # Before ingest: discovered but not processed.
    entries = discover_datasets(settings, compute_checksums=False)
    assert any(e.agency == "NASA" and e.fiscal_year == 2026 for e in entries)
    assert all(e.processing_status == "not_processed" for e in entries)

    ingest_archive(placed, "NASA", 2026, settings)
    entries = discover_datasets(settings, compute_checksums=True)
    nasa = next(e for e in entries if e.fiscal_year == 2026)
    assert nasa.processing_status == "published"
    assert nasa.schema_fingerprint is not None
    assert len(nasa.sha256) == 64
    assert nasa.published_path is not None


def test_profile_from_published_dataset(nasa_fy2026_archive: Path, settings: Settings):
    ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    result = profile_dataset("NASA", 2026, settings)
    ds = result.profile["dataset_statistics"]
    fin = result.profile["financial_statistics"]

    assert ds["published_rows"] == 4
    assert ds["source_rows"] == 10
    assert ds["quarantined_rows"] == 5
    assert ds["filtered_rows"] == 1
    assert ds["fiscal_months_present"] == [1, 4, 5, 12]
    assert fin["net_obligation"] == "250.00"
    assert fin["negative_obligation_count"] == 1
    assert fin["zero_obligation_count"] == 1

    assert result.json_path.exists()
    assert result.markdown_path.exists()
    assert "Data Profile" in result.markdown_path.read_text()
