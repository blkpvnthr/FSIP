"""Tests for the performance benchmarking suite (offline, real subprocess)."""

from __future__ import annotations

from pathlib import Path

import pytest
from ingestion.archive import ingest_archive
from ingestion.benchmarking import (
    benchmark,
    benchmark_phases,
    benchmark_queries,
)
from ingestion.config import Settings

pytestmark = pytest.mark.integration


def test_benchmark_phases(nasa_fy2026_archive: Path, settings: Settings):
    phases = benchmark_phases(nasa_fy2026_archive, settings)
    assert phases["rows"] == 10
    for key in ("csv_parse_seconds", "schema_validation_seconds", "parquet_conversion_seconds"):
        assert phases[key] >= 0


def test_benchmark_queries_pruning_evidence(nasa_fy2026_archive: Path, settings: Settings):
    ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    q = benchmark_queries("NASA", 2026, settings, repeats=2)
    pruning = q["partition_pruning"]
    assert pruning["rows_in_dataset"] == 4
    assert pruning["total_partition_files"] == 4
    # Plan text is captured as pruning evidence.
    assert isinstance(q["partition_pruning_plan"], str)
    assert q["partition_pruning_plan"]


def test_full_benchmark_writes_artifact(nasa_fy2026_archive: Path, settings: Settings):
    ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    result = benchmark("NASA", 2026, settings, repeats=2)
    assert result["ingestion"]["rows"] == 4
    assert result["ingestion"]["peak_rss_mb_max"] > 0
    assert result["environment"]["duckdb_version"]
    assert Path(result["_output_path"]).exists()
    assert "phase_timings_seconds" in result["ingestion"]
