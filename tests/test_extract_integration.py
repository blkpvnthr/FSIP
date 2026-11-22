"""Integration tests: the full pipeline over a controlled local dataset.

A fake client injects the sample ZIP (no network), so the extract -> validate ->
Parquet -> manifest chain is exercised end to end deterministically.
"""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from ingestion.config import Settings
from ingestion.extract import run_extraction
from ingestion.models import ExtractRequest

pytestmark = pytest.mark.integration


class FakeClient:
    """Stand-in for UsaspendingClient that serves a local ZIP."""

    def __init__(self, zip_fixture: Path):
        self._zip = zip_fixture
        self.submit_calls = 0

    def submit_bulk_download(self, payload: dict) -> dict:
        self.submit_calls += 1
        return {
            "status_url": "https://api.usaspending.gov/api/v2/download/status?file_name=x.zip",
            "file_name": "All_Contracts_PrimeTransactions_fake_1.zip",
            "file_url": "https://files.usaspending.gov/generated_downloads/x.zip",
        }

    def poll_download(self, status_url: str) -> dict:
        return {"status": "finished", "total_rows": 8, "total_columns": 17}

    def download_file(self, file_url: str, dest_path: Path) -> tuple[int, str]:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._zip, dest_path)
        from ingestion.client import sha256_file

        return dest_path.stat().st_size, sha256_file(dest_path)


@pytest.fixture
def request_obj() -> ExtractRequest:
    return ExtractRequest(agency="NASA", start_date="2025-10-01", end_date="2025-10-31")


def test_full_pipeline_produces_all_artifacts(
    settings: Settings, request_obj: ExtractRequest, sample_zip: Path
):
    client = FakeClient(sample_zip)
    result = run_extraction(request_obj, settings, client=client)

    assert result.valid_row_count == 4
    assert result.quarantined_row_count == 4
    assert result.obligation_total == "1250.25"

    assert result.parquet_path.exists()
    assert result.manifest_path.exists()
    assert result.quarantine_path and result.quarantine_path.exists()
    assert result.raw_archive_path.exists()  # raw preserved

    # Parquet content matches the validator totals, decimals intact.
    table = pq.read_table(result.parquet_path)
    assert table.num_rows == 4
    total = sum(
        (v for v in table.column("federal_action_obligation").to_pylist() if v is not None),
        Decimal("0"),
    )
    assert total == Decimal("1250.25")


def test_manifest_is_accurate_and_reconciles(
    settings: Settings, request_obj: ExtractRequest, sample_zip: Path
):
    client = FakeClient(sample_zip)
    result = run_extraction(request_obj, settings, client=client)
    manifest = json.loads(result.manifest_path.read_text())

    assert manifest["validated_total_rows"] == 8
    assert manifest["valid_row_count"] == 4
    assert manifest["quarantined_row_count"] == 4
    assert manifest["negative_obligation_count"] == 1
    assert manifest["obligation_total"] == "1250.25"
    assert manifest["column_count"] == 17
    assert manifest["source_archive"]["sha256"]
    assert len(manifest["source_archive"]["sha256"]) == 64

    rec = manifest["reconciliation"]
    assert rec["valid_plus_quarantined_equals_total"] is True
    assert rec["row_count_matches_reported"] is True


def test_idempotent_rerun_skips_and_does_not_duplicate(
    settings: Settings, request_obj: ExtractRequest, sample_zip: Path
):
    client = FakeClient(sample_zip)
    first = run_extraction(request_obj, settings, client=client)
    assert first.skipped is False
    assert client.submit_calls == 1

    # Second run with outputs present must skip (no second submit, same paths).
    second = run_extraction(request_obj, settings, client=client)
    assert second.skipped is True
    assert client.submit_calls == 1
    assert second.parquet_path == first.parquet_path

    # Parquet still has exactly 4 rows (no duplication).
    assert pq.read_table(second.parquet_path).num_rows == 4


def test_force_rerun_reprocesses(settings: Settings, request_obj: ExtractRequest, sample_zip: Path):
    client = FakeClient(sample_zip)
    run_extraction(request_obj, settings, client=client)
    forced = run_extraction(request_obj, settings, client=client, force=True)
    assert forced.skipped is False
    assert client.submit_calls == 2
    assert pq.read_table(forced.parquet_path).num_rows == 4
