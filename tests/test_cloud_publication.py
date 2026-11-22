"""Tests for the manifest-based cloud publication protocol."""

from __future__ import annotations

from decimal import Decimal

import pytest
from ingestion.cloud import catalog, keys, verification
from ingestion.cloud.publication import publish_local_dataset
from ingestion.config import Settings

pytestmark = pytest.mark.integration


def test_publish_happy_path(published_local: Settings, local_cloud_backend):
    r = publish_local_dataset(published_local, "NASA", 2026, backend=local_cloud_backend)
    assert r.status == "published"
    assert r.transaction_count == 4
    assert r.net_obligation == "250.00"
    assert r.reconciliation["passed"] is True
    assert len(r.part_keys) == 4  # 4 fiscal-month partitions

    # Catalog resolves the active snapshot; readback is decimal-exact.
    cat, _ = catalog.read_current(local_cloud_backend)
    entry = cat.datasets["nasa_contracts_fy2026"]
    rb = verification.readback_totals(local_cloud_backend, published_local, entry.part_keys)
    assert rb.transaction_count == 4
    assert rb.net_obligation == Decimal("250.00")


def test_dry_run_writes_nothing(published_local: Settings, local_cloud_backend):
    r = publish_local_dataset(
        published_local, "NASA", 2026, backend=local_cloud_backend, dry_run=True
    )
    assert r.status == "dry_run"
    assert r.plan["parquet_part_count"] == 4
    # Nothing written: catalog pointer absent.
    assert not local_cloud_backend.exists(keys.CURRENT_CATALOG_KEY)


def test_raw_archive_is_content_addressed_and_verified(
    published_local: Settings, local_cloud_backend
):
    r = publish_local_dataset(published_local, "NASA", 2026, backend=local_cloud_backend)
    assert r.raw_key and "sha256=" in r.raw_key
    # The raw object's checksum equals the hash embedded in its key.
    embedded = r.raw_key.split("sha256=")[1].split("/")[0]
    assert local_cloud_backend.sha256(r.raw_key) == embedded


def test_candidate_manifest_and_snapshot_recorded(published_local: Settings, local_cloud_backend):
    r = publish_local_dataset(published_local, "NASA", 2026, backend=local_cloud_backend)
    cm = keys.candidate_manifest_key("NASA", 2026, r.snapshot_id)
    assert local_cloud_backend.exists(cm)
    assert local_cloud_backend.exists(keys.snapshot_record_key(r.snapshot_id))


def test_reconciliation_failure_blocks_publication(
    published_local: Settings, local_cloud_backend, monkeypatch
):
    # Force a reconciliation mismatch by tampering the readback totals.
    from ingestion.cloud import publication
    from ingestion.cloud import verification as v

    real = v.readback_totals

    def fake(backend, settings, part_keys):
        t = real(backend, settings, part_keys)
        return v.ContentTotals(
            t.transaction_count + 1, t.distinct_keys, t.net_obligation, t.partition_counts
        )

    monkeypatch.setattr(publication.verification, "readback_totals", fake)
    r = publish_local_dataset(published_local, "NASA", 2026, backend=local_cloud_backend)
    assert r.status == "reconciliation_failed"
    assert not local_cloud_backend.exists(keys.CURRENT_CATALOG_KEY)  # never published
