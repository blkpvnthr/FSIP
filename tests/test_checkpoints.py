"""Unit tests for durable ingestion checkpoints."""

from __future__ import annotations

from ingestion.checkpoints import Checkpoint, load_checkpoint, save_checkpoint
from ingestion.config import Settings


def test_save_and_load_roundtrip(settings: Settings):
    cp = Checkpoint(dataset_id="nasa_contracts_fy2026", archive_sha256="abc123")
    cp.mark_member_done("file1.csv", 100)
    cp.status = "published"
    save_checkpoint(cp, settings.checkpoints_dir)

    loaded = load_checkpoint("nasa_contracts_fy2026", "abc123", settings.checkpoints_dir)
    assert loaded is not None
    assert loaded.status == "published"
    assert loaded.rows_processed == 100
    assert loaded.is_member_done("file1.csv")


def test_stale_checkpoint_ignored_on_sha_mismatch(settings: Settings):
    cp = Checkpoint(dataset_id="d", archive_sha256="old")
    save_checkpoint(cp, settings.checkpoints_dir)
    # A different archive checksum means the checkpoint is stale.
    assert load_checkpoint("d", "new", settings.checkpoints_dir) is None


def test_missing_checkpoint_returns_none(settings: Settings):
    assert load_checkpoint("nope", "x", settings.checkpoints_dir) is None
