"""Object + content verification for cloud publication.

Two layers:

* **Object verification** — every uploaded/published object exists and its SHA-256
  and size match the local source (bit-for-bit integrity).
* **Content readback** — the published Parquet is re-read *independently* with
  DuckDB (directly from the backend: a local path or an ``s3://`` URI via httpfs)
  and its transaction count, distinct keys, and decimal-exact net obligation are
  recomputed. Readers address the **exact** active part-file keys from the catalog,
  never a glob over historical snapshots.

DuckDB S3 access uses the standard AWS credential chain via a DuckDB SECRET — no
literal credentials ever appear in SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import duckdb

from ..config import Settings
from ..logging_config import get_logger
from ..storage_backends.base import StorageBackend

log = get_logger(__name__)


@dataclass
class ObjectCheck:
    key: str
    ok: bool
    reason: str = ""


def verify_objects(
    backend: StorageBackend, expected: list[tuple[str, str, int]]
) -> list[ObjectCheck]:
    """Verify each ``(key, sha256, size)`` object exists and matches."""
    checks: list[ObjectCheck] = []
    for key, sha, size in expected:
        if not backend.exists(key):
            checks.append(ObjectCheck(key, False, "missing"))
            continue
        meta = backend.stat(key)
        if meta.size != size:
            checks.append(ObjectCheck(key, False, f"size {meta.size} != {size}"))
            continue
        if backend.sha256(key) != sha:
            checks.append(ObjectCheck(key, False, "sha256 mismatch"))
            continue
        checks.append(ObjectCheck(key, True))
    return checks


def duckdb_connection(backend: StorageBackend, settings: Settings) -> duckdb.DuckDBPyConnection:
    """Return a DuckDB connection, configured for S3 (httpfs + credential chain)
    when the backend is S3."""
    con = duckdb.connect()
    if backend.name == "s3":
        con.execute("INSTALL httpfs; LOAD httpfs;")
        # Credential-chain secret — never embeds literal keys.
        con.execute(
            "CREATE OR REPLACE SECRET fsip_s3 "
            f"(TYPE s3, PROVIDER credential_chain, REGION '{settings.aws_region}')"
        )
    return con


def _file_list_sql(backend: StorageBackend, part_keys: list[str]) -> str:
    uris = [backend.uri(k).replace("'", "''") for k in part_keys]
    quoted = ", ".join(f"'{u}'" for u in uris)
    return f"read_parquet([{quoted}], hive_partitioning=true)"


@dataclass
class ContentTotals:
    transaction_count: int
    distinct_keys: int
    net_obligation: Decimal
    partition_counts: dict[str, int]


def readback_totals(
    backend: StorageBackend, settings: Settings, part_keys: list[str]
) -> ContentTotals:
    """Independently recompute dataset totals from the given part-file keys."""
    con = duckdb_connection(backend, settings)
    src = _file_list_sql(backend, part_keys)
    try:
        con.execute(f"CREATE VIEW tx AS SELECT * FROM {src}")
        rows = con.execute("SELECT COUNT(*) FROM tx").fetchone()[0]
        distinct = con.execute(
            "SELECT COUNT(DISTINCT contract_transaction_unique_key) FROM tx"
        ).fetchone()[0]
        net = con.execute("SELECT SUM(federal_action_obligation) FROM tx").fetchone()[0]
        part_rows = con.execute(
            "SELECT awarding_agency, fiscal_year, fiscal_month, COUNT(*) " "FROM tx GROUP BY 1,2,3"
        ).fetchall()
    finally:
        con.close()
    return ContentTotals(
        transaction_count=rows,
        distinct_keys=distinct,
        net_obligation=Decimal(str(net)) if net is not None else Decimal("0"),
        partition_counts={f"{a}/{fy}/{fm:0>2}": n for a, fy, fm, n in part_rows},
    )


def count_key_overlap(
    backend: StorageBackend,
    settings: Settings,
    keys_a: list[str],
    keys_b: list[str],
) -> int:
    """Count transaction keys present in BOTH file sets (dataset-wide uniqueness).

    Done entirely in DuckDB (a distinct-key join) so it never materializes millions
    of keys in Python.
    """
    if not keys_a or not keys_b:
        return 0
    con = duckdb_connection(backend, settings)
    a = _file_list_sql(backend, keys_a)
    b = _file_list_sql(backend, keys_b)
    try:
        return con.execute(
            f"SELECT COUNT(*) FROM "
            f"(SELECT DISTINCT contract_transaction_unique_key k FROM {a}) s "
            f"JOIN (SELECT DISTINCT contract_transaction_unique_key k FROM {b}) o USING(k)"
        ).fetchone()[0]
    finally:
        con.close()
