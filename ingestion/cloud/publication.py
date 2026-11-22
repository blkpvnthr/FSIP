"""Manifest-based cloud publication protocol.

S3 has no atomic directory rename, so publication never copies over a live curated
prefix. Instead a dataset is published into a **new immutable snapshot prefix**, an
immutable candidate manifest records it, and the active ``current.json`` catalog
pointer is advanced with a **conditional (compare-and-swap) write**. If anything
fails before the pointer moves, the previous catalog stays active, published
readers see only the old snapshots, and partial candidate objects remain invisible.

This module migrates an already-validated LOCAL dataset (its curated Parquet +
manifest) into the cloud lake. It re-verifies integrity independently at every step
— object checksums, decimal-exact financial reconciliation, and dataset-wide
transaction uniqueness — and never relaxes those checks, even under ``force``
(force only authorizes replacing a dataset's own prior snapshot).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from ..config import Settings
from ..exceptions import CloudPublicationError, IntegrityError, PreconditionFailedError
from ..logging_config import get_logger
from ..storage_backends import get_storage_backend
from ..storage_backends.base import ABSENT, StorageBackend
from . import catalog as catalog_mod
from . import keys, verification

log = get_logger(__name__)


@dataclass
class PublicationResult:
    dataset_id: str
    agency: str
    fiscal_year: int
    run_id: str
    snapshot_id: str
    status: str  # published | dry_run | reconciliation_failed | integrity_conflict | skipped
    raw_key: str | None = None
    curated_prefix: str | None = None
    part_keys: list[str] = field(default_factory=list)
    catalog_version: int | None = None
    transaction_count: int = 0
    net_obligation: str = "0"
    object_count: int = 0
    bytes_uploaded: int = 0
    reconciliation: dict = field(default_factory=dict)
    idempotent: bool = False
    plan: dict = field(default_factory=dict)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _same_content(existing, totals: dict, manifest: dict) -> bool:
    """True iff an active catalog entry already holds this dataset's exact content."""
    return (
        existing.transaction_count == totals["valid_rows"]
        and existing.net_obligation == totals["net_obligation"]
        and existing.schema_fingerprint == manifest["schema_fingerprint"]
    )


def _local_manifest(settings: Settings, agency: str, fiscal_year: int) -> dict:
    dataset_id = keys.dataset_id(agency, fiscal_year)
    path = settings.manifest_dir / f"{dataset_id}.json"
    if not path.exists():
        raise CloudPublicationError(f"No local manifest for {dataset_id} at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _published_parts(settings: Settings, agency: str, fiscal_year: int) -> list[Path]:
    base = (
        settings.partitioned_parquet_dir
        / f"awarding_agency={agency.upper()}"
        / f"fiscal_year={fiscal_year}"
    )
    if not base.exists():
        raise CloudPublicationError(f"No published parquet for {agency} FY{fiscal_year}")
    return sorted(base.glob("fiscal_month=*/*.parquet"))


def _fiscal_month_of(part: Path) -> int:
    # .../fiscal_month=NN/part-*.parquet
    return int(part.parent.name.split("=", 1)[1])


def _sha_size(path: Path) -> tuple[str, int]:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest(), path.stat().st_size


def publish_local_dataset(
    settings: Settings,
    agency: str,
    fiscal_year: int,
    *,
    backend: StorageBackend | None = None,
    run_id: str | None = None,
    snapshot_id: str | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> PublicationResult:
    """Publish a local dataset to the cloud lake via the manifest protocol."""
    backend = backend or get_storage_backend(settings)
    manifest = _local_manifest(settings, agency, fiscal_year)
    agency = manifest["agency"]["key"]
    dataset_id = manifest["dataset_id"]
    totals = manifest["totals"]
    archive = manifest["source_archive"]
    parts = _published_parts(settings, agency, fiscal_year)
    run_id = run_id or uuid.uuid4().hex
    snapshot_id = snapshot_id or uuid.uuid4().hex

    result = PublicationResult(
        dataset_id=dataset_id,
        agency=agency,
        fiscal_year=fiscal_year,
        run_id=run_id,
        snapshot_id=snapshot_id,
        status="pending",
        transaction_count=totals["valid_rows"],
        net_obligation=totals["net_obligation"],
    )

    # ---- Dry run: plan only, write nothing ----
    if dry_run:
        plan_bytes = (archive.get("size_bytes", 0) or 0) + sum(p.stat().st_size for p in parts)
        result.status = "dry_run"
        result.plan = {
            "raw_key": keys.raw_key(agency, fiscal_year, archive["sha256"]),
            "parquet_part_count": len(parts),
            "object_count": 1 + len(parts) + 3,  # raw + parts + manifest + snapshot + catalog
            "bytes_to_upload": plan_bytes,
        }
        log.info("publication.dry_run", extra={"context": {"dataset_id": dataset_id}})
        return result

    # ---- Idempotency: an already-active snapshot with identical content is a no-op ----
    current, etag = catalog_mod.read_current(backend)
    existing = current.datasets.get(dataset_id)
    if existing is not None and not force and _same_content(existing, totals, manifest):
        result.status = "skipped"
        result.idempotent = True
        result.curated_prefix = existing.curated_prefix
        result.part_keys = existing.part_keys
        result.snapshot_id = existing.snapshot_id
        result.catalog_version = current.version
        log.info("publication.idempotent_skip", extra={"context": {"dataset_id": dataset_id}})
        return result

    # ---- Step 2: immutable, content-addressed raw archive ----
    raw_key = keys.raw_key(agency, fiscal_year, archive["sha256"])
    if not backend.exists(raw_key):
        backend.upload_file(Path(archive["stored_path"]), raw_key)
    if backend.sha256(raw_key) != archive["sha256"]:
        raise CloudPublicationError(f"Raw archive checksum mismatch after upload: {raw_key}")
    result.raw_key = raw_key
    result.bytes_uploaded += archive.get("size_bytes", 0) or 0

    # ---- Step 4: upload candidate Parquet into run-isolated staging ----
    staged: list[tuple[str, str, int]] = []  # (key, sha, size)
    staged_keys: list[str] = []
    for part in parts:
        fm = _fiscal_month_of(part)
        skey = keys.staging_part_key(run_id, agency, fiscal_year, fm, part.name)
        backend.upload_file(part, skey)
        sha, size = _sha_size(part)
        staged.append((skey, sha, size))
        staged_keys.append(skey)
        result.bytes_uploaded += size

    # ---- Step 5: verify staged objects (existence, size, checksum) ----
    checks = verification.verify_objects(backend, staged)
    bad = [c for c in checks if not c.ok]
    if bad:
        raise CloudPublicationError(f"Staged object verification failed: {bad[:3]}")

    # ---- Step 6: reconcile staged vs local manifest (decimal-exact) ----
    rb = verification.readback_totals(backend, settings, staged_keys)
    recon = _reconcile(totals, manifest, rb)
    result.reconciliation = recon
    if not recon["passed"]:
        result.status = "reconciliation_failed"
        _write_json(backend, keys.reconciliation_key(agency, fiscal_year, snapshot_id), recon)
        log.error(
            "publication.reconciliation_failed", extra={"context": {"dataset_id": dataset_id}}
        )
        return result

    # ---- Step 7: dataset-wide transaction uniqueness vs OTHER active datasets ----
    other_keys = [
        k for did, ds in current.datasets.items() if did != dataset_id for k in ds.part_keys
    ]
    overlap = verification.count_key_overlap(backend, settings, staged_keys, other_keys)
    if overlap > 0:
        # Force NEVER bypasses dataset-wide uniqueness.
        result.status = "integrity_conflict"
        result.reconciliation["cross_dataset_overlap"] = overlap
        log.error(
            "publication.integrity_conflict",
            extra={"context": {"dataset_id": dataset_id, "overlap": overlap}},
        )
        return result

    # Same-dataset replacement with DIFFERING content requires --force (identical
    # content was already handled by the idempotent early-skip above).
    if existing is not None and not force:
        result.status = "conflict"
        log.warning("publication.dataset_conflict", extra={"context": {"dataset_id": dataset_id}})
        return result

    # ---- Step 8: immutable candidate manifest ----
    candidate = _candidate_manifest(
        dataset_id,
        agency,
        fiscal_year,
        run_id,
        snapshot_id,
        raw_key,
        archive,
        totals,
        manifest["schema_fingerprint"],
        staged,
        recon,
    )
    _write_json(
        backend,
        keys.candidate_manifest_key(agency, fiscal_year, snapshot_id),
        candidate,
        precondition=ABSENT,
    )

    # ---- Step 9: publish into immutable curated snapshot prefix ----
    curated_keys: list[str] = []
    for (skey, _sha, _size), part in zip(staged, parts, strict=True):
        fm = _fiscal_month_of(part)
        ckey = keys.curated_part_key(agency, fiscal_year, snapshot_id, fm, part.name)
        backend.copy(skey, ckey, precondition=ABSENT)
        curated_keys.append(ckey)

    # ---- Step 10: verify curated objects ----
    curated_expected = [
        (ckey, sha, size) for (_s, sha, size), ckey in zip(staged, curated_keys, strict=True)
    ]
    cbad = [c for c in verification.verify_objects(backend, curated_expected) if not c.ok]
    if cbad:
        raise CloudPublicationError(f"Curated snapshot verification failed: {cbad[:3]}")

    result.curated_prefix = keys.curated_prefix(agency, fiscal_year, snapshot_id)
    result.part_keys = curated_keys
    result.object_count = 1 + len(curated_keys) + 2

    # ---- Steps 11-12: build new catalog + CAS pointer (retry on conflict) ----
    entry = catalog_mod.DatasetEntry(
        dataset_id=dataset_id,
        agency=agency,
        fiscal_year=fiscal_year,
        snapshot_id=snapshot_id,
        curated_prefix=result.curated_prefix,
        part_keys=curated_keys,
        transaction_count=totals["valid_rows"],
        distinct_keys=totals["distinct_valid_tx"],
        net_obligation=totals["net_obligation"],
        schema_fingerprint=manifest["schema_fingerprint"],
        published_at=_now(),
    )

    def _advance() -> int:
        cur, cur_etag = catalog_mod.read_current(backend)
        # Re-validate uniqueness against the freshly-read catalog before committing.
        other = [k for did, ds in cur.datasets.items() if did != dataset_id for k in ds.part_keys]
        if verification.count_key_overlap(backend, settings, curated_keys, other) > 0:
            raise IntegrityError(f"Cross-dataset overlap detected on publish for {dataset_id}")
        new_catalog = cur.with_dataset(entry)
        catalog_mod.publish_catalog(backend, new_catalog, cur_etag)
        return new_catalog.version

    try:
        version = catalog_mod.retry_on_pointer_conflict(_advance)
    except PreconditionFailedError:
        result.status = "pointer_conflict"
        return result
    result.catalog_version = version

    # ---- Step 13: record snapshot + reconciliation ----
    _write_json(
        backend,
        keys.snapshot_record_key(snapshot_id),
        {
            "snapshot_id": snapshot_id,
            "dataset_id": dataset_id,
            "run_id": run_id,
            "catalog_version": version,
            "published_at": _now(),
            "curated_prefix": result.curated_prefix,
            "part_keys": curated_keys,
        },
    )
    _write_json(backend, keys.reconciliation_key(agency, fiscal_year, snapshot_id), recon)

    # ---- Step 14: clean eligible staging artifacts ----
    for skey in staged_keys:
        backend.delete(skey)

    result.status = "published"
    log.info(
        "publication.published",
        extra={
            "context": {
                "dataset_id": dataset_id,
                "snapshot_id": snapshot_id,
                "catalog_version": version,
                "rows": totals["valid_rows"],
            }
        },
    )
    return result


def _reconcile(totals: dict, manifest: dict, rb: verification.ContentTotals) -> dict:
    """Decimal-exact comparison of local manifest totals vs independent readback."""
    expected_parts = {
        f"{p['partition'].split('/')[0].split('=')[1]}/"
        f"{p['partition'].split('/')[1].split('=')[1]}/"
        f"{p['partition'].split('/')[2].split('=')[1]}": p["rows"]
        for p in manifest.get("partitions", [])
    }
    checks = {
        "transaction_count": (totals["valid_rows"], rb.transaction_count),
        "distinct_keys": (totals["distinct_valid_tx"], rb.distinct_keys),
        "net_obligation": (str(Decimal(totals["net_obligation"])), str(rb.net_obligation)),
        "partition_counts": (expected_parts, rb.partition_counts),
    }
    passed = all(a == b for a, b in checks.values())
    return {
        "passed": passed,
        "checks": {k: {"expected": a, "actual": b, "ok": a == b} for k, (a, b) in checks.items()},
    }


def _candidate_manifest(
    dataset_id,
    agency,
    fiscal_year,
    run_id,
    snapshot_id,
    raw_key,
    archive,
    totals,
    fingerprint,
    staged,
    recon,
) -> dict:
    return {
        "manifest_schema_version": "3.0",
        "dataset_id": dataset_id,
        "agency": agency,
        "fiscal_year": fiscal_year,
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "created_at": _now(),
        "raw_key": raw_key,
        "raw_sha256": archive["sha256"],
        "schema_fingerprint": fingerprint,
        "totals": totals,
        "objects": [{"key": k, "sha256": s, "size": sz} for k, s, sz in staged],
        "reconciliation": recon,
    }


def _write_json(
    backend: StorageBackend, key: str, obj: dict, *, precondition: str | None = None
) -> None:
    backend.put_bytes(
        key,
        json.dumps(obj, indent=2, default=str).encode("utf-8"),
        content_type="application/json",
        precondition=precondition,
    )
