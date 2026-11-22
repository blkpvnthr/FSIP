"""The published lake catalog and its optimistic-concurrency pointer.

Published analytics NEVER globs ``curated/``; it resolves the **active** snapshot
of each dataset through the catalog at ``control/current.json``. Each publication
writes an immutable, versioned catalog under ``control/catalogs/v<N>.json`` and
then advances ``current.json`` with a **conditional (compare-and-swap) write**
keyed on the previous pointer's etag, so two concurrent publications can never
silently overwrite one another — the loser observes a precondition failure,
reloads, re-validates, and retries.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from ..exceptions import PreconditionFailedError
from ..logging_config import get_logger
from ..storage_backends.base import ABSENT, StorageBackend
from . import keys

log = get_logger(__name__)


@dataclass
class DatasetEntry:
    """One active dataset snapshot referenced by the catalog."""

    dataset_id: str
    agency: str
    fiscal_year: int
    snapshot_id: str
    curated_prefix: str
    part_keys: list[str]  # exact active snapshot part-file keys (no globbing)
    transaction_count: int
    distinct_keys: int
    net_obligation: str  # decimal string, exact
    schema_fingerprint: str
    published_at: str


@dataclass
class LakeCatalog:
    """Immutable description of the active published lake at a point in time."""

    version: int = 0
    created_at: str = ""
    datasets: dict[str, DatasetEntry] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "created_at": self.created_at,
                "datasets": {k: asdict(v) for k, v in self.datasets.items()},
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> LakeCatalog:
        data = json.loads(text)
        return cls(
            version=data["version"],
            created_at=data.get("created_at", ""),
            datasets={k: DatasetEntry(**v) for k, v in data.get("datasets", {}).items()},
        )

    def with_dataset(self, entry: DatasetEntry) -> LakeCatalog:
        """Return a new catalog (version+1) with ``entry`` added/replaced."""
        datasets = dict(self.datasets)
        datasets[entry.dataset_id] = entry
        return LakeCatalog(
            version=self.version + 1,
            created_at=_now(),
            datasets=datasets,
        )

    def total_transactions(self) -> int:
        return sum(d.transaction_count for d in self.datasets.values())


def _now() -> str:
    return datetime.now(UTC).isoformat()


def read_current(backend: StorageBackend) -> tuple[LakeCatalog, str | None]:
    """Return ``(catalog, etag)``. An absent pointer yields an empty version-0 catalog."""
    if not backend.exists(keys.CURRENT_CATALOG_KEY):
        return LakeCatalog(version=0, created_at=_now()), None
    etag = backend.stat(keys.CURRENT_CATALOG_KEY).etag
    catalog = LakeCatalog.from_json(backend.get_bytes(keys.CURRENT_CATALOG_KEY).decode("utf-8"))
    return catalog, etag


def publish_catalog(
    backend: StorageBackend, catalog: LakeCatalog, expected_etag: str | None
) -> str:
    """Archive ``catalog`` immutably and advance the pointer via compare-and-swap.

    Args:
        expected_etag: the etag read with :func:`read_current` (``None`` if the
            pointer did not exist). The pointer is written only if it still matches.

    Returns:
        The new pointer etag.

    Raises:
        PreconditionFailedError: another publication advanced the pointer first.
    """
    # Immutable versioned archive (create-only; a version number is never reused).
    backend.put_bytes(
        keys.catalog_version_key(catalog.version),
        catalog.to_json().encode("utf-8"),
        content_type="application/json",
        precondition=ABSENT,
    )
    # Compare-and-swap the pointer.
    precondition = ABSENT if expected_etag is None else expected_etag
    meta = backend.put_bytes(
        keys.CURRENT_CATALOG_KEY,
        catalog.to_json().encode("utf-8"),
        content_type="application/json",
        precondition=precondition,
    )
    log.info(
        "catalog.published",
        extra={"context": {"version": catalog.version, "datasets": len(catalog.datasets)}},
    )
    return meta.etag


def rollback_to(backend: StorageBackend, target_version: int) -> LakeCatalog:
    """Roll the active pointer back to a previously published catalog version.

    Writes a NEW catalog (version+1) whose dataset set equals the target version's,
    so history is append-only and the rollback itself is auditable. Immutable
    snapshots referenced by the target are retained, so the rollback is safe.
    """
    archived = backend.get_bytes(keys.catalog_version_key(target_version)).decode("utf-8")
    target = LakeCatalog.from_json(archived)
    current, etag = read_current(backend)
    restored = LakeCatalog(
        version=current.version + 1,
        created_at=_now(),
        datasets=dict(target.datasets),
    )
    publish_catalog(backend, restored, etag)
    log.warning(
        "catalog.rolled_back",
        extra={"context": {"to_version": target_version, "new_version": restored.version}},
    )
    return restored


def retry_on_pointer_conflict(fn, *, attempts: int = 5):
    """Run ``fn`` (which performs a catalog CAS); on a pointer conflict, retry.

    ``fn`` must re-read the current catalog and re-validate integrity each call.
    """
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return fn()
        except PreconditionFailedError as exc:
            last = exc
            log.warning("catalog.pointer_conflict_retry", extra={"context": {}})
    raise PreconditionFailedError(
        f"Catalog pointer still conflicting after {attempts} attempts"
    ) from last
