"""Extraction orchestration.

Ties the single-purpose modules together into one reproducible pipeline:

    submit job -> poll -> stream ZIP (immutable raw) -> checksum -> safe-extract
    -> validate -> quarantine -> Parquet -> manifest

Responsibilities stay separated (this module only *orchestrates*); it owns the
deterministic on-disk layout and the idempotency contract: identical requests map
to identical output paths, and a completed extraction is skipped on re-run unless
``force=True``, so reprocessing never produces duplicate output records.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .client import UsaspendingClient, safe_extract_zip, sha256_file
from .config import Settings
from .logging_config import get_logger
from .manifest import (
    ExtractionManifest,
    SourceFileRecord,
    build_manifest,
    write_manifest,
)
from .models import ExtractRequest
from .storage import write_parquet
from .validate import validate_file, write_quarantine

log = get_logger(__name__)


@dataclass
class ExtractionResult:
    """What an extraction produced (paths + headline figures)."""

    request_slug: str
    raw_archive_path: Path
    source_csv_path: Path
    parquet_path: Path
    manifest_path: Path
    quarantine_path: Path | None
    valid_row_count: int
    quarantined_row_count: int
    obligation_total: str
    skipped: bool = False


def _find_single_csv(paths: list[Path]) -> Path:
    csvs = [p for p in paths if p.suffix.lower() == ".csv"]
    if len(csvs) != 1:
        raise ValueError(f"Expected exactly one CSV in archive, found {len(csvs)}: {csvs}")
    return csvs[0]


def run_extraction(
    request: ExtractRequest,
    settings: Settings,
    *,
    client: UsaspendingClient | None = None,
    force: bool = False,
) -> ExtractionResult:
    """Run the full extraction pipeline for ``request``.

    Args:
        request: the resolved extraction request.
        settings: runtime settings (paths, timeouts, retry policy).
        client: optional injected client (tests pass a fake/mock).
        force: re-run even if outputs already exist for this slug.
    """
    settings.ensure_directories()
    slug = request.slug
    parquet_path = settings.parquet_dir / f"{slug}.parquet"
    manifest_path = settings.manifest_dir / f"{slug}.json"
    raw_subdir = settings.raw_dir / slug

    # Idempotency: a completed extraction is not repeated unless forced.
    if parquet_path.exists() and manifest_path.exists() and not force:
        log.info("extract.skip_existing", extra={"context": {"slug": slug}})
        return _result_from_existing(request, settings, parquet_path, manifest_path, raw_subdir)

    client = client or UsaspendingClient(settings)

    # Clean the per-slug raw dir on a forced re-run so we never accumulate stale
    # server-timestamped archives alongside the current one.
    if raw_subdir.exists() and force:
        shutil.rmtree(raw_subdir)
    raw_subdir.mkdir(parents=True, exist_ok=True)

    # 1) Submit + poll the async bulk-download job.
    payload = request.to_api_payload()
    submit = client.submit_bulk_download(payload)
    status_url = submit["status_url"]
    job_status = client.poll_download(status_url)

    # 2) Stream the ZIP to the immutable raw area; checksum as it lands.
    original_name = submit["file_name"]
    zip_path = raw_subdir / original_name
    size_bytes, archive_sha = client.download_file(submit["file_url"], zip_path)

    # 3) Safely extract (zip-slip / zip-bomb guarded) into a sibling dir; the
    #    raw ZIP itself is never modified.
    extracted = safe_extract_zip(zip_path, raw_subdir / "extracted", settings)
    csv_path = _find_single_csv(extracted)

    # 4) Validate + partition into valid / quarantined.
    validation = validate_file(csv_path)

    # 5) Materialize valid rows to Parquet (decimal-safe, typed).
    write_parquet(validation.valid_rows, validation.header, parquet_path)

    # 6) Quarantine (if any) — preserved verbatim, never discarded.
    quarantine_path: Path | None = None
    if validation.quarantined:
        quarantine_path = settings.quarantine_dir / f"{slug}.csv"
        write_quarantine(validation, quarantine_path)

    # 7) Manifest — provenance + reconciliation.
    archive_record = SourceFileRecord(
        original_file_name=original_name,
        stored_path=str(zip_path),
        sha256=archive_sha,
        size_bytes=size_bytes,
    )
    csv_record = SourceFileRecord(
        original_file_name=csv_path.name,
        stored_path=str(csv_path),
        sha256=sha256_file(csv_path),
        size_bytes=csv_path.stat().st_size,
    )
    manifest = build_manifest(
        request=request,
        api_payload=payload,
        status_url=status_url,
        job_status=job_status,
        archive=archive_record,
        source_csv=csv_record,
        validation=validation,
        parquet_path=parquet_path,
        quarantine_path=quarantine_path,
    )
    write_manifest(manifest, manifest_path)

    return ExtractionResult(
        request_slug=slug,
        raw_archive_path=zip_path,
        source_csv_path=csv_path,
        parquet_path=parquet_path,
        manifest_path=manifest_path,
        quarantine_path=quarantine_path,
        valid_row_count=validation.valid_count,
        quarantined_row_count=validation.quarantined_count,
        obligation_total=str(validation.obligation_total()),
    )


def _result_from_existing(
    request: ExtractRequest,
    settings: Settings,
    parquet_path: Path,
    manifest_path: Path,
    raw_subdir: Path,
) -> ExtractionResult:
    """Reconstruct a result from a previously-completed extraction's manifest."""
    import json

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    qpath = data.get("quarantine_path")
    return ExtractionResult(
        request_slug=request.slug,
        raw_archive_path=Path(data["source_archive"]["stored_path"]),
        source_csv_path=Path(data["source_csv"]["stored_path"]),
        parquet_path=parquet_path,
        manifest_path=manifest_path,
        quarantine_path=Path(qpath) if qpath else None,
        valid_row_count=data["valid_row_count"],
        quarantined_row_count=data["quarantined_row_count"],
        obligation_total=data["obligation_total"],
        skipped=True,
    )


__all__ = ["ExtractionManifest", "ExtractionResult", "run_extraction"]
