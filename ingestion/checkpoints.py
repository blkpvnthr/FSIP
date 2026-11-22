"""Durable checkpoints for resumable archive ingestion.

A checkpoint is a small JSON file keyed by dataset id that records how far an
ingestion run progressed (which archive members / batches have been consumed and
whether the dataset was published). It lets ``--resume`` skip already-processed
members after an interruption, and lets the pipeline detect a previously-completed
run for idempotency.

Checkpoints are advisory metadata, not a source of truth for financial data —
reconciliation always re-validates the published dataset independently.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .logging_config import get_logger

log = get_logger(__name__)

CHECKPOINT_SCHEMA_VERSION = "1.0"


@dataclass
class Checkpoint:
    """Progress record for one dataset ingestion."""

    dataset_id: str
    archive_sha256: str
    schema_version: str = CHECKPOINT_SCHEMA_VERSION
    completed_members: list[str] = field(default_factory=list)
    status: str = "in_progress"  # in_progress | staged | published | failed
    rows_processed: int = 0

    def mark_member_done(self, member: str, rows: int) -> None:
        if member not in self.completed_members:
            self.completed_members.append(member)
        self.rows_processed += rows

    def is_member_done(self, member: str) -> bool:
        return member in self.completed_members


def _path(dataset_id: str, checkpoints_dir: Path) -> Path:
    return checkpoints_dir / f"{dataset_id}.json"


def load_checkpoint(
    dataset_id: str, archive_sha256: str, checkpoints_dir: Path
) -> Checkpoint | None:
    """Load a checkpoint for ``dataset_id`` if it matches the archive checksum.

    A checksum mismatch means the source archive changed since the checkpoint was
    written, so the stale checkpoint is ignored (a fresh run is required).
    """
    path = _path(dataset_id, checkpoints_dir)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("archive_sha256") != archive_sha256:
        log.warning(
            "checkpoint.stale_ignored",
            extra={"context": {"dataset_id": dataset_id}},
        )
        return None
    return Checkpoint(
        dataset_id=data["dataset_id"],
        archive_sha256=data["archive_sha256"],
        schema_version=data.get("schema_version", CHECKPOINT_SCHEMA_VERSION),
        completed_members=list(data.get("completed_members", [])),
        status=data.get("status", "in_progress"),
        rows_processed=int(data.get("rows_processed", 0)),
    )


def save_checkpoint(checkpoint: Checkpoint, checkpoints_dir: Path) -> Path:
    """Persist a checkpoint atomically; return its path."""
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    path = _path(checkpoint.dataset_id, checkpoints_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(checkpoint), indent=2), encoding="utf-8")
    tmp.replace(path)
    return path
