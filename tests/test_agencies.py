"""Unit tests for the extensible agency registry."""

from __future__ import annotations

import pytest
from ingestion.agencies import (
    AGENCIES,
    agency_for_code,
    list_agencies,
    resolve,
)


def test_all_five_target_agencies_present():
    keys = {a.key for a in list_agencies()}
    assert keys == {"NASA", "DOD", "DHS", "DOE", "GSA"}


def test_authoritative_toptier_codes():
    # Verified against the live USAspending toptier_agencies reference endpoint.
    assert resolve("NASA").toptier_code == "080"
    assert resolve("DOD").toptier_code == "097"
    assert resolve("DHS").toptier_code == "070"
    assert resolve("DOE").toptier_code == "089"
    assert resolve("GSA").toptier_code == "047"


@pytest.mark.parametrize(
    "identifier,expected",
    [
        ("nasa", "NASA"),
        ("DoD", "DOD"),
        ("defense", "DOD"),
        ("Department of Defense", "DOD"),
        ("homeland security", "DHS"),
        ("energy", "DOE"),
        ("  gsa  ", "GSA"),
    ],
)
def test_alias_resolution(identifier, expected):
    assert resolve(identifier).key == expected


def test_unknown_agency_raises():
    with pytest.raises(KeyError):
        resolve("treasury")


def test_agency_for_code():
    assert agency_for_code("097").key == "DOD"
    assert agency_for_code("999") is None


def test_backward_compatible_agencies_mapping_shape():
    # Milestone-1 code consumes AGENCIES[key] = {name, toptier_code, abbreviation}.
    assert set(AGENCIES["NASA"]) == {"name", "toptier_code", "abbreviation"}
    assert AGENCIES["NASA"]["toptier_code"] == "080"
