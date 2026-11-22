"""Tests for dataset-wide (cross-dataset) transaction-identity integrity."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from ingestion.archive import ingest_archive
from ingestion.config import Settings
from ingestion.integrity import audit_integrity

pytestmark = pytest.mark.integration


def test_audit_clean_single_dataset(nasa_fy2026_archive: Path, settings: Settings):
    ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    report = audit_integrity(settings)
    assert report.passed is True
    assert report.duplicate_key_count == 0
    assert report.published_datasets == ["NASA/FY2026"]


def test_cross_dataset_overlap_blocks_publication(nasa_fy2026_archive: Path, settings: Settings):
    # Publish NASA FY2026, then try to publish the SAME transactions as DOE FY2026.
    ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    r = ingest_archive(nasa_fy2026_archive, "DOE", 2026, settings)
    assert r.status == "integrity_conflict"
    assert r.conflict is True
    overlap = r.extra["cross_dataset_overlap"]
    assert overlap["overlapping_keys"] == 4  # the 4 valid NASA FY2026 keys
    assert overlap["identical_keys"] == 4
    # DOE was NOT published — no double counting.
    assert not (settings.partitioned_parquet_dir / "awarding_agency=DOE").exists()


def test_force_does_not_bypass_cross_dataset_uniqueness(
    nasa_fy2026_archive: Path, settings: Settings
):
    # --force may replace a dataset's OWN version, but must NEVER publish
    # transaction keys that collide with a different published dataset.
    ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    forced = ingest_archive(nasa_fy2026_archive, "DOE", 2026, settings, force=True)
    assert forced.status == "integrity_conflict"  # force does NOT bypass uniqueness
    assert not (settings.partitioned_parquet_dir / "awarding_agency=DOE").exists()
    # The lake remains duplicate-free.
    assert audit_integrity(settings).passed is True


def test_audit_detects_injected_duplicates(nasa_fy2026_archive: Path, settings: Settings):
    # Simulate a pre-existing integrity breach by copying NASA's published parquet
    # into a DOE partition (bypassing the pipeline), then confirm the auditor flags it.
    import shutil

    ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    nasa = settings.partitioned_parquet_dir / "awarding_agency=NASA" / "fiscal_year=2026"
    doe = settings.partitioned_parquet_dir / "awarding_agency=DOE" / "fiscal_year=2026"
    shutil.copytree(nasa, doe)

    report = audit_integrity(settings)
    assert report.passed is False
    assert report.cross_dataset_key_count == 4
    assert report.identical_duplicate_count == 4  # identical values
    assert report.conflicting_key_count == 0


def test_conflicting_version_classified(
    nasa_fy2026_archive: Path, make_archive: Callable[..., Path], settings: Settings
):
    from tests.conftest import _row

    ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    # DOE archive reusing NASA's TX_A1 key but with a DIFFERENT obligation.
    conflicting = make_archive(
        {"part.csv": [_row("TX_A1", "AW1", "777.77", "2025-10-15")]},
        name="conflict.zip",
    )
    r = ingest_archive(conflicting, "DOE", 2026, settings)
    assert r.status == "integrity_conflict"
    overlap = r.extra["cross_dataset_overlap"]
    assert overlap["overlapping_keys"] == 1
    assert overlap["conflicting_keys"] == 1


def test_idempotent_republish_not_flagged_as_overlap(nasa_fy2026_archive: Path, settings: Settings):
    # Re-publishing the SAME dataset must not self-collide in the integrity gate.
    ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    r = ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    assert r.status == "published"
    assert r.extra["cross_dataset_overlap"]["overlapping_keys"] == 0
    published = settings.partitioned_parquet_dir / "awarding_agency=NASA" / "fiscal_year=2026"
    total = sum(pq.read_metadata(f).num_rows for f in published.glob("**/*.parquet"))
    assert total == 4  # no duplication
