"""Extraction manifest — the provenance record for every extraction.

One JSON manifest is written per extraction. It captures *exactly* what was
requested, when, from where, and what was produced — so any downstream artifact
(Parquet, quarantine) can be traced back to an immutable, checksummed source file
and a reproducible API request. The manifest never contains fabricated figures:
counts and totals are copied from the validator and the live job status, and are
cross-checked against each other at build time.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .logging_config import get_logger
from .models import ExtractRequest
from .validate import ValidationResult

log = get_logger(__name__)

MANIFEST_SCHEMA_VERSION = "1.0"


@dataclass
class SourceFileRecord:
    """Immutable description of one preserved raw source file."""

    original_file_name: str
    stored_path: str
    sha256: str
    size_bytes: int


@dataclass
class ExtractionManifest:
    """Full provenance record for a single extraction run."""

    manifest_schema_version: str
    extraction_timestamp_utc: str
    request: dict
    api_request_payload: dict
    status_url: str
    source_archive: SourceFileRecord
    source_csv: SourceFileRecord
    schema_fingerprint: str
    column_count: int
    columns: list[str]
    reported_total_rows: int | None
    reported_total_columns: int | None
    validated_total_rows: int
    valid_row_count: int
    quarantined_row_count: int
    negative_obligation_count: int
    obligation_total: str
    warning_count: int
    parquet_path: str
    quarantine_path: str | None
    reconciliation: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=False, default=str)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_manifest(
    *,
    request: ExtractRequest,
    api_payload: dict,
    status_url: str,
    job_status: dict,
    archive: SourceFileRecord,
    source_csv: SourceFileRecord,
    validation: ValidationResult,
    parquet_path: Path,
    quarantine_path: Path | None,
) -> ExtractionManifest:
    """Assemble a manifest, cross-checking reported vs. observed row counts."""
    reported_rows = job_status.get("total_rows")
    observed_rows = validation.total_rows

    reconciliation = {
        # Reported-by-API vs. rows actually read from the CSV.
        "reported_total_rows": reported_rows,
        "observed_source_rows": observed_rows,
        "row_count_matches_reported": (
            reported_rows is None or int(reported_rows) == observed_rows
        ),
        # Partition accounting must be exhaustive: valid + quarantined == total.
        "valid_plus_quarantined_equals_total": (
            validation.valid_count + validation.quarantined_count == observed_rows
        ),
    }

    return ExtractionManifest(
        manifest_schema_version=MANIFEST_SCHEMA_VERSION,
        extraction_timestamp_utc=_now_iso(),
        request=_request_dict(request),
        api_request_payload=api_payload,
        status_url=status_url,
        source_archive=archive,
        source_csv=source_csv,
        schema_fingerprint=validation.schema_fingerprint,
        column_count=len(validation.header),
        columns=validation.header,
        reported_total_rows=(int(reported_rows) if reported_rows is not None else None),
        reported_total_columns=job_status.get("total_columns"),
        validated_total_rows=observed_rows,
        valid_row_count=validation.valid_count,
        quarantined_row_count=validation.quarantined_count,
        negative_obligation_count=validation.negative_obligation_count(),
        obligation_total=str(validation.obligation_total()),
        warning_count=len(validation.warnings),
        parquet_path=str(parquet_path),
        quarantine_path=(str(quarantine_path) if quarantine_path else None),
        reconciliation=reconciliation,
    )


def _request_dict(request: ExtractRequest) -> dict:
    return {
        "agency": request.agency,
        "agency_toptier_code": request.agency_meta["toptier_code"],
        "start_date": request.start_date,
        "end_date": request.end_date,
        "award_type_group": request.award_type_group.value,
        "prime_award_type_codes": request.prime_award_type_codes,
        "date_type": request.date_type,
        "agency_type": request.agency_type,
        "file_format": request.file_format,
        "slug": request.slug,
    }


def write_manifest(manifest: ExtractionManifest, dest_path: Path) -> Path:
    """Write the manifest JSON atomically; return the written path."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    tmp_path.write_text(manifest.to_json(), encoding="utf-8")
    tmp_path.replace(dest_path)
    log.info("manifest.written", extra={"context": {"path": str(dest_path)}})
    return dest_path
