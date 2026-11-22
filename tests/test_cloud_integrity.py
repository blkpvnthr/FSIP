"""Tests for cloud transaction integrity, catalog CAS, and rollback."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from ingestion.cloud import catalog
from ingestion.cloud.publication import publish_local_dataset
from ingestion.config import Settings
from ingestion.exceptions import PreconditionFailedError

pytestmark = pytest.mark.integration


def _fabricate_doe_from_nasa(settings: Settings) -> None:
    """Create a local DOE FY2026 dataset reusing NASA FY2026's transaction keys.

    The local ingest pipeline would block this (its own cross-dataset gate), so we
    fabricate it directly on disk to exercise the independent CLOUD publish gate.
    Partition paths in the manifest are rewritten to DOE so reconciliation passes
    and the cross-dataset uniqueness check (not reconciliation) is what triggers.
    """
    p = settings.partitioned_parquet_dir
    shutil.copytree(
        p / "awarding_agency=NASA" / "fiscal_year=2026",
        p / "awarding_agency=DOE" / "fiscal_year=2026",
    )
    m = json.loads((settings.manifest_dir / "nasa_contracts_fy2026.json").read_text())
    m["dataset_id"] = "doe_contracts_fy2026"
    m["agency"] = {"key": "DOE", "name": "Department of Energy", "toptier_code": "089"}
    for part in m["partitions"]:
        part["partition"] = part["partition"].replace("awarding_agency=NASA", "awarding_agency=DOE")
    (settings.manifest_dir / "doe_contracts_fy2026.json").write_text(json.dumps(m))


def test_cross_dataset_uniqueness_blocks(published_local: Settings, local_cloud_backend):
    publish_local_dataset(published_local, "NASA", 2026, backend=local_cloud_backend)
    _fabricate_doe_from_nasa(published_local)
    r = publish_local_dataset(published_local, "DOE", 2026, backend=local_cloud_backend)
    assert r.status == "integrity_conflict"
    # DOE is not added to the catalog.
    cat, _ = catalog.read_current(local_cloud_backend)
    assert "doe_contracts_fy2026" not in cat.datasets


def test_force_does_not_bypass_cross_dataset_uniqueness(
    published_local: Settings, local_cloud_backend
):
    publish_local_dataset(published_local, "NASA", 2026, backend=local_cloud_backend)
    _fabricate_doe_from_nasa(published_local)
    r = publish_local_dataset(published_local, "DOE", 2026, backend=local_cloud_backend, force=True)
    assert r.status == "integrity_conflict"  # force never bypasses uniqueness


def test_catalog_cas_conflict_raises(published_local: Settings, local_cloud_backend):
    publish_local_dataset(published_local, "NASA", 2026, backend=local_cloud_backend)
    cat, etag = catalog.read_current(local_cloud_backend)
    other = cat.with_dataset(cat.datasets["nasa_contracts_fy2026"])
    catalog.publish_catalog(local_cloud_backend, other, etag)  # advances the pointer
    with pytest.raises(PreconditionFailedError):
        catalog.publish_catalog(local_cloud_backend, other, etag)  # stale etag -> conflict


def _ingest_fy2025(settings: Settings, make_archive: Callable[..., Path]) -> None:
    from ingestion.archive import ingest_archive

    rows = [
        _row25("TX25_1", "AWX", "500.00", "2024-10-15"),
        _row25("TX25_2", "AWY", "-20.00", "2025-01-10"),
    ]
    archive = make_archive({"FY2025_part_1.csv": rows}, name="nasa_fy2025.zip")
    ingest_archive(archive, "NASA", 2025, settings)


def _row25(tx, award, oblig, action_date):
    from tests.conftest import _row

    return _row(tx, award, oblig, action_date)


def test_rollback_restores_previous_catalog(
    published_local: Settings, local_cloud_backend, make_archive
):
    r1 = publish_local_dataset(published_local, "NASA", 2026, backend=local_cloud_backend)
    assert r1.catalog_version == 1
    _ingest_fy2025(published_local, make_archive)
    r2 = publish_local_dataset(published_local, "NASA", 2025, backend=local_cloud_backend)
    assert r2.status == "published"
    assert r2.catalog_version == 2

    restored = catalog.rollback_to(local_cloud_backend, target_version=1)
    assert restored.version == 3  # append-only history
    assert set(restored.datasets) == {"nasa_contracts_fy2026"}  # back to the v1 dataset set
