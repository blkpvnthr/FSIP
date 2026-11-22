"""Unit + integration tests for historical archive ingestion."""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from ingestion.archive import ingest_archive, inspect_archive, iter_csv_batches
from ingestion.config import Settings
from ingestion.exceptions import UnsafeArchiveError

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Inspection / safety
# --------------------------------------------------------------------------- #
def test_inspect_happy_path(nasa_fy2026_archive: Path, settings: Settings):
    insp = inspect_archive(nasa_fy2026_archive, settings)
    assert len(insp.csv_members) == 2
    assert len(insp.zip_sha256) == 64
    assert insp.total_uncompressed_bytes > 0


def test_inspect_rejects_corrupted_zip(tmp_path: Path, settings: Settings):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"this is not a zip file")
    with pytest.raises(UnsafeArchiveError):
        inspect_archive(bad, settings)


def test_inspect_rejects_unsupported_file_type(tmp_path: Path, settings: Settings):
    z = tmp_path / "exe.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("payload.exe", "MZ...")
    with pytest.raises(UnsafeArchiveError):
        inspect_archive(z, settings)


def test_inspect_rejects_archive_without_csv(tmp_path: Path, settings: Settings):
    z = tmp_path / "nocsv.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("readme.txt", "no data here")
    with pytest.raises(UnsafeArchiveError):
        inspect_archive(z, settings)


def test_iter_csv_batches_bounded(nasa_fy2026_archive: Path, settings: Settings, tmp_path: Path):
    from ingestion.client import safe_extract_zip

    extracted = safe_extract_zip(nasa_fy2026_archive, tmp_path / "ex", settings)
    csv_path = next(p for p in extracted if p.suffix == ".csv")
    batches = list(iter_csv_batches(csv_path, batch_size=2))
    assert all(len(b) <= 2 for _, b in batches)


# --------------------------------------------------------------------------- #
# Full ingest (controlled dataset, exact accounting)
# --------------------------------------------------------------------------- #
def test_full_ingest_accounting(nasa_fy2026_archive: Path, settings: Settings):
    r = ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    t = r.totals
    assert r.status == "published"
    assert t.source_rows == 10
    assert t.valid_rows == 4
    assert t.filtered_rows == 1  # the FY2025 row is out of scope
    assert t.quarantined_rows == 5
    assert t.duplicate_rows == 1
    assert t.net_obligation == Decimal("250.00")
    assert t.positive_obligation == Decimal("300.00")
    assert t.negative_obligation == Decimal("-50.00")
    assert t.negative_count == 1
    assert t.zero_count == 1
    assert r.partition_count == 4  # fiscal months 01, 04, 05, 12
    assert r.reconciliation.passed is True


def test_ingest_publishes_hive_partitions(nasa_fy2026_archive: Path, settings: Settings):
    r = ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    months = sorted(p.name for p in r.published_path.glob("fiscal_month=*"))
    assert months == ["fiscal_month=01", "fiscal_month=04", "fiscal_month=05", "fiscal_month=12"]
    total = sum(pq.read_metadata(f).num_rows for f in r.published_path.glob("**/*.parquet"))
    assert total == 4


def test_quarantine_captures_all_reasons(nasa_fy2026_archive: Path, settings: Settings):
    r = ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    text = r.quarantine_path.read_text()
    for reason in [
        "blank_obligation",
        "malformed_obligation",
        "missing_or_unparseable_action_date",
        "duplicate_transaction_key",
        "missing_transaction_key",
    ]:
        assert reason in text


def test_negatives_preserved_in_published_parquet(nasa_fy2026_archive: Path, settings: Settings):
    r = ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    table = pq.read_table(r.published_path)
    obligs = table.column("federal_action_obligation").to_pylist()
    assert Decimal("-50.00") in obligs


# --------------------------------------------------------------------------- #
# Idempotency / conflict / dry-run
# --------------------------------------------------------------------------- #
def test_idempotent_rerun_no_duplication(nasa_fy2026_archive: Path, settings: Settings):
    ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    second = ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    assert second.idempotent is True
    assert second.conflict is False
    total = sum(pq.read_metadata(f).num_rows for f in second.published_path.glob("**/*.parquet"))
    assert total == 4  # not 8


def test_conflict_is_not_silently_overwritten(
    nasa_fy2026_archive: Path, make_archive: Callable[..., Path], settings: Settings
):
    from tests.conftest import M2_MEMBER_1

    ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    # A different-content archive for the same dataset: drop one valid row.
    altered = make_archive({"part_1.csv": M2_MEMBER_1[:-1]}, name="altered.zip")
    r = ingest_archive(altered, "NASA", 2026, settings)
    assert r.conflict is True
    assert r.status == "conflict"
    # Published data unchanged (still the original 4 rows).
    published = settings.partitioned_parquet_dir / "awarding_agency=NASA" / "fiscal_year=2026"
    total = sum(pq.read_metadata(f).num_rows for f in published.glob("**/*.parquet"))
    assert total == 4


def test_force_overwrites_conflict(
    nasa_fy2026_archive: Path, make_archive: Callable[..., Path], settings: Settings
):
    from tests.conftest import M2_MEMBER_1

    ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    altered = make_archive({"part_1.csv": M2_MEMBER_1[:-1]}, name="altered2.zip")
    r = ingest_archive(altered, "NASA", 2026, settings, force=True)
    assert r.status == "published"
    assert r.conflict is False


def test_dry_run_does_not_publish(nasa_fy2026_archive: Path, settings: Settings):
    r = ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings, dry_run=True)
    assert r.status == "dry_run"
    published = settings.partitioned_parquet_dir / "awarding_agency=NASA"
    assert not published.exists()


def test_resume_skips_completed_dataset(nasa_fy2026_archive: Path, settings: Settings):
    first = ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    assert first.status == "published"
    # With --resume, a prior published run for the same archive checksum is skipped.
    resumed = ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings, resume=True)
    assert resumed.status == "skipped"
    assert resumed.totals.valid_rows == 4


def test_raw_archive_is_immutable(nasa_fy2026_archive: Path, settings: Settings):
    before = nasa_fy2026_archive.read_bytes()
    ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    assert nasa_fy2026_archive.read_bytes() == before
