"""Historical archive ingestion pipeline.

Ingests a full USAspending contract archive ZIP (manually downloaded, or produced
by the bulk-download API) into the partitioned analytical data lake:

    inspect (integrity · SHA-256 · safety) -> safe extract -> stream CSV batches
    -> validate (+ dedup) -> fiscal scope filter -> partitioned Parquet (staging)
    -> reconcile (gate) -> atomic publish (+ conflict detection) -> manifest

The raw ZIP is treated as immutable and is never modified. Processing is streamed
in bounded-memory batches, deduplicated across members, and published atomically
only after reconciliation passes. Re-ingesting identical input is idempotent;
re-ingesting *different* content for an already-published dataset is reported as a
conflict and is **not** silently overwritten (deferred to the CDC milestone).
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from .agencies import Agency, resolve
from .checkpoints import Checkpoint, load_checkpoint, save_checkpoint
from .client import safe_extract_zip, sha256_file
from .config import Settings
from .exceptions import IngestionError, UnsafeArchiveError
from .logging_config import get_logger
from .models import OBLIGATION_COLUMN, schema_fingerprint
from .partitioning import PartitionedParquetWriter, PartitionKey, partition_key_for_row
from .reconciliation import IngestTotals, ReconciliationResult, reconcile_dataset
from .schema_registry import record_schema
from .validate import (
    QuarantineWriter,
    parse_decimal,
    validate_batch,
    verify_contract,
)

log = get_logger(__name__)

MANIFEST_SCHEMA_VERSION = "2.0"
_ALLOWED_MEMBER_SUFFIXES = {".csv"}


# --------------------------------------------------------------------------- #
# Archive inspection
# --------------------------------------------------------------------------- #
@dataclass
class ArchiveMember:
    name: str
    size_bytes: int
    compress_bytes: int
    is_csv: bool


@dataclass
class ArchiveInspection:
    zip_path: Path
    zip_sha256: str
    zip_size_bytes: int
    members: list[ArchiveMember]
    total_uncompressed_bytes: int

    @property
    def csv_members(self) -> list[str]:
        return [m.name for m in self.members if m.is_csv]

    def summary(self) -> dict:
        return {
            "zip_path": str(self.zip_path),
            "zip_sha256": self.zip_sha256,
            "zip_size_bytes": self.zip_size_bytes,
            "member_count": len(self.members),
            "csv_members": self.csv_members,
            "total_uncompressed_bytes": self.total_uncompressed_bytes,
        }


def inspect_archive(zip_path: Path, settings: Settings) -> ArchiveInspection:
    """Validate and describe an archive without extracting it.

    Checks ZIP integrity (corruption), the file-count and uncompressed-size caps,
    and member-path safety (traversal / symlinks / unsupported file types).

    Raises:
        UnsafeArchiveError: integrity or safety guardrail violated.
    """
    if not zip_path.exists():
        raise IngestionError(f"Archive not found: {zip_path}")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise UnsafeArchiveError(f"Corrupted archive member: {bad!r}")
            infos = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise UnsafeArchiveError(f"Not a valid ZIP archive: {zip_path} ({exc})") from exc

    if len(infos) > settings.max_extract_files:
        raise UnsafeArchiveError(
            f"Archive has {len(infos)} members > cap {settings.max_extract_files}"
        )
    total = sum(i.file_size for i in infos)
    if total > settings.max_extract_bytes:
        raise UnsafeArchiveError(
            f"Archive uncompressed size {total} > cap {settings.max_extract_bytes}"
        )

    members: list[ArchiveMember] = []
    for info in infos:
        if info.is_dir():
            continue
        name = info.filename
        if os.path.isabs(name) or ".." in Path(name).parts:
            raise UnsafeArchiveError(f"Unsafe member path: {name!r}")
        import stat as _stat

        if _stat.S_ISLNK(info.external_attr >> 16):
            raise UnsafeArchiveError(f"Symlink member not allowed: {name!r}")
        suffix = Path(name).suffix.lower()
        is_csv = suffix in _ALLOWED_MEMBER_SUFFIXES
        if suffix and suffix not in _ALLOWED_MEMBER_SUFFIXES and suffix not in {".txt", ".md"}:
            # Unsupported binary/payload types are rejected; benign text is tolerated.
            raise UnsafeArchiveError(f"Unsupported member file type: {name!r}")
        members.append(
            ArchiveMember(
                name=name,
                size_bytes=info.file_size,
                compress_bytes=info.compress_size,
                is_csv=is_csv,
            )
        )

    inspection = ArchiveInspection(
        zip_path=zip_path,
        zip_sha256=sha256_file(zip_path),
        zip_size_bytes=zip_path.stat().st_size,
        members=members,
        total_uncompressed_bytes=total,
    )
    if not inspection.csv_members:
        raise UnsafeArchiveError(f"Archive contains no CSV members: {zip_path}")
    log.info("archive.inspected", extra={"context": inspection.summary()})
    return inspection


# --------------------------------------------------------------------------- #
# Streaming CSV batches (bounded memory)
# --------------------------------------------------------------------------- #
def iter_csv_batches(
    csv_path: Path, batch_size: int
) -> Iterator[tuple[list[str], list[dict[str, str]]]]:
    """Yield ``(header, batch)`` tuples of at most ``batch_size`` rows each."""
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = list(reader.fieldnames or [])
        batch: list[dict[str, str]] = []
        for row in reader:
            batch.append(row)
            if len(batch) >= batch_size:
                yield header, batch
                batch = []
        if batch:
            yield header, batch


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #
@dataclass
class ArchiveIngestResult:
    dataset_id: str
    agency: str
    fiscal_year: int
    status: str  # published | staged | conflict | skipped | dry_run
    totals: IngestTotals
    reconciliation: ReconciliationResult | None
    manifest_path: Path | None
    published_path: Path | None
    quarantine_path: Path | None
    partition_count: int
    parquet_file_count: int
    conflict: bool = False
    idempotent: bool = False
    inspection: ArchiveInspection | None = None
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _fy_window(fiscal_year: int) -> tuple[date, date]:
    return date(fiscal_year - 1, 10, 1), date(fiscal_year, 9, 30)


def download_full_archive(agency_identifier: str, fiscal_year: int, settings: Settings) -> Path:
    """Download an agency's full fiscal-year contract archive via the API.

    Used by ``backfill --download`` for fiscal years not present locally. The ZIP
    is stored immutably under ``archive_root/<agency>/fy<YYYY>/``. This hits the
    USAspending bulk-download API, so it requires network access and should only be
    invoked with explicit operator opt-in.
    """
    from .client import UsaspendingClient
    from .models import AwardTypeGroup, ExtractRequest

    agency = resolve(agency_identifier)
    fy_start, fy_end = _fy_window(fiscal_year)
    request = ExtractRequest(
        agency=agency.key,
        start_date=fy_start.isoformat(),
        end_date=fy_end.isoformat(),
        award_type_group=AwardTypeGroup.CONTRACTS,
    )
    client = UsaspendingClient(settings)
    submit = client.submit_bulk_download(request.to_api_payload())
    client.poll_download(submit["status_url"])
    dest_dir = settings.archive_root / agency.key.lower() / f"fy{fiscal_year}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / submit["file_name"]
    client.download_file(submit["file_url"], dest)
    log.info(
        "archive.downloaded",
        extra={"context": {"agency": agency.key, "fiscal_year": fiscal_year, "path": str(dest)}},
    )
    return dest


def _tally_obligation(row: dict[str, str], totals: IngestTotals) -> None:
    value = parse_decimal(row.get(OBLIGATION_COLUMN))  # valid rows: non-blank, parseable
    if value is None:
        return
    totals.net_obligation += value
    if value > 0:
        totals.positive_obligation += value
    elif value < 0:
        totals.negative_obligation += value
        totals.negative_count += 1
    else:
        totals.zero_count += 1


def _content_signature(totals: IngestTotals, fingerprint: str) -> dict:
    return {
        "valid_rows": totals.valid_rows,
        "net_obligation": str(totals.net_obligation),
        "distinct_valid_tx": totals.distinct_valid_tx,
        "schema_fingerprint": fingerprint,
    }


def ingest_archive(
    zip_path: Path,
    agency_identifier: str,
    fiscal_year: int,
    settings: Settings,
    *,
    dry_run: bool = False,
    force: bool = False,
    resume: bool = False,
    timings: dict[str, float] | None = None,
) -> ArchiveIngestResult:
    """Ingest one full contract archive into the partitioned data lake.

    See the module docstring for the pipeline stages and guarantees.
    """
    agency: Agency = resolve(agency_identifier)
    dataset_id = f"{agency.key.lower()}_contracts_fy{fiscal_year}"
    fy_start, fy_end = _fy_window(fiscal_year)

    inspection = inspect_archive(zip_path, settings)

    if dry_run:
        # A dry run inspects only — it creates no directories and publishes nothing.
        log.info("archive.dry_run", extra={"context": {"dataset_id": dataset_id}})
        return ArchiveIngestResult(
            dataset_id=dataset_id,
            agency=agency.key,
            fiscal_year=fiscal_year,
            status="dry_run",
            totals=IngestTotals(),
            reconciliation=None,
            manifest_path=None,
            published_path=None,
            quarantine_path=None,
            partition_count=0,
            parquet_file_count=0,
            inspection=inspection,
        )

    settings.ensure_directories()
    settings.ensure_m2_directories()
    published_fy_dir = (
        settings.partitioned_parquet_dir
        / f"awarding_agency={agency.key}"
        / f"fiscal_year={fiscal_year}"
    )
    manifest_path = settings.manifest_dir / f"{dataset_id}.json"

    # Resume / idempotency short-circuit: a prior *published* run for the same
    # archive checksum is complete; do not reprocess.
    checkpoint = load_checkpoint(dataset_id, inspection.zip_sha256, settings.checkpoints_dir)
    if (
        resume
        and checkpoint is not None
        and checkpoint.status == "published"
        and published_fy_dir.exists()
        and manifest_path.exists()
    ):
        log.info("archive.resume_skip", extra={"context": {"dataset_id": dataset_id}})
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return _result_from_manifest(data, inspection, status="skipped")

    checkpoint = Checkpoint(dataset_id=dataset_id, archive_sha256=inspection.zip_sha256)

    # Fresh staging area for this dataset.
    staging_root = settings.staging_dir / dataset_id
    if staging_root.exists():
        shutil.rmtree(staging_root)
    extract_dir = staging_root / "extracted"
    parquet_stage = staging_root / "parquet"
    _t = time.perf_counter()
    extracted = safe_extract_zip(zip_path, extract_dir, settings)
    if timings is not None:
        timings["extract"] = time.perf_counter() - _t
    csv_paths = sorted(p for p in extracted if p.suffix.lower() == ".csv")
    _t_process = time.perf_counter()

    totals = IngestTotals()
    seen_keys: set[str] = set()
    warnings: list[str] = []
    header: list[str] = []
    writer: PartitionedParquetWriter | None = None
    quarantine_path = settings.quarantine_dir / f"{dataset_id}.csv"
    quarantine: QuarantineWriter | None = None
    csv_records: list[dict] = []

    try:
        for csv_path in csv_paths:
            for h, batch in iter_csv_batches(csv_path, settings.batch_size):
                if not header:
                    header = h
                    verify_contract(header)
                    writer = PartitionedParquetWriter(parquet_stage, header, settings)
                    quarantine = QuarantineWriter(quarantine_path, header)
                elif h != header:
                    raise IngestionError(
                        "Archive members have inconsistent headers (schema drift "
                        "within a single archive is not supported)."
                    )

                totals.source_rows += len(batch)
                valid, quarantined = validate_batch(batch, seen_keys, warnings)
                totals.duplicate_rows += sum(
                    1 for _, rs in quarantined if "duplicate_transaction_key" in rs
                )

                # Route valid rows: scope filter + partition, else quarantine
                # unpartitionable (missing/unparseable action_date) rows.
                per_partition: dict[PartitionKey, list[dict[str, str]]] = {}
                partition_quarantine: list[tuple[dict[str, str], list[str]]] = []
                for row in valid:
                    pkey = partition_key_for_row(row, agency.key)
                    if pkey is None:
                        partition_quarantine.append((row, ["missing_or_unparseable_action_date"]))
                        continue
                    if pkey.fiscal_year != fiscal_year:
                        totals.filtered_rows += 1  # out of requested FY scope
                        continue
                    per_partition.setdefault(pkey, []).append(row)

                for pkey, rows in per_partition.items():
                    writer.write_rows(pkey, rows)
                    totals.partition_rows[pkey] = totals.partition_rows.get(pkey, 0) + len(rows)
                    totals.valid_rows += len(rows)
                    for row in rows:
                        _tally_obligation(row, totals)

                quarantine.write(quarantined)
                quarantine.write(partition_quarantine)
                totals.quarantined_rows += len(quarantined) + len(partition_quarantine)

            csv_records.append(
                {
                    "original_file_name": csv_path.name,
                    "sha256": sha256_file(csv_path),
                    "size_bytes": csv_path.stat().st_size,
                }
            )
            checkpoint.mark_member_done(csv_path.name, totals.source_rows)

        if writer is None:
            raise IngestionError("No CSV data found in archive.")
        totals.distinct_valid_tx = totals.valid_rows  # dedup guarantees 1:1
        partition_stats = writer.close()
        if quarantine is not None:
            quarantine.close()
    finally:
        if writer is not None and writer._writers:
            writer.close()
    if timings is not None:
        timings["process"] = time.perf_counter() - _t_process

    # Reconcile staged candidate (publication gate).
    _t = time.perf_counter()
    reconciliation = reconcile_dataset(totals, parquet_stage)
    if timings is not None:
        timings["reconcile"] = time.perf_counter() - _t
    checkpoint.status = "staged"
    save_checkpoint(checkpoint, settings.checkpoints_dir)

    fingerprint = schema_fingerprint(header)
    signature = _content_signature(totals, fingerprint)
    parquet_file_count = sum(len(s.files) for s in partition_stats)

    # Record this dataset's declared schema in the versioned registry so schema
    # evolution across fiscal years / agencies can be compared later.
    record_schema(dataset_id, agency.key, fiscal_year, header, settings.schemas_dir)

    if not reconciliation.passed:
        checkpoint.status = "failed"
        save_checkpoint(checkpoint, settings.checkpoints_dir)
        log.error("archive.reconciliation_failed", extra={"context": {"dataset_id": dataset_id}})
        manifest = _build_manifest(
            dataset_id,
            agency,
            fiscal_year,
            inspection,
            csv_records,
            header,
            fingerprint,
            totals,
            partition_stats,
            reconciliation,
            signature,
            fy_start,
            fy_end,
            published=False,
            published_path=None,
            status="reconciliation_failed",
            quarantine_path=(quarantine_path if (quarantine and quarantine.count) else None),
        )
        _write_manifest(manifest, manifest_path)
        return ArchiveIngestResult(
            dataset_id=dataset_id,
            agency=agency.key,
            fiscal_year=fiscal_year,
            status="reconciliation_failed",
            totals=totals,
            reconciliation=reconciliation,
            manifest_path=manifest_path,
            published_path=None,
            quarantine_path=(quarantine_path if (quarantine and quarantine.count) else None),
            partition_count=len(partition_stats),
            parquet_file_count=parquet_file_count,
            inspection=inspection,
        )

    # Conflict detection vs an already-published dataset.
    conflict = False
    idempotent = False
    if published_fy_dir.exists() and manifest_path.exists():
        prior = json.loads(manifest_path.read_text(encoding="utf-8"))
        prior_sig = prior.get("content_signature")
        if prior_sig == signature:
            idempotent = True
        elif not force:
            conflict = True

    status = "published"
    published_path: Path | None = None
    if conflict:
        status = "conflict"
        log.warning(
            "archive.publish_conflict",
            extra={
                "context": {
                    "dataset_id": dataset_id,
                    "hint": "content differs from published; use --force (CDC)",
                }
            },
        )
    else:
        _t = time.perf_counter()
        published_path = _publish_atomic(parquet_stage, published_fy_dir, agency.key, fiscal_year)
        if timings is not None:
            timings["publish"] = time.perf_counter() - _t
        status = "published"
        checkpoint.status = "published"
        save_checkpoint(checkpoint, settings.checkpoints_dir)

    manifest = _build_manifest(
        dataset_id,
        agency,
        fiscal_year,
        inspection,
        csv_records,
        header,
        fingerprint,
        totals,
        partition_stats,
        reconciliation,
        signature,
        fy_start,
        fy_end,
        published=(published_path is not None),
        published_path=published_path,
        status=status,
        quarantine_path=(quarantine_path if (quarantine and quarantine.count) else None),
    )
    _write_manifest(manifest, manifest_path)

    # Clean staging on successful publish (keep it on conflict for inspection).
    if status == "published":
        shutil.rmtree(staging_root, ignore_errors=True)

    return ArchiveIngestResult(
        dataset_id=dataset_id,
        agency=agency.key,
        fiscal_year=fiscal_year,
        status=status,
        totals=totals,
        reconciliation=reconciliation,
        manifest_path=manifest_path,
        published_path=published_path,
        quarantine_path=(quarantine_path if (quarantine and quarantine.count) else None),
        partition_count=len(partition_stats),
        parquet_file_count=parquet_file_count,
        conflict=conflict,
        idempotent=idempotent,
        inspection=inspection,
    )


def _publish_atomic(
    parquet_stage: Path, published_fy_dir: Path, agency_key: str, fiscal_year: int
) -> Path:
    """Atomically publish the staged fiscal-year tree into the published lake.

    The staged ``awarding_agency=.../fiscal_year=.../`` subtree is renamed into
    place as the single mutating operation. An existing published tree is removed
    immediately before the rename (so republication replaces, never appends —
    guaranteeing no duplicate published transactions).
    """
    staged_fy_dir = parquet_stage / f"awarding_agency={agency_key}" / f"fiscal_year={fiscal_year}"
    published_fy_dir.parent.mkdir(parents=True, exist_ok=True)
    if published_fy_dir.exists():
        shutil.rmtree(published_fy_dir)
    os.replace(staged_fy_dir, published_fy_dir)
    log.info("archive.published", extra={"context": {"path": str(published_fy_dir)}})
    return published_fy_dir


def _build_manifest(
    dataset_id,
    agency: Agency,
    fiscal_year,
    inspection: ArchiveInspection,
    csv_records,
    header,
    fingerprint,
    totals: IngestTotals,
    partition_stats,
    reconciliation: ReconciliationResult,
    signature,
    fy_start: date,
    fy_end: date,
    *,
    published: bool,
    published_path: Path | None,
    status: str,
    quarantine_path: Path | None,
) -> dict:
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "ingestion_timestamp_utc": datetime.now(UTC).isoformat(),
        "agency": {"key": agency.key, "name": agency.name, "toptier_code": agency.toptier_code},
        "fiscal_year": fiscal_year,
        "scope_filter": {
            "date_type": "action_date",
            "fy_start": fy_start.isoformat(),
            "fy_end": fy_end.isoformat(),
        },
        "source_archive": {
            "original_file_name": inspection.zip_path.name,
            "stored_path": str(inspection.zip_path),
            "sha256": inspection.zip_sha256,
            "size_bytes": inspection.zip_size_bytes,
            "uncompressed_bytes": inspection.total_uncompressed_bytes,
        },
        "source_csv_members": csv_records,
        "schema_fingerprint": fingerprint,
        "column_count": len(header),
        "columns": header,
        "totals": {
            "source_rows": totals.source_rows,
            "valid_rows": totals.valid_rows,
            "quarantined_rows": totals.quarantined_rows,
            "filtered_rows": totals.filtered_rows,
            "duplicate_rows": totals.duplicate_rows,
            "distinct_valid_tx": totals.distinct_valid_tx,
            "net_obligation": str(totals.net_obligation),
            "positive_obligation": str(totals.positive_obligation),
            "negative_obligation": str(totals.negative_obligation),
            "negative_obligation_count": totals.negative_count,
            "zero_obligation_count": totals.zero_count,
        },
        "partitions": [
            {"partition": s.key.relative_path.as_posix(), "rows": s.rows, "files": len(s.files)}
            for s in partition_stats
        ],
        "partition_count": len(partition_stats),
        "parquet_file_count": sum(len(s.files) for s in partition_stats),
        "reconciliation": reconciliation.to_dict(),
        "content_signature": signature,
        "published": published,
        "published_path": str(published_path) if published_path else None,
        "status": status,
        "quarantine_path": str(quarantine_path) if quarantine_path else None,
    }


def _write_manifest(manifest: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    log.info("archive.manifest_written", extra={"context": {"path": str(path)}})


def _result_from_manifest(
    data: dict, inspection: ArchiveInspection, *, status: str
) -> ArchiveIngestResult:
    t = data["totals"]
    totals = IngestTotals(
        source_rows=t["source_rows"],
        valid_rows=t["valid_rows"],
        quarantined_rows=t["quarantined_rows"],
        filtered_rows=t["filtered_rows"],
        duplicate_rows=t["duplicate_rows"],
        net_obligation=Decimal(t["net_obligation"]),
        positive_obligation=Decimal(t["positive_obligation"]),
        negative_obligation=Decimal(t["negative_obligation"]),
        negative_count=t["negative_obligation_count"],
        zero_count=t["zero_obligation_count"],
        distinct_valid_tx=t["distinct_valid_tx"],
    )
    return ArchiveIngestResult(
        dataset_id=data["dataset_id"],
        agency=data["agency"]["key"],
        fiscal_year=data["fiscal_year"],
        status=status,
        totals=totals,
        reconciliation=None,
        manifest_path=None,
        published_path=Path(data["published_path"]) if data.get("published_path") else None,
        quarantine_path=Path(data["quarantine_path"]) if data.get("quarantine_path") else None,
        partition_count=data["partition_count"],
        parquet_file_count=data["parquet_file_count"],
        idempotent=True,
        inspection=inspection,
    )
