"""Data contract and request models.

This module encodes what the pipeline *expects* from the USAspending contract
download so that schema drift is detected loudly rather than silently absorbed.

Design notes
------------
* ``REQUIRED_COLUMNS`` is intentionally a *minimal, stable* subset of the ~280
  columns in a prime-contract download. These are the identifiers, financial,
  agency, recipient and date fields the warehouse is built around. The actual
  (full) column set is discovered at runtime, fingerprinted, and recorded in the
  manifest — we never hard-code the complete list, and extra columns are
  preserved, not dropped.
* ``DECIMAL_COLUMNS`` / ``DATE_COLUMNS`` are typed explicitly so financial values
  stay decimal-exact (never floats) and dates are real dates. Every other column
  is preserved verbatim as a string to guarantee source fidelity.

The specific column names here are those published in the USAspending
``bulk_download/awards`` contract CSV; they are re-verified against the live
header at extraction time (see ``validate.verify_contract``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

# The agency registry is the single source of truth (see ``ingestion.agencies``).
# ``AGENCIES`` is re-exported here in its original Milestone-1 shape so existing
# code (``ExtractRequest.agency_meta``, the CLI) keeps working unchanged.
from .agencies import AGENCIES

# --------------------------------------------------------------------------- #
# Federal fiscal-calendar helpers
# --------------------------------------------------------------------------- #
# The U.S. federal fiscal year runs October 1 -> September 30 and is named for the
# calendar year in which it *ends*. Fiscal month 01 = October, ..., 12 = September.


def federal_fiscal_year(d: date) -> int:
    """Return the federal fiscal year for a date (FY starts October 1)."""
    return d.year + 1 if d.month >= 10 else d.year


def federal_fiscal_month(d: date) -> int:
    """Return the federal fiscal month (1=Oct .. 12=Sep) for a date."""
    return d.month - 9 if d.month >= 10 else d.month + 3


def federal_fiscal_quarter(d: date) -> int:
    """Return the federal fiscal quarter (1..4) for a date."""
    return (federal_fiscal_month(d) - 1) // 3 + 1


# --------------------------------------------------------------------------- #
# Award-type groups -> USAspending prime_award_type codes
# --------------------------------------------------------------------------- #


class AwardTypeGroup(str, Enum):
    """Logical groupings exposed on the CLI, mapped to API prime-award codes."""

    CONTRACTS = "contracts"
    IDVS = "idvs"
    CONTRACTS_AND_IDVS = "contracts_and_idvs"


# Prime-award type codes per USAspending:
#   A/B/C/D  = definitive contracts, purchase/delivery orders, BPA calls
#   IDV_*    = indefinite delivery vehicles
AWARD_TYPE_CODES: dict[AwardTypeGroup, list[str]] = {
    AwardTypeGroup.CONTRACTS: ["A", "B", "C", "D"],
    AwardTypeGroup.IDVS: ["IDV_A", "IDV_B", "IDV_C", "IDV_D", "IDV_E"],
    AwardTypeGroup.CONTRACTS_AND_IDVS: [
        "A",
        "B",
        "C",
        "D",
        "IDV_A",
        "IDV_B",
        "IDV_C",
        "IDV_D",
        "IDV_E",
    ],
}


# --------------------------------------------------------------------------- #
# The data contract: stable columns the warehouse depends on
# --------------------------------------------------------------------------- #

# Stable transaction identifier (unique per transaction).
TRANSACTION_KEY = "contract_transaction_unique_key"
# Generated unique award identifier (groups transactions into an award).
AWARD_KEY = "contract_award_unique_key"
# Primary financial obligation of the transaction (may be negative = deobligation).
OBLIGATION_COLUMN = "federal_action_obligation"
ACTION_DATE_COLUMN = "action_date"
LAST_MODIFIED_COLUMN = "last_modified_date"

# Columns that MUST exist or the extraction is rejected as a contract breach.
REQUIRED_COLUMNS: tuple[str, ...] = (
    TRANSACTION_KEY,
    AWARD_KEY,
    "award_id_piid",
    "modification_number",
    OBLIGATION_COLUMN,
    ACTION_DATE_COLUMN,
    LAST_MODIFIED_COLUMN,
    "recipient_uei",
    "recipient_name",
    "awarding_agency_code",
    "awarding_sub_agency_code",
    "funding_agency_code",
)

# Financial columns -> decimal128(23, 2). Preserves sign; never parsed as float.
DECIMAL_COLUMNS: frozenset[str] = frozenset(
    {
        OBLIGATION_COLUMN,
        "total_dollars_obligated",
        "current_total_value_of_award",
        "potential_total_value_of_award",
        "base_and_exercised_options_value",
        "base_and_all_options_value",
    }
)

# Columns that should be typed as real dates (date32) when present.
#
# NOTE: these were verified against a live NASA October-2025 download. The source
# emits a plain ``YYYY-MM-DD`` for ``action_date``/``solicitation_date`` and the
# period-of-performance dates, but ``last_modified_date`` carries a full
# timestamp with a UTC offset (e.g. ``2025-10-31 12:29:06+00``). We therefore
# type it as a timestamp, not a date, to avoid silently truncating the time —
# and because ``last_modified_date`` is the source modification field later
# milestones will use for incremental/CDC loads.
DATE_COLUMNS: frozenset[str] = frozenset(
    {
        ACTION_DATE_COLUMN,
        "period_of_performance_start_date",
        "period_of_performance_current_end_date",
        "period_of_performance_potential_end_date",
        "solicitation_date",
    }
)

# Columns that should be typed as timezone-aware timestamps (timestamp[us, UTC]).
TIMESTAMP_COLUMNS: frozenset[str] = frozenset(
    {
        LAST_MODIFIED_COLUMN,
    }
)

DECIMAL_PRECISION = 23
DECIMAL_SCALE = 2


def schema_fingerprint(header: list[str]) -> str:
    """Return a SHA-256 fingerprint of the *ordered* column header.

    Two downloads with an identical column layout fingerprint identically;
    any column added, removed, or reordered changes the fingerprint, giving the
    manifest a cheap, stable schema-drift signal.
    """
    joined = "\n".join(header)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Extraction request
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ExtractRequest:
    """A fully-resolved extraction request (what/when/how to pull)."""

    agency: str
    start_date: str  # YYYY-MM-DD
    end_date: str  # YYYY-MM-DD
    award_type_group: AwardTypeGroup = AwardTypeGroup.CONTRACTS
    date_type: str = "action_date"  # or "last_modified_date"
    agency_type: str = "awarding"  # "awarding" or "funding"
    file_format: str = "csv"

    @property
    def agency_meta(self) -> dict[str, str]:
        key = self.agency.upper()
        if key not in AGENCIES:
            raise KeyError(f"Unknown agency '{self.agency}'. Known: {sorted(AGENCIES)}")
        return AGENCIES[key]

    @property
    def prime_award_type_codes(self) -> list[str]:
        return AWARD_TYPE_CODES[self.award_type_group]

    @property
    def slug(self) -> str:
        """Deterministic identifier used for output paths and idempotency.

        Identical requests map to an identical slug, so re-running overwrites the
        same Parquet/manifest rather than producing duplicate outputs.
        """
        start = self.start_date.replace("-", "")
        end = self.end_date.replace("-", "")
        return (
            f"{self.agency.lower()}_{self.award_type_group.value}_"
            f"{self.date_type}_{start}_{end}"
        )

    def to_api_payload(self, columns: list[str] | None = None) -> dict:
        """Build the POST body for ``/api/v2/bulk_download/awards/``."""
        payload: dict = {
            "filters": {
                "agencies": [
                    {
                        "name": self.agency_meta["name"],
                        "tier": "toptier",
                        "type": self.agency_type,
                    }
                ],
                "prime_award_types": self.prime_award_type_codes,
                "date_type": self.date_type,
                "date_range": {
                    "start_date": self.start_date,
                    "end_date": self.end_date,
                },
            },
            "file_format": self.file_format,
        }
        if columns:
            payload["columns"] = columns
        return payload


@dataclass
class RowOutcome:
    """Result of validating a single source row."""

    valid: bool
    reasons: list[str] = field(default_factory=list)
