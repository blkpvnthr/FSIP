"""Local data-lake inventory for migration planning.

Enumerates the existing published datasets (curated Parquet + manifests + raw
archives) and computes the counts, sizes, checksums, and estimated S3 object
counts / transfer volume a migration would require — all offline, before any
upload. This is the input to cost estimation and the pilot/full migration plan.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path

from ..config import Settings
from ..logging_config import get_logger

log = get_logger(__name__)


@dataclass
class DatasetInventory:
    dataset_id: str
    agency: str
    fiscal_year: int
    raw_archive_path: str | None
    raw_archive_sha256: str | None
    raw_archive_bytes: int
    parquet_file_count: int
    parquet_bytes: int
    manifest_bytes: int
    transaction_count: int
    net_obligation: str
    schema_fingerprint: str
    partition_count: int
    # S3 projection: 1 raw + N parquet parts + candidate manifest + snapshot record.
    s3_object_count: int = 0
    transfer_bytes: int = 0


@dataclass
class LakeInventory:
    datasets: list[DatasetInventory] = field(default_factory=list)
    total_transactions: int = 0
    total_net_obligation: str = "0"
    total_parquet_bytes: int = 0
    total_raw_bytes: int = 0
    total_s3_objects: int = 0
    total_transfer_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "datasets": [asdict(d) for d in self.datasets],
            "totals": {
                "datasets": len(self.datasets),
                "transactions": self.total_transactions,
                "net_obligation": self.total_net_obligation,
                "parquet_bytes": self.total_parquet_bytes,
                "raw_bytes": self.total_raw_bytes,
                "s3_objects": self.total_s3_objects,
                "transfer_bytes": self.total_transfer_bytes,
            },
        }


def _published_dir(settings: Settings, agency: str, fiscal_year: int) -> Path:
    return (
        settings.partitioned_parquet_dir
        / f"awarding_agency={agency.upper()}"
        / f"fiscal_year={fiscal_year}"
    )


def build_inventory(settings: Settings) -> LakeInventory:
    """Scan published datasets (via their manifests) and project migration needs."""
    inv = LakeInventory()
    net_total = Decimal("0")
    manifest_dir = settings.manifest_dir
    for manifest_path in sorted(manifest_dir.glob("*_contracts_fy*.json")):
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        agency = m["agency"]["key"]
        fy = m["fiscal_year"]
        published = _published_dir(settings, agency, fy)
        if not published.exists():
            continue  # only inventory datasets that are actually published
        parts = sorted(published.glob("**/*.parquet"))
        parquet_bytes = sum(p.stat().st_size for p in parts)
        src = m.get("source_archive", {})
        raw_bytes = src.get("size_bytes", 0) or 0
        net = Decimal(m["totals"]["net_obligation"])
        net_total += net
        s3_objects = 1 + len(parts) + 2  # raw + parts + candidate manifest + snapshot
        transfer = raw_bytes + parquet_bytes + manifest_path.stat().st_size

        d = DatasetInventory(
            dataset_id=m["dataset_id"],
            agency=agency,
            fiscal_year=fy,
            raw_archive_path=src.get("stored_path"),
            raw_archive_sha256=src.get("sha256"),
            raw_archive_bytes=raw_bytes,
            parquet_file_count=len(parts),
            parquet_bytes=parquet_bytes,
            manifest_bytes=manifest_path.stat().st_size,
            transaction_count=m["totals"]["valid_rows"],
            net_obligation=str(net),
            schema_fingerprint=m["schema_fingerprint"],
            partition_count=m["partition_count"],
            s3_object_count=s3_objects,
            transfer_bytes=transfer,
        )
        inv.datasets.append(d)
        inv.total_transactions += d.transaction_count
        inv.total_parquet_bytes += parquet_bytes
        inv.total_raw_bytes += raw_bytes
        inv.total_s3_objects += s3_objects
        inv.total_transfer_bytes += transfer

    inv.total_net_obligation = str(net_total)
    log.info(
        "inventory.built",
        extra={"context": {"datasets": len(inv.datasets), "transactions": inv.total_transactions}},
    )
    return inv
