"""Tests for cloud migration, full-lake audit, inventory, and cost estimation."""

from __future__ import annotations

import pytest
from ingestion.cloud import migration, reconciliation
from ingestion.cloud.cost import estimate_costs
from ingestion.cloud.inventory import build_inventory
from ingestion.config import Settings

pytestmark = pytest.mark.integration


def test_migrate_dataset_and_idempotent_repeat(published_local: Settings, local_cloud_backend):
    first = migration.migrate_dataset(published_local, "NASA", 2026, backend=local_cloud_backend)
    assert first.status == "published"
    # Re-migrating identical content is a no-op (no duplicate snapshot).
    second = migration.migrate_dataset(published_local, "NASA", 2026, backend=local_cloud_backend)
    assert second.status == "skipped"
    assert second.idempotent is True


def test_full_lake_audit_matches_local(published_local: Settings, local_cloud_backend):
    migration.migrate_dataset(published_local, "NASA", 2026, backend=local_cloud_backend)
    report = reconciliation.audit_lake_cloud(published_local, backend=local_cloud_backend)
    assert report["passed"] is True
    assert report["transactions"] == 4
    assert report["distinct_keys"] == 4
    assert report["duplicate_keys"] == 0
    assert report["net_obligation"] == "250.00"


def test_dataset_reconcile_cloud(published_local: Settings, local_cloud_backend):
    migration.migrate_dataset(published_local, "NASA", 2026, backend=local_cloud_backend)
    rec = reconciliation.reconcile_dataset_cloud(
        published_local, "NASA", 2026, backend=local_cloud_backend
    )
    assert rec.passed is True
    names = {c["name"] for c in rec.checks}
    assert "net_obligation" in names
    assert "raw_archive_checksum" in names


def test_inventory(published_local: Settings):
    inv = build_inventory(published_local)
    assert inv.total_transactions == 4
    assert inv.total_net_obligation == "250.00"
    assert len(inv.datasets) == 1
    assert inv.datasets[0].s3_object_count >= 1 + 4 + 2  # raw + 4 parts + manifest + snapshot


def test_migrate_lake_subset(published_local: Settings, local_cloud_backend):
    results = migration.migrate_lake(
        published_local, datasets=[("NASA", 2026)], backend=local_cloud_backend
    )
    assert len(results) == 1
    assert results[0].status == "published"


def test_cloud_benchmark(published_local: Settings, tmp_path):
    from ingestion.cloud.benchmarking import benchmark_cloud

    result = benchmark_cloud(published_local, "NASA", 2026, tmp_root=tmp_path / "bench")
    assert result["backend"] == "local"
    assert result["rows"] == 4
    assert result["publication_seconds"] >= 0
    assert "query_full_readback_ms" in result
    # Network/S3 metrics are honestly null on the local backend.
    assert result["network_throughput_mb_s"] is None
    assert result["s3_request_count"] is None


def test_gc_detects_orphaned_snapshot_not_active_one(
    published_local: Settings, local_cloud_backend
):
    from ingestion.cloud import keys
    from ingestion.cloud.gc import find_orphaned_snapshots

    r = migration.migrate_dataset(published_local, "NASA", 2026, backend=local_cloud_backend)
    # No orphans right after a clean publish.
    assert find_orphaned_snapshots(local_cloud_backend) == []

    # Simulate a partial-failure orphan: copy one active part into an unreferenced
    # snapshot_id prefix (as a crash between curated-write and catalog-advance would).
    active_part = r.part_keys[0]
    orphan_key = keys.curated_part_key("NASA", 2026, "ORPHANSNAP", 1, "part-00000.parquet")
    local_cloud_backend.copy(active_part, orphan_key)

    orphans = find_orphaned_snapshots(local_cloud_backend)
    assert [o.snapshot_id for o in orphans] == ["ORPHANSNAP"]
    assert orphans[0].object_count == 1
    # The active snapshot is NOT flagged.
    assert r.snapshot_id not in {o.snapshot_id for o in orphans}


def test_cost_estimate(published_local: Settings):
    est = estimate_costs(published_local)
    assert est["recommended_budget_usd"] == 5
    assert est["budget_escalation_usd"] == 10
    assert "nasa_fy2026_pilot" in est["estimates"]
    assert "full_lake" in est["estimates"]
    # Estimates are non-negative dollar figures.
    assert est["estimates"]["full_lake"]["monthly_total_usd"] >= 0
    assert "NOT a hard spending cap" in est["budget_note"]
