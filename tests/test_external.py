"""Tests for external financial reconciliation (mocked offline + opt-in live)."""

from __future__ import annotations

import json

import pytest
import responses
from ingestion.config import Settings
from ingestion.external import (
    SPENDING_OVER_TIME_PATH,
    fetch_external_contract_obligations,
    reconcile_external,
)

BASE = "https://api.usaspending.gov"


def _write_manifest(settings: Settings, net: str) -> None:
    settings.manifest_dir.mkdir(parents=True, exist_ok=True)
    (settings.manifest_dir / "nasa_contracts_fy2026.json").write_text(
        json.dumps({"totals": {"net_obligation": net}})
    )


@responses.activate
def test_fetch_external_parses_contract_obligations(settings: Settings):
    responses.post(
        f"{BASE}{SPENDING_OVER_TIME_PATH}",
        json={
            "results": [
                {
                    "time_period": {"fiscal_year": "2026"},
                    "Contract_Obligations": 16095925883.0,
                    "aggregated_amount": 16095925883.0,
                }
            ]
        },
        status=200,
    )
    total = fetch_external_contract_obligations("NASA", 2026, settings)
    assert str(total) == "16095925883.0"


@responses.activate
def test_reconcile_external_within_tolerance(settings: Settings):
    _write_manifest(settings, "16095847228.00")
    responses.post(
        f"{BASE}{SPENDING_OVER_TIME_PATH}",
        json={
            "results": [
                {"time_period": {"fiscal_year": "2026"}, "Contract_Obligations": 16095925883.0}
            ]
        },
        status=200,
    )
    r = reconcile_external("NASA", 2026, settings)
    assert r.within_tolerance is True
    assert r.pct_difference < 0.01  # ~0.0005%
    assert r.difference == "-78655.00"


@responses.activate
def test_reconcile_external_flags_large_discrepancy(settings: Settings):
    _write_manifest(settings, "10000000000.00")  # far from external
    responses.post(
        f"{BASE}{SPENDING_OVER_TIME_PATH}",
        json={
            "results": [
                {"time_period": {"fiscal_year": "2026"}, "Contract_Obligations": 16095925883.0}
            ]
        },
        status=200,
    )
    r = reconcile_external("NASA", 2026, settings, tolerance_pct=1.0)
    assert r.within_tolerance is False


def test_reconcile_external_requires_manifest(settings: Settings):
    with pytest.raises(FileNotFoundError):
        reconcile_external("NASA", 2026, settings)


@pytest.mark.live
def test_live_external_total_is_positive(settings: Settings):
    total = fetch_external_contract_obligations("NASA", 2026, settings)
    assert total > 0  # real USAspending aggregate for NASA FY2026 contracts
