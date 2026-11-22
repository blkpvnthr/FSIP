"""Deterministic object-key conventions for the cloud data lake.

A single bucket is organized into logical zones. Keys are deterministic functions
of ``(agency, fiscal_year, fiscal_month, content hash, run id, snapshot id)`` so the
same inputs always map to the same object, agency/funding scopes never mix, and raw
archives are content-addressed (identical content → identical key → no duplicate
uploads).

Zones::

    raw/        immutable, content-addressed source ZIPs (SHA-256 in the key)
    staging/    run-isolated candidate datasets (never read by published analytics)
    curated/    versioned, Hive-partitioned Parquet snapshots (immutable once published)
    quarantine/ rejected records + failure evidence, by run id
    control/    schemas, manifests, snapshots, checkpoints, reconciliation, current.json
    reports/    profiles, benchmarks, integrity, cost
"""

from __future__ import annotations

CURRENT_CATALOG_KEY = "control/current.json"


def dataset_id(agency: str, fiscal_year: int) -> str:
    return f"{agency.lower()}_contracts_fy{fiscal_year}"


def raw_key(agency: str, fiscal_year: int, sha256: str, filename: str = "source.zip") -> str:
    """Content-addressed immutable raw archive key (SHA-256 in the path)."""
    return (
        f"raw/usaspending/contracts/awarding_agency={agency.upper()}/"
        f"fiscal_year={fiscal_year}/sha256={sha256}/{filename}"
    )


def staging_prefix(run_id: str) -> str:
    return f"staging/run_id={run_id}"


def staging_part_key(
    run_id: str, agency: str, fiscal_year: int, fiscal_month: int, part: str
) -> str:
    return (
        f"{staging_prefix(run_id)}/parquet/awarding_agency={agency.upper()}/"
        f"fiscal_year={fiscal_year}/fiscal_month={fiscal_month:02d}/{part}"
    )


def curated_prefix(agency: str, fiscal_year: int, snapshot_id: str) -> str:
    return (
        f"curated/contracts/awarding_agency={agency.upper()}/"
        f"fiscal_year={fiscal_year}/snapshot_id={snapshot_id}"
    )


def curated_part_key(
    agency: str, fiscal_year: int, snapshot_id: str, fiscal_month: int, part: str
) -> str:
    return (
        f"{curated_prefix(agency, fiscal_year, snapshot_id)}/"
        f"fiscal_month={fiscal_month:02d}/{part}"
    )


def quarantine_key(run_id: str, filename: str) -> str:
    return f"quarantine/run_id={run_id}/{filename}"


def candidate_manifest_key(agency: str, fiscal_year: int, snapshot_id: str) -> str:
    return f"control/manifests/{dataset_id(agency, fiscal_year)}/{snapshot_id}.json"


def snapshot_record_key(snapshot_id: str) -> str:
    return f"control/snapshots/{snapshot_id}.json"


def catalog_version_key(version: int) -> str:
    return f"control/catalogs/v{version:06d}.json"


def reconciliation_key(agency: str, fiscal_year: int, snapshot_id: str) -> str:
    return f"control/reconciliation/{dataset_id(agency, fiscal_year)}/{snapshot_id}.json"


def report_key(kind: str, filename: str) -> str:
    """kind in {profiles, benchmarks, integrity, cost}."""
    return f"reports/{kind}/{filename}"
