"""Schema validation, typing, duplicate detection and quarantine routing.

The validator enforces the data contract declared in :mod:`ingestion.models`
without ever *silently* dropping a record:

* A missing **required column** is a contract breach for the whole file
  (:class:`~ingestion.exceptions.SchemaContractError`) — we refuse to guess.
* A **row** that violates a hard rule (blank/duplicate transaction key, or a
  non-numeric primary obligation) is routed to *quarantine*, preserved verbatim
  with machine-readable reasons, and counted — never discarded.
* **Negative obligations are valid** (de-obligations) and pass through untouched.
* Unparseable *optional* date/decimal fields are a soft issue: the row is kept,
  the offending value is recorded as a warning (and later typed as null), so a
  single bad ancillary field never costs us an otherwise-good financial record.

The validator emits strings only (source fidelity); typing to Arrow happens in
:mod:`ingestion.storage`, which re-uses the same parsing helpers so the quarantine
decision and the stored type can never disagree.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .exceptions import SchemaContractError
from .logging_config import get_logger
from .models import (
    AWARD_KEY,
    DATE_COLUMNS,
    DECIMAL_COLUMNS,
    OBLIGATION_COLUMN,
    REQUIRED_COLUMNS,
    TIMESTAMP_COLUMNS,
    TRANSACTION_KEY,
    schema_fingerprint,
)

log = get_logger(__name__)

# Normalizes a trailing 2-digit UTC offset (e.g. "+00") to "+00:00" so
# datetime.fromisoformat accepts USAspending's ``last_modified_date`` form.
_SHORT_TZ = re.compile(r"([+-]\d{2})$")


# --------------------------------------------------------------------------- #
# Parsing helpers (shared with storage so decisions and types stay consistent)
# --------------------------------------------------------------------------- #
def parse_decimal(raw: str | None) -> Decimal | None:
    """Parse a decimal-safe money value. Blank -> None. Raises on malformed."""
    if raw is None:
        return None
    s = raw.strip()
    if s == "":
        return None
    return Decimal(s)  # exact; raises InvalidOperation on malformed input


def parse_date(raw: str | None) -> date | None:
    """Parse a ``YYYY-MM-DD`` date (tolerating a trailing time). Blank -> None."""
    if raw is None:
        return None
    s = raw.strip()
    if s == "":
        return None
    return date.fromisoformat(s[:10])


def parse_timestamp(raw: str | None) -> datetime | None:
    """Parse a timezone-aware timestamp. Blank -> None."""
    if raw is None:
        return None
    s = raw.strip()
    if s == "":
        return None
    return datetime.fromisoformat(_SHORT_TZ.sub(r"\1:00", s))


@dataclass
class ValidationResult:
    """Outcome of validating one source CSV file."""

    header: list[str]
    schema_fingerprint: str
    valid_rows: list[dict[str, str]] = field(default_factory=list)
    quarantined: list[tuple[dict[str, str], list[str]]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_rows: int = 0

    @property
    def valid_count(self) -> int:
        return len(self.valid_rows)

    @property
    def quarantined_count(self) -> int:
        return len(self.quarantined)

    def obligation_total(self) -> Decimal:
        """Decimal-exact sum of ``federal_action_obligation`` over valid rows."""
        total = Decimal("0")
        for row in self.valid_rows:
            value = parse_decimal(row.get(OBLIGATION_COLUMN))
            if value is not None:
                total += value
        return total

    def negative_obligation_count(self) -> int:
        return sum(
            1
            for row in self.valid_rows
            if (v := parse_decimal(row.get(OBLIGATION_COLUMN))) is not None and v < 0
        )


def verify_contract(header: list[str]) -> None:
    """Raise :class:`SchemaContractError` if any required column is absent."""
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise SchemaContractError(
            f"Source is missing required column(s): {missing}"
        )


def _validate_row(
    row: dict[str, str],
    seen_keys: set[str],
    warnings: list[str],
) -> list[str]:
    """Return a list of hard-failure reasons (empty list == row is valid)."""
    reasons: list[str] = []

    tx_key = (row.get(TRANSACTION_KEY) or "").strip()
    if not tx_key:
        reasons.append("missing_transaction_key")
    elif tx_key in seen_keys:
        reasons.append("duplicate_transaction_key")

    if not (row.get(AWARD_KEY) or "").strip():
        reasons.append("missing_award_key")

    # Primary obligation must be present and numeric. A blank or malformed money
    # value is quarantined (preserved for review), never silently dropped.
    raw_oblig = (row.get(OBLIGATION_COLUMN) or "").strip()
    if raw_oblig == "":
        reasons.append("blank_obligation")
    else:
        try:
            parse_decimal(raw_oblig)  # negatives pass; this only checks parseability
        except InvalidOperation:
            reasons.append("malformed_obligation")

    # Soft checks: bad ancillary dates/decimals do not quarantine the row.
    for col in DATE_COLUMNS:
        if col in row and (row[col] or "").strip():
            try:
                parse_date(row[col])
            except ValueError:
                warnings.append(f"unparseable_date:{col}={row[col]!r}")
    for col in TIMESTAMP_COLUMNS:
        if col in row and (row[col] or "").strip():
            try:
                parse_timestamp(row[col])
            except ValueError:
                warnings.append(f"unparseable_timestamp:{col}={row[col]!r}")
    for col in DECIMAL_COLUMNS:
        if col == OBLIGATION_COLUMN:
            continue
        if col in row and (row[col] or "").strip():
            try:
                parse_decimal(row[col])
            except InvalidOperation:
                warnings.append(f"unparseable_decimal:{col}={row[col]!r}")

    if not reasons and tx_key:
        seen_keys.add(tx_key)
    return reasons


def validate_file(csv_path: Path) -> ValidationResult:
    """Validate a source CSV and partition rows into valid / quarantined.

    Reads the file streaming (one row at a time) so large extracts do not need to
    be held fully in memory for the validation pass.
    """
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = list(reader.fieldnames or [])
        verify_contract(header)

        result = ValidationResult(
            header=header,
            schema_fingerprint=schema_fingerprint(header),
        )
        seen_keys: set[str] = set()
        for row in reader:
            result.total_rows += 1
            reasons = _validate_row(row, seen_keys, result.warnings)
            if reasons:
                result.quarantined.append((row, reasons))
            else:
                result.valid_rows.append(row)

    log.info(
        "validate.done",
        extra={"context": {
            "total": result.total_rows,
            "valid": result.valid_count,
            "quarantined": result.quarantined_count,
            "warnings": len(result.warnings),
        }},
    )
    return result


def write_quarantine(result: ValidationResult, quarantine_path: Path) -> int:
    """Write quarantined rows (verbatim + reasons) to CSV. Returns row count.

    The original columns are preserved exactly and a ``_quarantine_reasons``
    column is appended so the rejection is auditable and reprocessable.
    """
    if not result.quarantined:
        return 0
    quarantine_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [*result.header, "_quarantine_reasons"]
    with open(quarantine_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row, reasons in result.quarantined:
            out = dict(row)
            out["_quarantine_reasons"] = ";".join(reasons)
            writer.writerow(out)
    log.warning(
        "validate.quarantine_written",
        extra={"context": {"path": str(quarantine_path), "rows": len(result.quarantined)}},
    )
    return len(result.quarantined)
