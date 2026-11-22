"""Versioned schema registry and historical schema comparison.

Each ingested dataset records its *declared* schema — the ordered column list, a
SHA-256 fingerprint, and the Arrow type the pipeline assigns each column — under
``data/schemas/``. Comparing two recorded schemas classifies the delta so schema
evolution across fiscal years (or agencies) is explicit rather than silent.

Classification
--------------
* **compatible** — only additive or positional changes (added columns, reorder).
* **potentially_breaking** — a *non-required* column was removed or changed type.
* **breaking** — a *required* contract column was removed or changed type.

The registry records the declared schema (names + assigned types). Value-level
type inference from sampled source data is noted as a future enhancement; this
module never coerces or truncates values — it only describes and compares.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .logging_config import get_logger
from .models import REQUIRED_COLUMNS, schema_fingerprint
from .storage import build_arrow_schema

log = get_logger(__name__)

SCHEMA_REGISTRY_VERSION = "1.0"

# Severity ordering for aggregating the overall classification.
_SEVERITY = {"compatible": 0, "potentially_breaking": 1, "breaking": 2}


@dataclass
class RecordedSchema:
    dataset_id: str
    agency: str
    fiscal_year: int
    schema_fingerprint: str
    columns: list[str]
    column_types: dict[str, str]
    recorded_at: str = ""
    registry_version: str = SCHEMA_REGISTRY_VERSION


def declared_column_types(header: list[str]) -> dict[str, str]:
    """Return each column's assigned Arrow type as a string (declared schema)."""
    schema = build_arrow_schema(header)
    return {field.name: str(field.type) for field in schema}


def record_schema(
    dataset_id: str,
    agency: str,
    fiscal_year: int,
    header: list[str],
    schemas_dir: Path,
) -> RecordedSchema:
    """Record a dataset's declared schema to the registry; return it."""
    recorded = RecordedSchema(
        dataset_id=dataset_id,
        agency=agency,
        fiscal_year=fiscal_year,
        schema_fingerprint=schema_fingerprint(header),
        columns=list(header),
        column_types=declared_column_types(header),
        recorded_at=datetime.now(UTC).isoformat(),
    )
    schemas_dir.mkdir(parents=True, exist_ok=True)
    path = schemas_dir / f"{dataset_id}.json"
    path.write_text(json.dumps(asdict(recorded), indent=2), encoding="utf-8")
    log.info(
        "schema.recorded",
        extra={"context": {"dataset_id": dataset_id, "fingerprint": recorded.schema_fingerprint}},
    )
    return recorded


def load_schema(dataset_id: str, schemas_dir: Path) -> RecordedSchema:
    path = schemas_dir / f"{dataset_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No recorded schema for {dataset_id!r} at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return RecordedSchema(**data)


@dataclass
class SchemaDiff:
    from_dataset: str
    to_dataset: str
    identical: bool
    added_columns: list[str] = field(default_factory=list)
    removed_columns: list[str] = field(default_factory=list)
    reordered: bool = False
    type_changes: list[dict[str, str]] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    classification: str = "compatible"

    def to_dict(self) -> dict:
        return asdict(self)


def compare_schemas(a: RecordedSchema, b: RecordedSchema) -> SchemaDiff:
    """Compare schema ``a`` (from) to ``b`` (to) and classify the delta."""
    a_cols, b_cols = set(a.columns), set(b.columns)
    added = sorted(b_cols - a_cols)
    removed = sorted(a_cols - b_cols)

    common = [c for c in a.columns if c in b_cols]
    # Reorder: same common columns appear in a different relative order.
    a_common_order = [c for c in a.columns if c in b_cols]
    b_common_order = [c for c in b.columns if c in a_cols]
    reordered = a_common_order != b_common_order

    type_changes = [
        {
            "column": c,
            "from_type": a.column_types.get(c, "?"),
            "to_type": b.column_types.get(c, "?"),
        }
        for c in common
        if a.column_types.get(c) != b.column_types.get(c)
    ]

    required = set(REQUIRED_COLUMNS)
    missing_required = sorted(required - b_cols)

    severity = "compatible"

    def bump(level: str) -> None:
        nonlocal severity
        if _SEVERITY[level] > _SEVERITY[severity]:
            severity = level

    for col in removed:
        bump("breaking" if col in required else "potentially_breaking")
    for change in type_changes:
        bump("breaking" if change["column"] in required else "potentially_breaking")
    if missing_required:
        bump("breaking")

    identical = (
        a.schema_fingerprint == b.schema_fingerprint
        and not added
        and not removed
        and not type_changes
    )

    diff = SchemaDiff(
        from_dataset=a.dataset_id,
        to_dataset=b.dataset_id,
        identical=identical,
        added_columns=added,
        removed_columns=removed,
        reordered=reordered,
        type_changes=type_changes,
        missing_required=missing_required,
        classification=severity,
    )
    log.info(
        "schema.compared",
        extra={
            "context": {
                "from": a.dataset_id,
                "to": b.dataset_id,
                "classification": severity,
                "identical": identical,
            }
        },
    )
    return diff
