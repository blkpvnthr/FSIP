"""Parquet materialization via PyArrow.

Converts validated string rows into a typed Arrow table and writes Parquet. The
guiding rule is *source fidelity with explicit, decimal-safe types*:

* Money columns become ``decimal128(23, 2)`` — never floats — so cents are exact
  and negative obligations (de-obligations) are preserved bit-for-bit.
* Date columns become ``date32``; ``last_modified_date`` becomes a
  timezone-aware ``timestamp[us, UTC]``.
* **Every other column is preserved verbatim as UTF-8 string.** The full 297-column
  source layout survives; we never drop columns we do not explicitly understand.

Soft parse failures on ancillary fields (already surfaced as warnings in
:mod:`ingestion.validate`) become nulls here rather than hard errors, consistent
with the validator's decision to keep the row.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .logging_config import get_logger
from .models import (
    DATE_COLUMNS,
    DECIMAL_COLUMNS,
    DECIMAL_PRECISION,
    DECIMAL_SCALE,
    TIMESTAMP_COLUMNS,
)
from .validate import parse_date, parse_decimal, parse_timestamp

log = get_logger(__name__)

_DECIMAL_TYPE = pa.decimal128(DECIMAL_PRECISION, DECIMAL_SCALE)
_DATE_TYPE = pa.date32()
_TIMESTAMP_TYPE = pa.timestamp("us", tz="UTC")
_QUANTIZE = Decimal(1).scaleb(-DECIMAL_SCALE)  # Decimal("0.01")


def build_arrow_schema(header: list[str]) -> pa.Schema:
    """Build an explicit Arrow schema for the given (ordered) source header."""
    fields: list[pa.Field] = []
    for col in header:
        if col in DECIMAL_COLUMNS:
            fields.append(pa.field(col, _DECIMAL_TYPE))
        elif col in DATE_COLUMNS:
            fields.append(pa.field(col, _DATE_TYPE))
        elif col in TIMESTAMP_COLUMNS:
            fields.append(pa.field(col, _TIMESTAMP_TYPE))
        else:
            fields.append(pa.field(col, pa.string()))
    return pa.schema(fields)


def _decimal_column(rows: list[dict[str, str]], col: str) -> list[Decimal | None]:
    values: list[Decimal | None] = []
    for row in rows:
        try:
            value = parse_decimal(row.get(col))
        except InvalidOperation:
            # Ancillary money field was flagged soft-invalid by the validator;
            # store null rather than fail (primary obligation is already guarded).
            value = None
        values.append(value.quantize(_QUANTIZE) if value is not None else None)
    return values


def _date_column(rows: list[dict[str, str]], col: str):
    out = []
    for row in rows:
        try:
            out.append(parse_date(row.get(col)))
        except ValueError:
            out.append(None)
    return out


def _timestamp_column(rows: list[dict[str, str]], col: str):
    out = []
    for row in rows:
        try:
            out.append(parse_timestamp(row.get(col)))
        except ValueError:
            out.append(None)
    return out


def rows_to_table(rows: list[dict[str, str]], header: list[str]) -> pa.Table:
    """Convert validated string rows into a typed Arrow table."""
    schema = build_arrow_schema(header)
    arrays: list[pa.Array] = []
    for col in header:
        if col in DECIMAL_COLUMNS:
            arrays.append(pa.array(_decimal_column(rows, col), type=_DECIMAL_TYPE))
        elif col in DATE_COLUMNS:
            arrays.append(pa.array(_date_column(rows, col), type=_DATE_TYPE))
        elif col in TIMESTAMP_COLUMNS:
            arrays.append(pa.array(_timestamp_column(rows, col), type=_TIMESTAMP_TYPE))
        else:
            arrays.append(pa.array([row.get(col) for row in rows], type=pa.string()))
    return pa.Table.from_arrays(arrays, schema=schema)


def write_parquet(rows: list[dict[str, str]], header: list[str], dest_path: Path) -> int:
    """Materialize validated rows to Parquet. Returns the row count written.

    The write is atomic: rows are written to a ``*.tmp`` sibling and then renamed
    into place, so a crashed run never leaves a half-written Parquet behind and a
    re-run deterministically replaces the prior output (idempotent).
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    table = rows_to_table(rows, header)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    pq.write_table(table, tmp_path, compression="zstd")
    tmp_path.replace(dest_path)
    log.info(
        "storage.parquet_written",
        extra={
            "context": {
                "path": str(dest_path),
                "rows": table.num_rows,
                "columns": table.num_columns,
            }
        },
    )
    return table.num_rows
