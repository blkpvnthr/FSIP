"""Run the DuckDB analytics SQL layer over the published NASA contracts Parquet set.

This runner wires a DuckDB view named ``tx`` over the Hive-partitioned Parquet
dataset and executes every ``analytics/sql/*.sql`` query against it. Each query is a
single self-contained SELECT that references ``tx`` (the SQL files never embed
``read_parquet`` themselves -- this module is the only place the path is wired in).

Financial semantics (see individual .sql headers for detail):
- ``federal_action_obligation`` is a per-transaction obligation, NOT an outlay; the
  net obligation is its SUM, and de-obligations are preserved as negatives.
- Award count = COUNT(DISTINCT contract_award_unique_key); transaction count =
  COUNT(DISTINCT contract_transaction_unique_key).
- Coverage is NASA only, FY2025 (full) + FY2026 (partial).

Usage:
    python analytics/run_analytics.py [--data-dir data] [--out-dir analytics/reports]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

MAX_ROWS = 25

SQL_DIR = Path(__file__).resolve().parent / "sql"


def build_connection(data_dir: Path) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB connection with the ``tx`` view wired to Parquet.

    The glob + ``hive_partitioning=true`` pattern is the required access path: it
    exposes the partition columns (awarding_agency, fiscal_year, fiscal_month) as
    regular columns on ``tx``.
    """
    parquet_glob = (data_dir / "parquet" / "contracts" / "**" / "*.parquet").as_posix()
    # CREATE VIEW cannot be prepared, so inline the literal; escape single quotes.
    safe_glob = parquet_glob.replace("'", "''")
    con = duckdb.connect()
    con.execute(
        "CREATE VIEW tx AS " f"SELECT * FROM read_parquet('{safe_glob}', hive_partitioning=true)"
    )
    return con


def discover_sql_files() -> list[Path]:
    """Return the analytics .sql files in deterministic (sorted) order."""
    return sorted(SQL_DIR.glob("*.sql"))


def format_markdown_table(columns: list[str], rows: list[tuple[object, ...]]) -> str:
    """Render query results as a GitHub-flavored Markdown table."""
    if not columns:
        return "_(no columns)_\n"
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    lines = [header, separator]
    for row in rows:
        cells = ["" if value is None else str(value) for value in row]
        lines.append("| " + " | ".join(cells) + " |")
    if not rows:
        lines.append("| " + " | ".join("_(0 rows)_" for _ in columns) + " |")
    return "\n".join(lines) + "\n"


def run_query(
    con: duckdb.DuckDBPyConnection, sql_path: Path
) -> tuple[list[str], list[tuple[object, ...]], int]:
    """Execute one SQL file and return (columns, truncated_rows, total_row_count)."""
    sql = sql_path.read_text()
    cursor = con.execute(sql)
    columns = [desc[0] for desc in cursor.description] if cursor.description else []
    all_rows = cursor.fetchall()
    return columns, all_rows[:MAX_ROWS], len(all_rows)


def main() -> int:
    """Parse args, run all analytics queries, print output, and write the report."""
    parser = argparse.ArgumentParser(description="Run the DuckDB analytics SQL layer.")
    parser.add_argument("--data-dir", default="data", help="Root data directory (default: data)")
    parser.add_argument(
        "--out-dir",
        default="analytics/reports",
        help="Directory for the Markdown report (default: analytics/reports)",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    con = build_connection(data_dir)
    sql_files = discover_sql_files()
    if not sql_files:
        print(f"No .sql files found under {SQL_DIR}", file=sys.stderr)
        return 1

    report_parts: list[str] = [
        "# Federal Spending Analytics Report",
        "",
        "Source view `tx` = NASA prime contract transactions (FY2025 full, FY2026 partial).",
        f"Rows shown per query are truncated to {MAX_ROWS}.",
        "",
    ]

    for sql_path in sql_files:
        name = sql_path.stem
        columns, rows, total = run_query(con, sql_path)

        print(f"\n{'=' * 78}")
        print(f"{name}  ({total} row(s); showing up to {MAX_ROWS})")
        print("=" * 78)
        if columns:
            print(" | ".join(columns))
        for row in rows:
            print(" | ".join("" if v is None else str(v) for v in row))

        report_parts.append(f"## {name}")
        report_parts.append("")
        report_parts.append(f"_{total} row(s); showing up to {MAX_ROWS}._")
        report_parts.append("")
        report_parts.append(format_markdown_table(columns, rows))

    report_path = out_dir / "analytics_report.md"
    report_path.write_text("\n".join(report_parts))
    print(f"\nReport written to {report_path}")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
