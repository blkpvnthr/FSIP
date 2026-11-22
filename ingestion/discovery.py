"""Local dataset discovery and inventory.

Scans the data lake for USAspending contract archives and reports an inventory
entry per archive: agency, fiscal year, archive type, source path, size, SHA-256,
processing status, schema fingerprint, associated manifest, and published dataset
location. The status/fingerprint/published fields are recovered from the dataset
manifest written at ingestion time, so discovery reflects what has actually been
processed and published rather than guessing.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from .client import sha256_file
from .config import Settings
from .logging_config import get_logger

log = get_logger(__name__)

_FY_RE = re.compile(r"fy(\d{4})", re.IGNORECASE)


@dataclass
class DatasetInventoryEntry:
    agency: str | None
    fiscal_year: int | None
    archive_type: str  # "full" | "api"
    source_path: str
    size_bytes: int
    sha256: str | None
    processing_status: str  # published | staged | conflict | not_processed | ...
    schema_fingerprint: str | None
    manifest_path: str | None
    published_path: str | None


def _manifest_for(dataset_id: str, settings: Settings) -> dict | None:
    path = settings.manifest_dir / f"{dataset_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def discover_datasets(
    settings: Settings, *, compute_checksums: bool = True
) -> list[DatasetInventoryEntry]:
    """Return an inventory of all discoverable local contract archives."""
    entries: list[DatasetInventoryEntry] = []

    # Full / API-generated full archives under the Milestone-2 archive root:
    #   data/raw/usaspending/contracts/full/<agency>/fy<YYYY>/*.zip
    if settings.archive_root.exists():
        for zip_path in sorted(settings.archive_root.glob("*/fy*/*.zip")):
            rel = zip_path.relative_to(settings.archive_root)
            agency = rel.parts[0].upper() if rel.parts else None
            fy_match = _FY_RE.search(rel.parts[1]) if len(rel.parts) > 1 else None
            fiscal_year = int(fy_match.group(1)) if fy_match else None
            dataset_id = (
                f"{agency.lower()}_contracts_fy{fiscal_year}" if agency and fiscal_year else None
            )
            manifest = _manifest_for(dataset_id, settings) if dataset_id else None
            entries.append(
                DatasetInventoryEntry(
                    agency=agency,
                    fiscal_year=fiscal_year,
                    archive_type="full",
                    source_path=str(zip_path),
                    size_bytes=zip_path.stat().st_size,
                    sha256=sha256_file(zip_path) if compute_checksums else None,
                    processing_status=(manifest or {}).get("status", "not_processed"),
                    schema_fingerprint=(manifest or {}).get("schema_fingerprint"),
                    manifest_path=str(settings.manifest_dir / f"{dataset_id}.json")
                    if manifest
                    else None,
                    published_path=(manifest or {}).get("published_path"),
                )
            )

    # Milestone-1 API extractions: data/raw/<slug>/*.zip (excludes the M2 tree).
    if settings.raw_dir.exists():
        for slug_dir in sorted(p for p in settings.raw_dir.iterdir() if p.is_dir()):
            if slug_dir.name == "usaspending":
                continue
            for zip_path in sorted(slug_dir.glob("*.zip")):
                manifest = _manifest_for(slug_dir.name, settings)
                entries.append(
                    DatasetInventoryEntry(
                        agency=None,
                        fiscal_year=None,
                        archive_type="api",
                        source_path=str(zip_path),
                        size_bytes=zip_path.stat().st_size,
                        sha256=sha256_file(zip_path) if compute_checksums else None,
                        processing_status="published" if manifest else "not_processed",
                        schema_fingerprint=(manifest or {}).get("schema_fingerprint"),
                        manifest_path=str(settings.manifest_dir / f"{slug_dir.name}.json")
                        if manifest
                        else None,
                        published_path=(manifest or {}).get("parquet_path"),
                    )
                )

    log.info("discovery.done", extra={"context": {"archives": len(entries)}})
    return entries


def inventory_as_dicts(entries: list[DatasetInventoryEntry]) -> list[dict]:
    return [asdict(e) for e in entries]
