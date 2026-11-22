"""Detect orphaned curated snapshots left by partial publication failures.

A publication writes immutable curated snapshot objects (step 9) *before* it
advances the catalog pointer (step 12). If it fails in between (e.g. an
unrecoverable pointer conflict or a crash), those snapshot objects are written but
never referenced by any catalog version — orphans that occupy storage forever,
since no lifecycle rule targets ``curated/`` (that prefix holds live data) and the
ingestion role is *denied* delete on it.

This module *detects and reports* orphans. It deliberately does NOT delete them:
by IAM design the ingestion role cannot delete ``curated/``/``control/`` objects
(immutability), so reclamation is an explicit **admin** operation. A snapshot is an
orphan only if it is referenced by **no** catalog version — current or any archived
``control/catalogs/v*.json`` — so snapshots reachable via rollback are retained.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from ..logging_config import get_logger
from ..storage_backends.base import StorageBackend
from . import catalog as catalog_mod

log = get_logger(__name__)

_SNAPSHOT_RE = re.compile(r"snapshot_id=([^/]+)/")


@dataclass
class OrphanSnapshot:
    snapshot_id: str
    object_count: int
    bytes: int
    sample_key: str


def _referenced_snapshot_ids(backend: StorageBackend) -> set[str]:
    """Snapshot ids referenced by the current pointer OR any archived catalog."""
    referenced: set[str] = set()
    current, _ = catalog_mod.read_current(backend)
    for ds in current.datasets.values():
        referenced.add(ds.snapshot_id)
    # All archived catalog versions (rollback targets) also pin their snapshots.
    for key in backend.list("control/catalogs/"):
        try:
            cat = catalog_mod.LakeCatalog.from_json(backend.get_bytes(key).decode("utf-8"))
        except Exception:
            continue
        for ds in cat.datasets.values():
            referenced.add(ds.snapshot_id)
    return referenced


def find_orphaned_snapshots(backend: StorageBackend) -> list[OrphanSnapshot]:
    """Return curated snapshots present in storage but referenced by no catalog."""
    referenced = _referenced_snapshot_ids(backend)
    agg: dict[str, OrphanSnapshot] = {}
    for key in backend.list("curated/"):
        m = _SNAPSHOT_RE.search(key)
        if not m:
            continue
        sid = m.group(1)
        if sid in referenced:
            continue
        size = backend.stat(key).size
        if sid not in agg:
            agg[sid] = OrphanSnapshot(sid, 0, 0, key)
        agg[sid].object_count += 1
        agg[sid].bytes += size
    orphans = sorted(agg.values(), key=lambda o: o.snapshot_id)
    log.info("gc.scan", extra={"context": {"orphans": len(orphans)}})
    return orphans


def gc_report(backend: StorageBackend) -> dict:
    """Build a report of orphaned snapshots (read-only; no deletion)."""
    orphans = find_orphaned_snapshots(backend)
    return {
        "orphaned_snapshots": [asdict(o) for o in orphans],
        "orphan_count": len(orphans),
        "reclaimable_bytes": sum(o.bytes for o in orphans),
        "note": (
            "Report only. Reclaiming curated/control objects requires the "
            "infra-admin role (the ingestion role is denied delete there by design). "
            "Run deletion as a separate, approved admin operation."
        ),
    }
