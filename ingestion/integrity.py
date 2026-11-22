"""Dataset-wide transaction-identity integrity.

Milestone 2 deduplicates within a single ingestion run (across batches and archive
members). Milestone 2.1 extends identity validation to the *whole published lake*:
the same ``contract_transaction_unique_key`` must never appear in more than one
published dataset (an ``awarding_agency`` by ``fiscal_year`` partition set), because
that would double-count obligations in analytical totals.

Two entry points:

* :func:`audit_integrity` — a standalone, full-lake audit (the ``fsdw
  audit-integrity`` command). It reports duplicate keys, classifies cross-dataset
  duplicates as *identical* vs *conflicting* (different obligation or
  modification timestamp), and passes only when no cross-dataset duplicates exist.
* :func:`cross_dataset_overlap` — a focused pre-publish gate used by the archive
  pipeline. Before a candidate dataset is published it is checked against the
  already-published lake (excluding its own prior version); any overlap with a
  *different* dataset blocks publication unless explicitly forced.

Comprehensive correction/deletion lifecycle (applying delta files, superseding
versions) is deferred to the CDC milestone; this module only *detects and reports*.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import duckdb

from .config import Settings
from .logging_config import get_logger

log = get_logger(__name__)


@dataclass
class IntegrityReport:
    published_datasets: list[str] = field(default_factory=list)
    total_published_rows: int = 0
    distinct_transaction_keys: int = 0
    duplicate_key_count: int = 0  # keys appearing more than once anywhere
    cross_dataset_key_count: int = 0  # keys spanning >1 (agency, fiscal_year)
    conflicting_key_count: int = 0  # cross-dataset keys with differing values
    identical_duplicate_count: int = 0  # cross-dataset keys with identical values
    examples: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        # Integrity holds when no transaction key is duplicated anywhere in the lake.
        return self.duplicate_key_count == 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["passed"] = self.passed
        return d


def _lake_glob(settings: Settings) -> str:
    return str(settings.partitioned_parquet_dir).replace("'", "''") + "/**/*.parquet"


def _has_published(settings: Settings) -> bool:
    root = settings.partitioned_parquet_dir
    return root.exists() and any(root.glob("**/*.parquet"))


def audit_integrity(settings: Settings, *, examples: int = 10) -> IntegrityReport:
    """Audit the entire published lake for cross-dataset transaction-key collisions."""
    report = IntegrityReport()
    if not _has_published(settings):
        log.info("integrity.audit", extra={"context": {"published": False}})
        return report

    con = duckdb.connect()
    try:
        con.execute(
            "CREATE VIEW tx AS SELECT * FROM "
            f"read_parquet('{_lake_glob(settings)}', hive_partitioning=true)"
        )
        report.total_published_rows = con.execute("SELECT COUNT(*) FROM tx").fetchone()[0]
        report.distinct_transaction_keys = con.execute(
            "SELECT COUNT(DISTINCT contract_transaction_unique_key) FROM tx"
        ).fetchone()[0]
        report.published_datasets = [
            f"{a}/FY{fy}"
            for a, fy in con.execute(
                "SELECT DISTINCT awarding_agency, fiscal_year FROM tx "
                "ORDER BY awarding_agency, fiscal_year"
            ).fetchall()
        ]

        # One row per duplicated transaction key, with cross-dataset + conflict flags.
        con.execute(
            """
            CREATE VIEW dupes AS
            SELECT
                contract_transaction_unique_key AS tx_key,
                COUNT(*) AS occurrences,
                COUNT(DISTINCT awarding_agency || '|' || CAST(fiscal_year AS VARCHAR)) AS datasets,
                COUNT(DISTINCT federal_action_obligation) AS distinct_obligations,
                COUNT(DISTINCT last_modified_date) AS distinct_last_modified
            FROM tx
            GROUP BY contract_transaction_unique_key
            HAVING COUNT(*) > 1
            """
        )
        report.duplicate_key_count = con.execute("SELECT COUNT(*) FROM dupes").fetchone()[0]
        report.cross_dataset_key_count = con.execute(
            "SELECT COUNT(*) FROM dupes WHERE datasets > 1"
        ).fetchone()[0]
        report.conflicting_key_count = con.execute(
            "SELECT COUNT(*) FROM dupes "
            "WHERE distinct_obligations > 1 OR distinct_last_modified > 1"
        ).fetchone()[0]
        report.identical_duplicate_count = report.duplicate_key_count - report.conflicting_key_count
        report.examples = [
            {
                "transaction_key": r[0],
                "occurrences": r[1],
                "datasets": r[2],
                "conflicting": bool(r[3] > 1 or r[4] > 1),
            }
            for r in con.execute(
                "SELECT tx_key, occurrences, datasets, distinct_obligations, "
                "distinct_last_modified FROM dupes LIMIT ?",
                [examples],
            ).fetchall()
        ]
    finally:
        con.close()

    log.info(
        "integrity.audit",
        extra={
            "context": {
                "passed": report.passed,
                "duplicate_keys": report.duplicate_key_count,
                "cross_dataset": report.cross_dataset_key_count,
                "conflicting": report.conflicting_key_count,
            }
        },
    )
    return report


@dataclass
class OverlapResult:
    overlapping_keys: int = 0
    conflicting_keys: int = 0
    identical_keys: int = 0
    examples: list[str] = field(default_factory=list)

    @property
    def has_overlap(self) -> bool:
        return self.overlapping_keys > 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["has_overlap"] = self.has_overlap
        return d


def cross_dataset_overlap(
    staged_dir: Path,
    settings: Settings,
    *,
    current_agency: str,
    current_fiscal_year: int,
) -> OverlapResult:
    """Check a staged candidate's transaction keys against the published lake.

    Compares staged keys to all *already-published* rows except the current
    dataset's own prior version (so an idempotent re-publish never self-collides).
    An overlap means the same transaction would be published in two datasets
    (double-counting); it is classified identical vs conflicting by comparing the
    primary obligation and modification timestamp.
    """
    result = OverlapResult()
    if not _has_published(settings):
        return result

    staged_glob = str(staged_dir).replace("'", "''") + "/**/*.parquet"
    con = duckdb.connect()
    try:
        con.execute(
            "CREATE VIEW staged AS SELECT * FROM "
            f"read_parquet('{staged_glob}', hive_partitioning=true)"
        )
        # Published rows from OTHER datasets only (exclude this dataset's own prior
        # version so idempotent re-publishes never self-collide). DuckDB can't bind
        # params inside CREATE VIEW, so the agency/year are inlined (agency escaped;
        # fiscal_year cast to int).
        agency = current_agency.replace("'", "''")
        con.execute(
            "CREATE VIEW other AS SELECT * FROM "
            f"read_parquet('{_lake_glob(settings)}', hive_partitioning=true) "
            f"WHERE NOT (awarding_agency = '{agency}' AND fiscal_year = {int(current_fiscal_year)})"
        )
        rows = con.execute(
            """
            SELECT s.contract_transaction_unique_key,
                   (s.federal_action_obligation = o.federal_action_obligation
                    AND s.last_modified_date = o.last_modified_date) AS identical
            FROM (SELECT DISTINCT contract_transaction_unique_key,
                         federal_action_obligation, last_modified_date FROM staged) s
            JOIN (SELECT DISTINCT contract_transaction_unique_key,
                         federal_action_obligation, last_modified_date FROM other) o
              ON s.contract_transaction_unique_key = o.contract_transaction_unique_key
            """
        ).fetchall()
    finally:
        con.close()

    result.overlapping_keys = len(rows)
    for key, identical in rows:
        if identical:
            result.identical_keys += 1
        else:
            result.conflicting_keys += 1
        if len(result.examples) < 10:
            result.examples.append(key)
    if result.has_overlap:
        log.warning(
            "integrity.cross_dataset_overlap",
            extra={"context": result.to_dict()},
        )
    return result
