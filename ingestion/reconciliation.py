"""Historical financial reconciliation (archive / dataset / partition levels).

Reconciliation is the publication gate: a staged (candidate) dataset is published
only if its independently re-computed figures match the accounting produced during
ingestion, using **exact decimal arithmetic** throughout.

Two complementary views are compared:

* **Accounting identity** — the row partition is exhaustive:
  ``valid + quarantined + filtered == source_rows`` and
  ``positive + negative == net`` (zeros contribute 0).
* **Source-to-target** — the staged Parquet, read back independently via DuckDB,
  must agree with the ingestion accounting on total rows, distinct transaction
  keys, net obligation, and *per-partition* row counts.

No obligation is ever coerced to zero: unparseable primary obligations are
quarantined upstream and surface here as part of the quarantined count, so the
reconciliation explicitly accounts for them rather than hiding them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import duckdb

from .logging_config import get_logger
from .partitioning import PartitionKey

log = get_logger(__name__)


@dataclass
class IngestTotals:
    """Accounting totals accumulated during a streaming ingestion run."""

    source_rows: int = 0
    valid_rows: int = 0
    quarantined_rows: int = 0
    filtered_rows: int = 0
    duplicate_rows: int = 0
    net_obligation: Decimal = Decimal("0")
    positive_obligation: Decimal = Decimal("0")
    negative_obligation: Decimal = Decimal("0")
    negative_count: int = 0
    zero_count: int = 0
    distinct_valid_tx: int = 0
    partition_rows: dict[PartitionKey, int] = field(default_factory=dict)


@dataclass
class ReconciliationCheck:
    name: str
    passed: bool
    expected: str
    actual: str


@dataclass
class ReconciliationResult:
    checks: list[ReconciliationCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def add(self, name: str, expected, actual) -> None:
        self.checks.append(
            ReconciliationCheck(
                name=name,
                passed=(expected == actual),
                expected=str(expected),
                actual=str(actual),
            )
        )

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "expected": c.expected, "actual": c.actual}
                for c in self.checks
            ],
        }


def _read_staged(staged_dir: Path):
    """Open a DuckDB view over a Hive-partitioned staged Parquet dataset."""
    con = duckdb.connect()
    glob = str(staged_dir).replace("'", "''")
    con.execute(
        "CREATE VIEW tx AS SELECT * FROM "
        f"read_parquet('{glob}/**/*.parquet', hive_partitioning=true)"
    )
    return con


def reconcile_dataset(totals: IngestTotals, staged_dir: Path) -> ReconciliationResult:
    """Reconcile ingestion accounting against the staged Parquet (incl. partitions)."""
    result = ReconciliationResult()

    # 1) Accounting identity: the source row partition is exhaustive.
    result.add(
        "accounting_partition_exhaustive",
        totals.source_rows,
        totals.valid_rows + totals.quarantined_rows + totals.filtered_rows,
    )
    # 2) Sign decomposition sums to net (decimal-exact).
    result.add(
        "positive_plus_negative_equals_net",
        totals.net_obligation,
        totals.positive_obligation + totals.negative_obligation,
    )

    con = _read_staged(staged_dir)
    try:
        pq_rows = con.execute("SELECT COUNT(*) FROM tx").fetchone()[0]
        pq_distinct = con.execute(
            "SELECT COUNT(DISTINCT contract_transaction_unique_key) FROM tx"
        ).fetchone()[0]
        pq_net = con.execute("SELECT SUM(federal_action_obligation) FROM tx").fetchone()[0]
        pq_net_dec = Decimal(str(pq_net)) if pq_net is not None else Decimal("0")

        part_rows = con.execute(
            """
            SELECT awarding_agency, fiscal_year, fiscal_month, COUNT(*)
            FROM tx GROUP BY awarding_agency, fiscal_year, fiscal_month
            """
        ).fetchall()
    finally:
        con.close()

    # 3) Source-to-target: staged Parquet agrees with ingestion accounting.
    result.add("staged_rows_equal_valid", totals.valid_rows, pq_rows)
    result.add("staged_distinct_tx_equal_valid", totals.valid_rows, pq_distinct)
    result.add("staged_net_obligation_matches", totals.net_obligation, pq_net_dec)

    # 4) Per-partition row counts match exactly.
    staged_part = {PartitionKey(str(a), int(fy), int(fm)): int(n) for a, fy, fm, n in part_rows}
    expected_part = dict(totals.partition_rows)
    result.add("partition_count_matches", len(expected_part), len(staged_part))
    all_keys = set(expected_part) | set(staged_part)
    mismatches = [
        k.relative_path.as_posix()
        for k in all_keys
        if expected_part.get(k, 0) != staged_part.get(k, 0)
    ]
    result.add("partition_rows_match", [], mismatches)

    log.info(
        "reconciliation.done",
        extra={"context": {"passed": result.passed, "checks": len(result.checks)}},
    )
    return result


def reconcile_published(agency: str, fiscal_year: int, settings) -> ReconciliationResult:
    """Re-reconcile an already-published dataset against its manifest.

    Reads the published Parquet independently and verifies it still agrees with the
    counts and decimal-exact financial totals recorded in the dataset manifest —
    a standalone integrity check runnable any time after ingestion.
    """
    dataset_id = f"{agency.lower()}_contracts_fy{fiscal_year}"
    manifest_path = settings.manifest_dir / f"{dataset_id}.json"
    published = (
        settings.partitioned_parquet_dir
        / f"awarding_agency={agency.upper()}"
        / f"fiscal_year={fiscal_year}"
    )
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest for {dataset_id} at {manifest_path}")
    if not published.exists():
        raise FileNotFoundError(f"No published dataset at {published}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    m_totals = manifest["totals"]
    result = ReconciliationResult()

    con = _read_staged(published)
    try:
        rows = con.execute("SELECT COUNT(*) FROM tx").fetchone()[0]
        distinct = con.execute(
            "SELECT COUNT(DISTINCT contract_transaction_unique_key) FROM tx"
        ).fetchone()[0]
        net = con.execute("SELECT SUM(federal_action_obligation) FROM tx").fetchone()[0]
        neg = con.execute("SELECT COUNT(*) FROM tx WHERE federal_action_obligation < 0").fetchone()[
            0
        ]
        part_count = con.execute(
            "SELECT COUNT(DISTINCT (awarding_agency, fiscal_year, fiscal_month)) FROM tx"
        ).fetchone()[0]
    finally:
        con.close()

    net_dec = Decimal(str(net)) if net is not None else Decimal("0")
    result.add("published_rows_match_manifest", m_totals["valid_rows"], rows)
    result.add("distinct_tx_equal_rows", rows, distinct)
    result.add("net_obligation_matches_manifest", Decimal(m_totals["net_obligation"]), net_dec)
    result.add(
        "negative_count_matches_manifest",
        m_totals["negative_obligation_count"],
        neg,
    )
    result.add("partition_count_matches_manifest", manifest["partition_count"], part_count)
    return result
