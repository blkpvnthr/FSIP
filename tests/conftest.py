"""Shared pytest fixtures."""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from ingestion.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_CSV = FIXTURES / "sample_contracts.csv"

# Minimal header carrying every REQUIRED_COLUMN plus a couple of typed extras,
# used to synthesize controlled archive fixtures for Milestone-2 tests.
M2_HEADER = [
    "contract_transaction_unique_key",
    "contract_award_unique_key",
    "award_id_piid",
    "modification_number",
    "federal_action_obligation",
    "total_dollars_obligated",
    "action_date",
    "last_modified_date",
    "period_of_performance_start_date",
    "recipient_uei",
    "recipient_name",
    "naics_code",
    "awarding_agency_code",
    "awarding_sub_agency_code",
    "funding_agency_code",
]


def _row(
    tx,
    award,
    oblig,
    action_date,
    *,
    mod="P00001",
    uei="UEI000000001",
    name="ACME AEROSPACE",
    naics="541715",
) -> dict:
    return {
        "contract_transaction_unique_key": tx,
        "contract_award_unique_key": award,
        "award_id_piid": award,
        "modification_number": mod,
        "federal_action_obligation": oblig,
        "total_dollars_obligated": oblig,
        "action_date": action_date,
        "last_modified_date": "2026-01-15 12:00:00+00",
        "period_of_performance_start_date": "2025-10-01",
        "recipient_uei": uei,
        "recipient_name": name,
        "naics_code": naics,
        "awarding_agency_code": "080",
        "awarding_sub_agency_code": "8000",
        "funding_agency_code": "080",
    }


def _write_zip(zip_path: Path, members: dict[str, list[dict]]) -> Path:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, rows in members.items():
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=M2_HEADER)
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
            zf.writestr(name, buf.getvalue())
    return zip_path


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


@pytest.fixture
def make_archive(tmp_path: Path) -> Callable[..., Path]:
    """Factory: build a ZIP archive from {member_name: [row dict, ...]}."""

    def _make(members: dict[str, list[dict]], name: str = "archive.zip") -> Path:
        return _write_zip(tmp_path / name, members)

    return _make


# Controlled NASA FY2026 archive exercising every routing branch. Expected under
# an FY2026 ingest: source=10, valid=4, filtered=1, quarantined=5, duplicates=1,
# net=250.00, positive=300.00, negative=-50.00, neg=1, zero=1, partitions=4
# (fiscal months 01, 04, 05, 12).
M2_MEMBER_1 = [
    _row("TX_A1", "AW1", "100.00", "2025-10-15"),  # FM01
    _row("TX_A2", "AW1", "-50.00", "2026-01-10", mod="P00002"),  # FM04, de-obligation
    _row("TX_A3", "AW2", "200.00", "2026-09-30"),  # FM12
    _row("TX_A4", "AW3", "999.00", "2025-05-01"),  # FY2025 -> filtered (out of scope)
    _row("TX_A5", "AW4", "", "2025-11-01"),  # blank obligation -> quarantine
    _row("TX_A6", "AW5", "BAD", "2025-12-01"),  # malformed obligation -> quarantine
    _row("TX_A7", "AW6", "10.00", ""),  # missing action_date -> partition quarantine
]
M2_MEMBER_2 = [
    _row("TX_B1", "AW7", "0.00", "2026-02-15"),  # FM05, zero
    _row("TX_A1", "AW1", "100.00", "2025-10-15"),  # duplicate key -> quarantine
    _row("", "AW8", "5.00", "2026-03-01"),  # missing transaction key -> quarantine
]


@pytest.fixture
def nasa_fy2026_archive(make_archive: Callable[..., Path]) -> Path:
    """Controlled two-member NASA FY2026 archive (see expected counts above)."""
    return make_archive(
        {
            "All_Contracts_PrimeTransactions_part_1.csv": M2_MEMBER_1,
            "All_Contracts_PrimeTransactions_part_2.csv": M2_MEMBER_2,
        },
        name="nasa_fy2026.zip",
    )


# --------------------------------------------------------------------------- #
# Milestone 3: cloud fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def published_local(settings: Settings, nasa_fy2026_archive: Path) -> Settings:
    """Settings with a small NASA FY2026 dataset ingested + published locally.

    The controlled fixture publishes 4 valid rows across 4 fiscal months with a
    known net obligation of 250.00 — the basis for cloud publication/migration tests.
    """
    from ingestion.archive import ingest_archive

    ingest_archive(nasa_fy2026_archive, "NASA", 2026, settings)
    return settings


@pytest.fixture
def local_cloud_backend(settings: Settings):
    """A local storage backend emulating the cloud object lake (separate root)."""
    from ingestion.storage_backends import get_storage_backend

    return get_storage_backend(settings, local_root=settings.cloud_local_root)


@pytest.fixture
def s3_backend(settings: Settings):
    """A moto-mocked S3 backend (offline). Yields a configured S3StorageBackend."""
    import boto3
    from ingestion.storage_backends.s3 import S3StorageBackend
    from moto import mock_aws

    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="fsip-test-bucket")
        s3_settings = replace(
            settings,
            storage_backend="s3",
            s3_bucket="fsip-test-bucket",
            s3_prefix="dev",
            aws_region="us-east-1",
        )
        yield S3StorageBackend(s3_settings)
