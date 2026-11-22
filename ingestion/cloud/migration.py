"""Migration orchestration: replicate the validated local lake into the cloud.

Migrates existing, already-validated local datasets (no re-download, no changed
financial totals). The order runs the NASA FY2026 pilot first, then the remaining
NASA years, then DOE, then GSA. Each step is idempotent — re-running a migration
with identical content is a no-op (see :func:`publication.publish_local_dataset`).
"""

from __future__ import annotations

from ..config import Settings
from ..logging_config import get_logger
from ..storage_backends import get_storage_backend
from ..storage_backends.base import StorageBackend
from .publication import PublicationResult, publish_local_dataset

log = get_logger(__name__)

#: Migration sequence (pilot first), per the milestone plan.
MIGRATION_ORDER: list[tuple[str, int]] = [
    ("NASA", 2026),  # pilot
    ("NASA", 2022),
    ("NASA", 2023),
    ("NASA", 2024),
    ("NASA", 2025),
    ("DOE", 2026),
    ("GSA", 2026),
]


def migrate_dataset(
    settings: Settings,
    agency: str,
    fiscal_year: int,
    *,
    backend: StorageBackend | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> PublicationResult:
    return publish_local_dataset(
        settings,
        agency,
        fiscal_year,
        backend=backend or get_storage_backend(settings),
        dry_run=dry_run,
        force=force,
    )


def migrate_lake(
    settings: Settings,
    *,
    datasets: list[tuple[str, int]] | None = None,
    backend: StorageBackend | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> list[PublicationResult]:
    """Migrate datasets in the planned order; return each result."""
    backend = backend or get_storage_backend(settings)
    plan = datasets or MIGRATION_ORDER
    results: list[PublicationResult] = []
    for agency, fy in plan:
        # Skip datasets not present in the local lake (a full --all run on an
        # incomplete lake should not fail — it migrates what exists).
        dataset_id = f"{agency.lower()}_contracts_fy{fy}"
        if not (settings.manifest_dir / f"{dataset_id}.json").exists():
            log.info("migration.skip_absent", extra={"context": {"dataset_id": dataset_id}})
            continue
        r = migrate_dataset(settings, agency, fy, backend=backend, dry_run=dry_run, force=force)
        results.append(r)
        log.info(
            "migration.step",
            extra={"context": {"agency": agency, "fiscal_year": fy, "status": r.status}},
        )
    return results
