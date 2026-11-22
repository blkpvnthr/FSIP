"""External financial reconciliation against USAspending aggregate endpoints.

Milestone 2's reconciliation is *internal* (source CSV -> Parquet -> manifest).
Milestone 2.1 adds an *external* cross-check against USAspending's authoritative
``/api/v2/search/spending_over_time/`` aggregate, using an **equivalent scope**:
same awarding agency, same federal fiscal year (``action_date``), and the same
prime award-type codes. The endpoint aggregates transaction obligations
(``spending_level = transactions``), which is the same quantity the warehouse sums.

An exact match is not expected: the live aggregate reflects the current data
refresh while a published dataset is a point-in-time snapshot. The reconciliation
therefore reports the absolute and percentage difference and whether it falls
within a documented tolerance — it never silently asserts equality.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal

from .agencies import resolve
from .client import UsaspendingClient
from .config import Settings
from .logging_config import get_logger

log = get_logger(__name__)

SPENDING_OVER_TIME_PATH = "/api/v2/search/spending_over_time/"
DEFAULT_CONTRACT_CODES = ("A", "B", "C", "D")


@dataclass
class ExternalReconciliation:
    agency: str
    fiscal_year: int
    award_type_codes: list[str]
    local_net_obligation: str
    external_contract_obligations: str
    difference: str
    pct_difference: float
    within_tolerance: bool
    tolerance_pct: float
    endpoint: str
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


def fetch_external_contract_obligations(
    agency_identifier: str,
    fiscal_year: int,
    settings: Settings,
    *,
    award_type_codes: tuple[str, ...] = DEFAULT_CONTRACT_CODES,
    client: UsaspendingClient | None = None,
) -> Decimal:
    """Return USAspending's aggregated contract obligations for an equivalent scope."""
    agency = resolve(agency_identifier)
    payload = {
        "group": "fiscal_year",
        "filters": {
            "time_period": [
                {"start_date": f"{fiscal_year - 1}-10-01", "end_date": f"{fiscal_year}-09-30"}
            ],
            "agencies": [{"type": "awarding", "tier": "toptier", "name": agency.name}],
            "award_type_codes": list(award_type_codes),
        },
    }
    client = client or UsaspendingClient(settings)
    url = f"{settings.api_base_url}{SPENDING_OVER_TIME_PATH}"
    response = client._request("POST", url, json=payload)
    data = response.json()
    total = Decimal("0")
    for row in data.get("results", []):
        if str(row.get("time_period", {}).get("fiscal_year")) == str(fiscal_year):
            # Prefer the explicit contract bucket; fall back to aggregated_amount.
            value = row.get("Contract_Obligations", row.get("aggregated_amount", 0))
            total = Decimal(str(value))
            break
    log.info(
        "external.fetched",
        extra={
            "context": {
                "agency": agency.key,
                "fiscal_year": fiscal_year,
                "external_total": str(total),
            }
        },
    )
    return total


def reconcile_external(
    agency_identifier: str,
    fiscal_year: int,
    settings: Settings,
    *,
    award_type_codes: tuple[str, ...] = DEFAULT_CONTRACT_CODES,
    tolerance_pct: float = 1.0,
    client: UsaspendingClient | None = None,
) -> ExternalReconciliation:
    """Compare a published dataset's net obligation to the external aggregate."""
    agency = resolve(agency_identifier)
    dataset_id = f"{agency.key.lower()}_contracts_fy{fiscal_year}"
    manifest_path = settings.manifest_dir / f"{dataset_id}.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest for {dataset_id}; ingest it first.")
    import json

    local = Decimal(json.loads(manifest_path.read_text())["totals"]["net_obligation"])
    external = fetch_external_contract_obligations(
        agency.key, fiscal_year, settings, award_type_codes=award_type_codes, client=client
    )
    diff = local - external
    pct = float(abs(diff) / external * 100) if external else float("inf")
    within = pct <= tolerance_pct
    return ExternalReconciliation(
        agency=agency.key,
        fiscal_year=fiscal_year,
        award_type_codes=list(award_type_codes),
        local_net_obligation=str(local),
        external_contract_obligations=str(external),
        difference=str(diff),
        pct_difference=round(pct, 6),
        within_tolerance=within,
        tolerance_pct=tolerance_pct,
        endpoint=SPENDING_OVER_TIME_PATH,
        note=(
            "Equivalent scope: awarding agency, fiscal year (action_date), award types "
            f"{list(award_type_codes)}. A nonzero difference reflects data-refresh timing "
            "(live aggregate vs point-in-time snapshot), not a reconciliation failure."
        ),
    )
