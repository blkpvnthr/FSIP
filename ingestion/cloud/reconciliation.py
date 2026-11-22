"""Cloud financial reconciliation and integrity audit.

Verifies the reconciliation chain::

    Local validated dataset -> verified curated snapshot -> published catalog
                            -> independent cloud analytical readback

All internal comparisons are decimal-exact (``decimal128(23,2)`` semantics; values
compared as exact ``Decimal``, never floats). The full-lake audit also re-checks
dataset-wide transaction uniqueness across every active snapshot in the catalog.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal

from ..config import Settings
from ..logging_config import get_logger
from ..storage_backends import get_storage_backend
from ..storage_backends.base import StorageBackend
from . import catalog as catalog_mod
from . import keys, verification

log = get_logger(__name__)


@dataclass
class CloudReconciliation:
    scope: str
    checks: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c["ok"] for c in self.checks)

    def add(self, name: str, expected, actual) -> None:
        self.checks.append(
            {
                "name": name,
                "expected": str(expected),
                "actual": str(actual),
                "ok": expected == actual,
            }
        )

    def to_dict(self) -> dict:
        return {"scope": self.scope, "passed": self.passed, "checks": self.checks}


def reconcile_dataset_cloud(
    settings: Settings,
    agency: str,
    fiscal_year: int,
    *,
    backend: StorageBackend | None = None,
) -> CloudReconciliation:
    """Reconcile a published cloud dataset against its local manifest (exact)."""
    backend = backend or get_storage_backend(settings)
    dataset_id = keys.dataset_id(agency, fiscal_year)
    result = CloudReconciliation(scope=dataset_id)

    local_manifest_path = settings.manifest_dir / f"{dataset_id}.json"
    local = json.loads(local_manifest_path.read_text(encoding="utf-8"))["totals"]

    cat, _ = catalog_mod.read_current(backend)
    entry = cat.datasets.get(dataset_id)
    if entry is None:
        result.add("dataset_in_catalog", True, False)
        return result

    rb = verification.readback_totals(backend, settings, entry.part_keys)
    result.add("transaction_count", local["valid_rows"], rb.transaction_count)
    result.add("distinct_keys", local["distinct_valid_tx"], rb.distinct_keys)
    result.add("net_obligation", Decimal(local["net_obligation"]), rb.net_obligation)
    result.add("catalog_count_matches_readback", entry.transaction_count, rb.transaction_count)

    # Raw archive checksum must match the local source archive.
    raw_src = json.loads(local_manifest_path.read_text())["source_archive"]
    raw_key = keys.raw_key(agency, fiscal_year, raw_src["sha256"])
    raw_ok = backend.exists(raw_key) and backend.sha256(raw_key) == raw_src["sha256"]
    result.add("raw_archive_checksum", True, raw_ok)
    return result


def audit_lake_cloud(settings: Settings, *, backend: StorageBackend | None = None) -> dict:
    """Full-lake cloud audit: totals + dataset-wide transaction uniqueness."""
    backend = backend or get_storage_backend(settings)
    cat, _ = catalog_mod.read_current(backend)
    all_part_keys = [k for ds in cat.datasets.values() for k in ds.part_keys]
    if not all_part_keys:
        return {
            "datasets": 0,
            "transactions": 0,
            "distinct_keys": 0,
            "net_obligation": "0",
            "duplicate_keys": 0,
            "passed": True,
        }

    rb = verification.readback_totals(backend, settings, all_part_keys)
    duplicates = rb.transaction_count - rb.distinct_keys
    net = str(rb.net_obligation)
    passed = duplicates == 0
    report = {
        "datasets": len(cat.datasets),
        "dataset_ids": sorted(cat.datasets),
        "transactions": rb.transaction_count,
        "distinct_keys": rb.distinct_keys,
        "duplicate_keys": duplicates,
        "net_obligation": net,
        "catalog_version": cat.version,
        "passed": passed,
    }
    log.info(
        "cloud.lake_audit",
        extra={"context": {"passed": passed, "transactions": rb.transaction_count}},
    )
    return report
