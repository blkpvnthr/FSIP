"""Unit tests for the schema registry and comparison/classification."""

from __future__ import annotations

from ingestion.config import Settings
from ingestion.models import REQUIRED_COLUMNS
from ingestion.schema_registry import compare_schemas, load_schema, record_schema

BASE_HEADER = [*REQUIRED_COLUMNS, "total_dollars_obligated", "naics_code"]


def _record(settings: Settings, dataset_id: str, header: list[str]):
    return record_schema(dataset_id, "NASA", 2026, header, settings.schemas_dir)


def test_record_and_load_roundtrip(settings: Settings):
    rec = _record(settings, "nasa_contracts_fy2026", BASE_HEADER)
    loaded = load_schema("nasa_contracts_fy2026", settings.schemas_dir)
    assert loaded.columns == rec.columns
    assert loaded.schema_fingerprint == rec.schema_fingerprint


def test_identical_schemas_compatible(settings: Settings):
    a = _record(settings, "a", BASE_HEADER)
    b = _record(settings, "b", BASE_HEADER)
    diff = compare_schemas(a, b)
    assert diff.identical is True
    assert diff.classification == "compatible"


def test_added_column_is_compatible(settings: Settings):
    a = _record(settings, "a", BASE_HEADER)
    b = _record(settings, "b", [*BASE_HEADER, "new_optional_col"])
    diff = compare_schemas(a, b)
    assert diff.added_columns == ["new_optional_col"]
    assert diff.classification == "compatible"
    assert diff.identical is False


def test_removed_required_column_is_breaking(settings: Settings):
    reduced = [c for c in BASE_HEADER if c != "federal_action_obligation"]
    a = _record(settings, "a", BASE_HEADER)
    b = _record(settings, "b", reduced)
    diff = compare_schemas(a, b)
    assert "federal_action_obligation" in diff.removed_columns
    assert "federal_action_obligation" in diff.missing_required
    assert diff.classification == "breaking"


def test_removed_optional_column_is_potentially_breaking(settings: Settings):
    reduced = [c for c in BASE_HEADER if c != "naics_code"]
    a = _record(settings, "a", BASE_HEADER)
    b = _record(settings, "b", reduced)
    diff = compare_schemas(a, b)
    assert diff.removed_columns == ["naics_code"]
    assert diff.classification == "potentially_breaking"


def test_reorder_detected_but_compatible(settings: Settings):
    reordered = list(reversed(BASE_HEADER))
    a = _record(settings, "a", BASE_HEADER)
    b = _record(settings, "b", reordered)
    diff = compare_schemas(a, b)
    assert diff.reordered is True
    assert diff.classification == "compatible"
