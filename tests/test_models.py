"""Unit tests for the data-contract models."""

from __future__ import annotations

import pytest
from ingestion.models import (
    DATE_COLUMNS,
    LAST_MODIFIED_COLUMN,
    TIMESTAMP_COLUMNS,
    AwardTypeGroup,
    ExtractRequest,
    schema_fingerprint,
)


def test_slug_is_deterministic_and_idempotent():
    r1 = ExtractRequest(agency="NASA", start_date="2025-10-01", end_date="2025-10-31")
    r2 = ExtractRequest(agency="NASA", start_date="2025-10-01", end_date="2025-10-31")
    assert r1.slug == r2.slug == "nasa_contracts_action_date_20251001_20251031"


def test_unknown_agency_raises():
    req = ExtractRequest(agency="NOPE", start_date="2025-10-01", end_date="2025-10-31")
    with pytest.raises(KeyError):
        _ = req.agency_meta


def test_api_payload_shape():
    req = ExtractRequest(
        agency="NASA",
        start_date="2025-10-01",
        end_date="2025-10-31",
        award_type_group=AwardTypeGroup.CONTRACTS,
    )
    payload = req.to_api_payload()
    assert payload["filters"]["agencies"][0]["tier"] == "toptier"
    assert payload["filters"]["prime_award_types"] == ["A", "B", "C", "D"]
    assert payload["filters"]["date_range"] == {
        "start_date": "2025-10-01",
        "end_date": "2025-10-31",
    }
    assert payload["file_format"] == "csv"


def test_schema_fingerprint_sensitive_to_order():
    a = schema_fingerprint(["x", "y", "z"])
    b = schema_fingerprint(["x", "z", "y"])
    assert a != b
    assert a == schema_fingerprint(["x", "y", "z"])  # stable


def test_last_modified_is_timestamp_not_date():
    # Guards the schema discovery: last_modified_date carries a time component.
    assert LAST_MODIFIED_COLUMN in TIMESTAMP_COLUMNS
    assert LAST_MODIFIED_COLUMN not in DATE_COLUMNS
