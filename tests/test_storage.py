"""Unit tests for decimal-safe Parquet materialization."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from ingestion.storage import build_arrow_schema, write_parquet
from ingestion.validate import validate_file


def test_schema_types_are_explicit():
    header = [
        "contract_transaction_unique_key",
        "federal_action_obligation",
        "action_date",
        "last_modified_date",
        "recipient_name",
    ]
    schema = build_arrow_schema(header)
    types = {f.name: f.type for f in schema}
    assert pa.types.is_decimal(types["federal_action_obligation"])
    assert pa.types.is_date(types["action_date"])
    assert pa.types.is_timestamp(types["last_modified_date"])
    assert pa.types.is_string(types["recipient_name"])


def test_write_parquet_roundtrip_preserves_decimals_and_negatives(
    sample_csv_path: Path, tmp_path: Path
):
    result = validate_file(sample_csv_path)
    out = tmp_path / "out.parquet"
    n = write_parquet(result.valid_rows, result.header, out)
    assert n == 4

    table = pq.read_table(out)
    # Full source width is preserved (every column kept).
    assert table.num_columns == len(result.header)

    oblig = table.column("federal_action_obligation").to_pylist()
    assert Decimal("-250.25") in oblig  # de-obligation preserved, sign intact
    assert Decimal("0.00") in oblig
    assert all(v is None or isinstance(v, Decimal) for v in oblig)

    # Decimal-exact sum via Parquet matches the validator.
    total = sum((v for v in oblig if v is not None), Decimal("0"))
    assert total == Decimal("1250.25")


def test_unparseable_ancillary_date_becomes_null_not_error(sample_csv_path: Path, tmp_path: Path):
    result = validate_file(sample_csv_path)
    out = tmp_path / "out.parquet"
    write_parquet(result.valid_rows, result.header, out)
    table = pq.read_table(out)
    solic = table.column("solicitation_date").to_pylist()
    assert None in solic  # TX7's "not-a-date" stored as null, row still present


def test_write_is_atomic_no_tmp_left_behind(sample_csv_path: Path, tmp_path: Path):
    result = validate_file(sample_csv_path)
    out = tmp_path / "out.parquet"
    write_parquet(result.valid_rows, result.header, out)
    assert out.exists()
    assert not out.with_suffix(".parquet.tmp").exists()
