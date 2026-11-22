"""Offline AWS S3 cost estimation for the data lake.

Estimates monthly storage and one-time migration request/transfer costs from the
local inventory, using published AWS S3 pricing for ``us-east-1``. All figures are
ESTIMATES computed offline — no AWS calls — and free-tier eligibility is NOT
assumed. Pricing constants are documented with their source; update them if AWS
pricing changes.

Pricing source: AWS S3 pricing, US East (N. Virginia), S3 Standard
(https://aws.amazon.com/s3/pricing/). Encoded as of 2025; verify before relying on
exact figures. Data transfer IN to S3 is free; GET/analytics transfer OUT is the
main variable cost and is estimated separately.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..config import Settings
from .inventory import LakeInventory, build_inventory

GiB = 1024**3

# --- AWS S3 Standard pricing (us-east-1), USD. Documented constants. ---
PRICE_STORAGE_PER_GB_MONTH = 0.023  # first 50 TB/mo
PRICE_PUT_PER_1000 = 0.005  # PUT/COPY/POST/LIST
PRICE_GET_PER_1000 = 0.0004  # GET/SELECT and all other
PRICE_TRANSFER_OUT_PER_GB = 0.09  # data transfer OUT to internet (after 100 GB free tier ignored)


@dataclass
class CostEstimate:
    label: str
    storage_gb: float
    monthly_storage_usd: float
    put_requests: int
    get_requests: int
    one_time_request_usd: float
    transfer_out_gb: float
    monthly_transfer_usd: float
    monthly_total_usd: float

    def to_dict(self) -> dict:
        return asdict(self)


def _estimate(
    label: str,
    storage_bytes: int,
    put_requests: int,
    *,
    monthly_get: int,
    monthly_transfer_out_gb: float,
) -> CostEstimate:
    storage_gb = storage_bytes / GiB
    monthly_storage = storage_gb * PRICE_STORAGE_PER_GB_MONTH
    one_time_requests = (put_requests / 1000) * PRICE_PUT_PER_1000
    monthly_transfer = monthly_transfer_out_gb * PRICE_TRANSFER_OUT_PER_GB
    monthly_get_cost = (monthly_get / 1000) * PRICE_GET_PER_1000
    return CostEstimate(
        label=label,
        storage_gb=round(storage_gb, 4),
        monthly_storage_usd=round(monthly_storage, 4),
        put_requests=put_requests,
        get_requests=monthly_get,
        one_time_request_usd=round(one_time_requests, 6),
        transfer_out_gb=round(monthly_transfer_out_gb, 4),
        monthly_transfer_usd=round(monthly_transfer, 4),
        monthly_total_usd=round(monthly_storage + monthly_transfer + monthly_get_cost, 4),
    )


def estimate_costs(settings: Settings, inventory: LakeInventory | None = None) -> dict:
    """Return cost estimates for the pilot, full lake, and 5x / 10x growth."""
    inv = inventory or build_inventory(settings)
    pilot = next((d for d in inv.datasets if d.dataset_id == "nasa_contracts_fy2026"), None)

    # Assume versioning keeps ~1 noncurrent copy during active development (≈2x storage)
    # and a modest monthly analytical read workload.
    versioning_factor = 2.0
    monthly_analytics_gets = 5000
    monthly_transfer_out_gb = 0.5  # small analytical result/export volume

    estimates: dict[str, dict] = {}
    if pilot is not None:
        estimates["nasa_fy2026_pilot"] = _estimate(
            "NASA FY2026 pilot",
            int(pilot.transfer_bytes * versioning_factor),
            put_requests=pilot.s3_object_count,
            monthly_get=1000,
            monthly_transfer_out_gb=0.05,
        ).to_dict()

    full_bytes = int(inv.total_transfer_bytes * versioning_factor)
    estimates["full_lake"] = _estimate(
        "Full lake (7 datasets, 1.41M tx)",
        full_bytes,
        put_requests=inv.total_s3_objects,
        monthly_get=monthly_analytics_gets,
        monthly_transfer_out_gb=monthly_transfer_out_gb,
    ).to_dict()
    for mult in (5, 10):
        estimates[f"growth_{mult}x"] = _estimate(
            f"{mult}x dataset growth",
            full_bytes * mult,
            put_requests=inv.total_s3_objects * mult,
            monthly_get=monthly_analytics_gets * mult,
            monthly_transfer_out_gb=monthly_transfer_out_gb * mult,
        ).to_dict()

    return {
        "region": settings.aws_region,
        "pricing_note": (
            "S3 Standard us-east-1; storage $0.023/GB-mo, PUT $0.005/1k, GET $0.0004/1k, "
            "transfer-out $0.09/GB. Source: https://aws.amazon.com/s3/pricing/ (encoded 2025). "
            "Estimates only; free tier NOT assumed; versioning ~2x storage during development."
        ),
        "recommended_budget_usd": 5,
        "budget_escalation_usd": 10,
        "budget_note": (
            "AWS Budgets alerts are notifications, NOT a hard spending cap. "
            "A $5 alert with a $10 escalation is recommended, subject to approval."
        ),
        "estimates": estimates,
    }
