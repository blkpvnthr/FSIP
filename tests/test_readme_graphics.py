"""Tests for the README graphics preservation check.

Guards the Milestone-2 requirement that the README's existing architectural
graphics remain byte-for-byte unchanged.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_readme_graphics.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_readme_graphics", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_baseline_exists_and_covers_protected_graphics():
    mod = _load_module()
    assert mod.BASELINE.exists(), "run scripts/check_readme_graphics.py --record"
    import json

    baseline = json.loads(mod.BASELINE.read_text())
    # The four original graphics must be in the baseline.
    for name in [
        "milestone_1_ingestion_workflow",
        "enterprise_system_architecture",
        "dimensional_warehouse_star_schema",
        "apache_airflow_orchestration",
    ]:
        assert name in baseline


def test_current_readme_preserves_baseline_graphics():
    mod = _load_module()
    # verify() returns 0 when every baseline graphic is byte-for-byte intact.
    assert mod.verify() == 0


def test_detects_a_modified_graphic(tmp_path: Path, monkeypatch):
    mod = _load_module()
    # Point the checker at a tampered copy of the README and confirm it fails.
    tampered = tmp_path / "README.md"
    original = mod.README.read_text()
    tampered.write_text(original.replace("erDiagram", "erDiagram %% tampered"))
    monkeypatch.setattr(mod, "README", tampered)
    assert mod.verify() == 1
