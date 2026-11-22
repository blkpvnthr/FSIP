"""Tests for financial reconciliation (gate + published re-check)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from ingestion.archive import ingest_archive
from ingestion.config import Settings
from ingestion.reconciliation import (
    IngestTotals,
    ReconciliationResult,
    reconcile_published,
)

pytestmark = pytest.mark.integration


def test_reconciliation_result_passed_logic():
    r = ReconciliationResult()
    r.add("a", 1, 1)
    assert r.passed is True
    r.add("b", 1, 2)
    assert r.passed is False


def test_decimal_amounts_compare_exactly():
    r = ReconciliationResult()
    r.add("net", Decimal("250.00"), Decimal("250.00"))
    r.add("net2", Decimal("250.00"), Decimal("250.0"))  # equal numerically
    assert all(c.passed for c in r.checks)


def test_reconcile_published_passes_after_ingest(nasa_fy2026_archive: Path, settings: Settings):
    ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    result = reconcile_published("NASA", 2026, settings)
    assert result.passed is True
    names = {c.name for c in result.checks}
    assert "net_obligation_matches_manifest" in names
    assert "partition_count_matches_manifest" in names


def test_reconcile_published_detects_tampering(nasa_fy2026_archive: Path, settings: Settings):
    ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    # Corrupt the published data by deleting one partition's part-file.
    published = settings.partitioned_parquet_dir / "awarding_agency=NASA" / "fiscal_year=2026"
    victim = next(published.glob("fiscal_month=01/*.parquet"))
    victim.unlink()
    result = reconcile_published("NASA", 2026, settings)
    assert result.passed is False


def test_ingest_totals_defaults():
    t = IngestTotals()
    assert t.net_obligation == Decimal("0")
    assert t.source_rows == 0
