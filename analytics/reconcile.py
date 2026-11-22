"""DuckDB reconciliation for a materialized Parquet extract.

Loads the Parquet produced by the ingestion pipeline into DuckDB, computes the
headline metrics with SQL, and cross-checks them against the extraction manifest.
Exits non-zero if any reconciliation check fails — so it doubles as a post-load
gate in CI or a cron job.

Usage::

    python analytics/reconcile.py \\
        --parquet data/parquet/<slug>.parquet \\
        --manifest data/manifests/<slug>.json
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

import duckdb


def reconcile(parquet_path: Path, manifest_path: Path | None) -> int:
    con = duckdb.connect()
    # read_parquet inside CREATE VIEW cannot be a prepared parameter; inline the
    # path with single-quotes escaped (paths are operator-supplied, not untrusted).
    safe_path = str(parquet_path).replace("'", "''")
    con.execute(f"CREATE VIEW tx AS SELECT * FROM read_parquet('{safe_path}')")

    row_count = con.execute("SELECT COUNT(*) FROM tx").fetchone()[0]
    distinct_keys = con.execute(
        "SELECT COUNT(DISTINCT contract_transaction_unique_key) FROM tx"
    ).fetchone()[0]
    oblig_total = con.execute("SELECT SUM(federal_action_obligation) FROM tx").fetchone()[0]
    neg_rows = con.execute(
        "SELECT COUNT(*) FROM tx WHERE federal_action_obligation < 0"
    ).fetchone()[0]
    date_min, date_max = con.execute("SELECT MIN(action_date), MAX(action_date) FROM tx").fetchone()

    print("=" * 60)
    print("DuckDB reconciliation")
    print("=" * 60)
    print(f"parquet                     : {parquet_path}")
    print(f"row_count                   : {row_count}")
    print(f"distinct_transaction_keys   : {distinct_keys}")
    print(f"obligation_total            : {oblig_total}")
    print(f"negative_obligation_rows    : {neg_rows}")
    print(f"action_date range           : {date_min} .. {date_max}")

    print("\nTop recipients by total obligation:")
    rows = con.execute(
        """
        SELECT recipient_name, COUNT(*) AS n, SUM(federal_action_obligation) AS total
        FROM tx GROUP BY recipient_name ORDER BY total DESC LIMIT 5
        """
    ).fetchall()
    for name, n, total in rows:
        print(f"  {total:>18,.2f}  ({n:>3} tx)  {name}")

    checks: list[tuple[str, bool]] = [
        ("no_duplicate_keys", row_count == distinct_keys),
    ]

    if manifest_path and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        m_rows = manifest["valid_row_count"]
        m_total = Decimal(manifest["obligation_total"])
        m_neg = manifest["negative_obligation_count"]
        duck_total = Decimal(str(oblig_total)) if oblig_total is not None else Decimal("0")
        print("\nCross-check vs manifest:")
        print(f"  manifest valid_row_count   : {m_rows}")
        print(f"  manifest obligation_total  : {m_total}")
        print(f"  manifest negative_count    : {m_neg}")
        checks += [
            ("row_count_matches_manifest", row_count == m_rows),
            ("obligation_total_matches_manifest", duck_total == m_total),
            ("negative_count_matches_manifest", neg_rows == m_neg),
        ]

    print("\nChecks:")
    all_ok = True
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        all_ok = all_ok and ok

    print("=" * 60)
    print("RESULT:", "ALL CHECKS PASSED" if all_ok else "RECONCILIATION FAILED")
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DuckDB reconciliation of a Parquet extract.")
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.parquet.exists():
        print(f"ERROR: parquet not found: {args.parquet}", file=sys.stderr)
        return 2
    return reconcile(args.parquet, args.manifest)


if __name__ == "__main__":
    raise SystemExit(main())
