"""Tests for the Milestone-2 CLI surface (backward compatible + new commands)."""

from __future__ import annotations

from pathlib import Path

import pytest
from ingestion.cli import main

pytestmark = pytest.mark.integration


def test_agencies_lists_all_five(capsys):
    assert main(["agencies"]) == 0
    out = capsys.readouterr().out
    for key in ("NASA", "DOD", "DHS", "DOE", "GSA"):
        assert key in out


def test_ingest_archive_then_profile_reconcile(nasa_fy2026_archive: Path, tmp_path: Path, capsys):
    import shutil

    data_dir_path = tmp_path / "data"
    data_dir = str(data_dir_path)
    # Place the archive in the data lake so discovery (list-datasets) can find it.
    archive_dir = data_dir_path / "raw" / "usaspending" / "contracts" / "full" / "nasa" / "fy2026"
    archive_dir.mkdir(parents=True)
    archive = archive_dir / nasa_fy2026_archive.name
    shutil.copy(nasa_fy2026_archive, archive)
    code = main(
        [
            "ingest-archive",
            "--path",
            str(archive),
            "--agency",
            "NASA",
            "--fiscal-year",
            "2026",
            "--data-dir",
            data_dir,
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "PUBLISHED" in out
    assert "valid (published): 4" in out

    assert main(["list-datasets", "--data-dir", data_dir, "--no-checksums"]) == 0
    assert "NASA" in capsys.readouterr().out

    assert (
        main(["profile", "--agency", "NASA", "--fiscal-year", "2026", "--data-dir", data_dir]) == 0
    )
    assert "net obligation     : 250.00" in capsys.readouterr().out

    assert (
        main(["reconcile", "--agency", "NASA", "--fiscal-year", "2026", "--data-dir", data_dir])
        == 0
    )
    assert "PASSED" in capsys.readouterr().out


def test_dry_run_ingest_does_not_publish(nasa_fy2026_archive: Path, tmp_path: Path, capsys):
    data_dir = tmp_path / "data"
    code = main(
        [
            "ingest-archive",
            "--path",
            str(nasa_fy2026_archive),
            "--agency",
            "NASA",
            "--fiscal-year",
            "2026",
            "--data-dir",
            str(data_dir),
            "--dry-run",
        ]
    )
    assert code == 0
    assert "DRY_RUN" in capsys.readouterr().out
    assert not (data_dir / "parquet" / "contracts").exists()


def test_list_datasets_json(nasa_fy2026_archive: Path, tmp_path: Path, capsys):
    import json
    import shutil

    data_dir = tmp_path / "data"
    archive_dir = data_dir / "raw" / "usaspending" / "contracts" / "full" / "nasa" / "fy2026"
    archive_dir.mkdir(parents=True)
    shutil.copy(nasa_fy2026_archive, archive_dir / "a.zip")
    assert main(["list-datasets", "--data-dir", str(data_dir), "--json", "--no-checksums"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(e["agency"] == "NASA" and e["fiscal_year"] == 2026 for e in payload)


def test_unknown_agency_returns_error(tmp_path: Path):
    code = main(
        [
            "ingest-archive",
            "--path",
            str(tmp_path / "x.zip"),
            "--agency",
            "TREASURY",
            "--fiscal-year",
            "2026",
            "--data-dir",
            str(tmp_path / "d"),
        ]
    )
    assert code == 2  # KeyError -> handled error exit
