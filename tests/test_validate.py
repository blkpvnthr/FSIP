"""Unit tests for validation, quarantine and decimal/date parsing."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from ingestion.exceptions import SchemaContractError
from ingestion.validate import (
    parse_date,
    parse_decimal,
    parse_timestamp,
    validate_file,
    verify_contract,
    write_quarantine,
)


def test_verify_contract_rejects_missing_required_columns():
    with pytest.raises(SchemaContractError) as exc:
        verify_contract(["contract_transaction_unique_key"])  # missing the rest
    assert "missing required column" in str(exc.value).lower()


def test_parse_decimal_is_exact_and_preserves_sign():
    assert parse_decimal("1000.50") == Decimal("1000.50")
    assert parse_decimal("-250.25") == Decimal("-250.25")
    assert parse_decimal("0.00") == Decimal("0.00")
    assert parse_decimal("") is None
    assert parse_decimal(None) is None


def test_parse_date_and_timestamp():
    assert parse_date("2025-10-31") is not None
    assert parse_date("2025-10-31 00:00:00") == parse_date("2025-10-31")
    ts = parse_timestamp("2025-10-31 12:29:06+00")
    assert ts is not None and ts.utcoffset().total_seconds() == 0
    assert parse_timestamp("") is None


def test_validate_partitions_rows(sample_csv_path: Path):
    result = validate_file(sample_csv_path)
    assert result.total_rows == 8
    assert result.valid_count == 4  # TX1(pos), TX2(neg), TX3(zero), TX7
    assert result.quarantined_count == 4
    # Partition accounting is exhaustive.
    assert result.valid_count + result.quarantined_count == result.total_rows


def test_negative_obligations_are_valid_and_counted(sample_csv_path: Path):
    result = validate_file(sample_csv_path)
    assert result.negative_obligation_count() == 1
    # 1000.50 - 250.25 + 0.00 + 500.00
    assert result.obligation_total() == Decimal("1250.25")


def test_quarantine_reasons_are_specific(sample_csv_path: Path):
    result = validate_file(sample_csv_path)
    reasons = {r for _, rs in result.quarantined for r in rs}
    assert "duplicate_transaction_key" in reasons
    assert "missing_transaction_key" in reasons
    assert "blank_obligation" in reasons
    assert "malformed_obligation" in reasons


def test_soft_warning_keeps_row(sample_csv_path: Path):
    result = validate_file(sample_csv_path)
    # TX7 has an unparseable solicitation_date but must still be a valid row.
    valid_keys = {r["contract_transaction_unique_key"] for r in result.valid_rows}
    assert "TX7" in valid_keys
    assert any("unparseable_date" in w for w in result.warnings)


def test_write_quarantine_preserves_rows_and_appends_reasons(sample_csv_path: Path, tmp_path: Path):
    result = validate_file(sample_csv_path)
    out = tmp_path / "q.csv"
    n = write_quarantine(result, out)
    assert n == 4
    text = out.read_text()
    assert "_quarantine_reasons" in text
    assert "malformed_obligation" in text
    # Original malformed value preserved verbatim (not discarded).
    assert "N/A" in text
