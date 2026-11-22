"""Tests for the `fsdw cloud ...` CLI surface and typed exit codes."""

from __future__ import annotations

import pytest
from ingestion.cli import main
from ingestion.cloud_cli import EXIT_CONFIG, EXIT_OK
from ingestion.config import Settings

pytestmark = pytest.mark.integration


def _dd(settings: Settings) -> str:
    return str(settings.data_dir)


def test_cloud_estimate_cost(published_local: Settings, capsys):
    assert main(["cloud", "estimate-cost", "--data-dir", _dd(published_local)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "cost estimate" in out.lower()
    assert "hard spending cap" in out


def test_cloud_inventory(published_local: Settings, capsys):
    assert main(["cloud", "inventory", "--data-dir", _dd(published_local)]) == EXIT_OK
    assert "transactions" in capsys.readouterr().out


def test_cloud_plan_sync_dry_run_writes_nothing(published_local: Settings, capsys):
    code = main(
        [
            "cloud",
            "plan-sync",
            "--agency",
            "NASA",
            "--fiscal-year",
            "2026",
            "--data-dir",
            _dd(published_local),
        ]
    )
    assert code == EXIT_OK
    assert "dry_run" in capsys.readouterr().out
    assert not (published_local.cloud_local_root / "control" / "current.json").exists()


def test_cloud_sync_then_verify_then_audit(published_local: Settings, capsys):
    assert (
        main(
            [
                "cloud",
                "sync",
                "--agency",
                "NASA",
                "--fiscal-year",
                "2026",
                "--data-dir",
                _dd(published_local),
            ]
        )
        == EXIT_OK
    )
    capsys.readouterr()
    assert (
        main(
            [
                "cloud",
                "verify",
                "--agency",
                "NASA",
                "--fiscal-year",
                "2026",
                "--data-dir",
                _dd(published_local),
            ]
        )
        == EXIT_OK
    )
    assert "PASSED" in capsys.readouterr().out
    assert main(["cloud", "audit-integrity", "--data-dir", _dd(published_local)]) == EXIT_OK
    assert "passed" in capsys.readouterr().out.lower()


def test_cloud_list_snapshots(published_local: Settings, capsys):
    main(
        [
            "cloud",
            "sync",
            "--agency",
            "NASA",
            "--fiscal-year",
            "2026",
            "--data-dir",
            _dd(published_local),
        ]
    )
    capsys.readouterr()
    assert main(["cloud", "list-snapshots", "--data-dir", _dd(published_local)]) == EXIT_OK
    assert "catalog_version" in capsys.readouterr().out


def test_cloud_check_s3_without_bucket_is_config_error(published_local: Settings, monkeypatch):
    monkeypatch.setenv("FSDW_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("FSDW_S3_BUCKET", "")  # missing bucket -> config error
    code = main(["cloud", "check", "--data-dir", _dd(published_local)])
    assert code == EXIT_CONFIG


def test_cloud_check_local(published_local: Settings, capsys):
    assert main(["cloud", "check", "--data-dir", _dd(published_local)]) == EXIT_OK
    assert "local backend" in capsys.readouterr().out


def test_cloud_sync_all_and_reconcile(published_local: Settings, capsys):
    assert main(["cloud", "sync", "--all", "--data-dir", _dd(published_local)]) == EXIT_OK
    capsys.readouterr()
    assert main(["cloud", "reconcile", "--data-dir", _dd(published_local)]) == EXIT_OK
    assert "RECONCILED" in capsys.readouterr().out


def test_cloud_benchmark_cli(published_local: Settings, capsys):
    assert (
        main(
            [
                "cloud",
                "benchmark",
                "--agency",
                "NASA",
                "--fiscal-year",
                "2026",
                "--data-dir",
                _dd(published_local),
                "--json",
            ]
        )
        == EXIT_OK
    )
    assert "publication_seconds" in capsys.readouterr().out


def test_cloud_rollback_requires_target(published_local: Settings):
    assert main(["cloud", "rollback", "--data-dir", _dd(published_local)]) == EXIT_CONFIG


def test_cloud_rollback_by_version(published_local: Settings, make_archive, capsys):
    main(
        [
            "cloud",
            "sync",
            "--agency",
            "NASA",
            "--fiscal-year",
            "2026",
            "--data-dir",
            _dd(published_local),
        ]
    )
    # Publish a disjoint FY2025 to reach catalog v2, then roll back to v1.
    from tests.test_cloud_integrity import _ingest_fy2025

    _ingest_fy2025(published_local, make_archive)
    main(
        [
            "cloud",
            "sync",
            "--agency",
            "NASA",
            "--fiscal-year",
            "2025",
            "--data-dir",
            _dd(published_local),
        ]
    )
    capsys.readouterr()
    assert (
        main(["cloud", "rollback", "--to-version", "1", "--data-dir", _dd(published_local)])
        == EXIT_OK
    )
    assert "Rolled back" in capsys.readouterr().out
