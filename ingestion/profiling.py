"""Historical data profiling.

Generates machine-readable JSON and human-readable Markdown profiles for a
published dataset, combining:

* **manifest-sourced accounting** — exact source/valid/quarantined/filtered/
  duplicate counts and decimal-exact financial totals recorded at ingestion time;
* **DuckDB-computed statistics** over the published Parquet — dataset-level counts,
  per-fiscal-month financial distributions, exact distinct identifiers, and
  column-level null/distinct/min/max via ``SUMMARIZE``; and
* **storage statistics** — Parquet file sizes, per-partition records, and the
  source-to-Parquet compression ratio.

Statistics that are approximate (DuckDB's ``approx_unique`` from ``SUMMARIZE``)
are labelled as such; headline financial figures are exact decimals.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from .config import Settings
from .logging_config import get_logger

log = get_logger(__name__)


@dataclass
class ProfileResult:
    dataset_id: str
    json_path: Path
    markdown_path: Path
    profile: dict


def _dataset_paths(agency: str, fiscal_year: int, settings: Settings) -> tuple[str, Path, Path]:
    dataset_id = f"{agency.lower()}_contracts_fy{fiscal_year}"
    published = (
        settings.partitioned_parquet_dir
        / f"awarding_agency={agency.upper()}"
        / f"fiscal_year={fiscal_year}"
    )
    manifest = settings.manifest_dir / f"{dataset_id}.json"
    return dataset_id, published, manifest


def _storage_stats(published_dir: Path) -> dict:
    files = sorted(published_dir.glob("**/*.parquet"))
    sizes = [f.stat().st_size for f in files]
    per_partition: dict[str, dict] = {}
    for f in files:
        part = f.parent.name  # fiscal_month=NN
        per_partition.setdefault(part, {"files": 0, "bytes": 0})
        per_partition[part]["files"] += 1
        per_partition[part]["bytes"] += f.stat().st_size
    return {
        "parquet_file_count": len(files),
        "parquet_total_bytes": sum(sizes),
        "parquet_file_bytes_min": min(sizes) if sizes else 0,
        "parquet_file_bytes_max": max(sizes) if sizes else 0,
        "per_partition": per_partition,
    }


def profile_dataset(agency: str, fiscal_year: int, settings: Settings) -> ProfileResult:
    """Profile a published dataset; write JSON + Markdown under ``data/profiles/``."""
    dataset_id, published, manifest_path = _dataset_paths(agency, fiscal_year, settings)
    if not published.exists():
        raise FileNotFoundError(f"No published dataset at {published}")
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )

    con = duckdb.connect()
    glob = str(published).replace("'", "''")
    con.execute(
        "CREATE VIEW tx AS SELECT * FROM "
        f"read_parquet('{glob}/**/*.parquet', hive_partitioning=true)"
    )

    total_rows = con.execute("SELECT COUNT(*) FROM tx").fetchone()[0]
    distinct_tx = con.execute(
        "SELECT COUNT(DISTINCT contract_transaction_unique_key) FROM tx"
    ).fetchone()[0]
    distinct_awards = con.execute(
        "SELECT COUNT(DISTINCT contract_award_unique_key) FROM tx"
    ).fetchone()[0]
    distinct_recipients = con.execute("SELECT COUNT(DISTINCT recipient_uei) FROM tx").fetchone()[0]

    # Financial stats (exact decimals).
    fin = con.execute(
        """
        SELECT
            SUM(federal_action_obligation),
            SUM(CASE WHEN federal_action_obligation > 0 THEN federal_action_obligation ELSE 0 END),
            SUM(CASE WHEN federal_action_obligation < 0 THEN federal_action_obligation ELSE 0 END),
            SUM(CASE WHEN federal_action_obligation < 0 THEN 1 ELSE 0 END),
            SUM(CASE WHEN federal_action_obligation = 0 THEN 1 ELSE 0 END),
            MIN(federal_action_obligation),
            MAX(federal_action_obligation)
        FROM tx
        """
    ).fetchone()

    by_month = con.execute(
        """
        SELECT fiscal_month, COUNT(*) AS rows, SUM(federal_action_obligation) AS net
        FROM tx GROUP BY fiscal_month ORDER BY fiscal_month
        """
    ).fetchall()

    # Column-level stats via SUMMARIZE (approx_unique is approximate).
    summarize = con.execute("SUMMARIZE SELECT * FROM tx").fetchall()
    summ_cols = [d[0] for d in con.description]
    col_stats = []
    for row in summarize:
        rec = dict(zip(summ_cols, row, strict=False))
        col_stats.append(
            {
                "column": rec.get("column_name"),
                "type": rec.get("column_type"),
                "min": rec.get("min"),
                "max": rec.get("max"),
                "approx_unique": rec.get("approx_unique"),
                "null_percentage": rec.get("null_percentage"),
            }
        )
    con.close()

    storage = _storage_stats(published)
    zip_bytes = manifest.get("source_archive", {}).get("size_bytes")
    uncompressed = manifest.get("source_archive", {}).get("uncompressed_bytes")
    compression_ratio = (
        round(uncompressed / storage["parquet_total_bytes"], 2)
        if uncompressed and storage["parquet_total_bytes"]
        else None
    )

    profile = {
        "dataset_id": dataset_id,
        "profiled_at_utc": datetime.now(UTC).isoformat(),
        "agency": agency.upper(),
        "fiscal_year": fiscal_year,
        "dataset_statistics": {
            "published_rows": total_rows,
            "distinct_transaction_keys": distinct_tx,
            "distinct_awards": distinct_awards,
            "distinct_recipients": distinct_recipients,
            "column_count": manifest.get("column_count"),
            # Accounting recovered from the ingestion manifest (exact).
            "source_rows": manifest.get("totals", {}).get("source_rows"),
            "valid_rows": manifest.get("totals", {}).get("valid_rows"),
            "quarantined_rows": manifest.get("totals", {}).get("quarantined_rows"),
            "filtered_rows": manifest.get("totals", {}).get("filtered_rows"),
            "duplicate_rows": manifest.get("totals", {}).get("duplicate_rows"),
            "partition_count": manifest.get("partition_count"),
            "fiscal_months_present": [int(m) for m, _, _ in by_month],
        },
        "financial_statistics": {
            "net_obligation": str(fin[0]),
            "total_positive_obligation": str(fin[1]),
            "total_negative_obligation": str(fin[2]),
            "negative_obligation_count": int(fin[3]),
            "zero_obligation_count": int(fin[4]),
            "min_transaction_obligation": str(fin[5]),
            "max_transaction_obligation": str(fin[6]),
            "by_fiscal_month": [
                {"fiscal_month": int(m), "rows": int(r), "net_obligation": str(n)}
                for m, r, n in by_month
            ],
        },
        "storage_statistics": {
            "source_zip_bytes": zip_bytes,
            "uncompressed_csv_bytes": uncompressed,
            "parquet_total_bytes": storage["parquet_total_bytes"],
            "parquet_file_count": storage["parquet_file_count"],
            "parquet_file_bytes_min": storage["parquet_file_bytes_min"],
            "parquet_file_bytes_max": storage["parquet_file_bytes_max"],
            "source_to_parquet_compression_ratio": compression_ratio,
            "records_per_partition": dict(sorted(storage["per_partition"].items())),
            "schema_fingerprint": manifest.get("schema_fingerprint"),
        },
        "column_statistics_note": "approx_unique is approximate (DuckDB SUMMARIZE)",
        "column_statistics": col_stats,
    }

    settings.profiles_dir.mkdir(parents=True, exist_ok=True)
    json_path = settings.profiles_dir / f"{dataset_id}.json"
    json_path.write_text(json.dumps(profile, indent=2, default=str), encoding="utf-8")
    md_path = settings.profiles_dir / f"{dataset_id}.md"
    md_path.write_text(_render_markdown(profile), encoding="utf-8")

    log.info(
        "profile.written", extra={"context": {"dataset_id": dataset_id, "json": str(json_path)}}
    )
    return ProfileResult(
        dataset_id=dataset_id, json_path=json_path, markdown_path=md_path, profile=profile
    )


def _render_markdown(p: dict) -> str:
    ds = p["dataset_statistics"]
    fin = p["financial_statistics"]
    st = p["storage_statistics"]
    lines = [
        f"# Data Profile — {p['dataset_id']}",
        "",
        f"- Agency: **{p['agency']}**  ·  Fiscal year: **{p['fiscal_year']}**",
        f"- Profiled at: {p['profiled_at_utc']}",
        "",
        "## Dataset statistics",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Source rows | {ds['source_rows']} |",
        f"| Valid (published) rows | {ds['valid_rows']} |",
        f"| Quarantined rows | {ds['quarantined_rows']} |",
        f"| Filtered (out-of-scope) rows | {ds['filtered_rows']} |",
        f"| Duplicate rows | {ds['duplicate_rows']} |",
        f"| Distinct transaction keys | {ds['distinct_transaction_keys']} |",
        f"| Distinct awards | {ds['distinct_awards']} |",
        f"| Distinct recipients | {ds['distinct_recipients']} |",
        f"| Columns | {ds['column_count']} |",
        f"| Partitions | {ds['partition_count']} |",
        f"| Fiscal months present | {ds['fiscal_months_present']} |",
        "",
        "## Financial statistics (decimal-exact)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Net obligation | {fin['net_obligation']} |",
        f"| Total positive obligation | {fin['total_positive_obligation']} |",
        f"| Total negative obligation | {fin['total_negative_obligation']} |",
        f"| Negative obligation count | {fin['negative_obligation_count']} |",
        f"| Zero obligation count | {fin['zero_obligation_count']} |",
        f"| Min transaction obligation | {fin['min_transaction_obligation']} |",
        f"| Max transaction obligation | {fin['max_transaction_obligation']} |",
        "",
        "### Obligations by fiscal month",
        "",
        "| Fiscal month | Rows | Net obligation |",
        "|---|---|---|",
    ]
    for row in fin["by_fiscal_month"]:
        lines.append(f"| {row['fiscal_month']:02d} | {row['rows']} | {row['net_obligation']} |")
    lines += [
        "",
        "## Storage statistics",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Source ZIP bytes | {st['source_zip_bytes']} |",
        f"| Uncompressed CSV bytes | {st['uncompressed_csv_bytes']} |",
        f"| Parquet total bytes | {st['parquet_total_bytes']} |",
        f"| Parquet file count | {st['parquet_file_count']} |",
        f"| Source→Parquet compression ratio | {st['source_to_parquet_compression_ratio']} |",
        f"| Schema fingerprint | `{st['schema_fingerprint']}` |",
        "",
        f"Column-level statistics for all {len(p['column_statistics'])} columns are in "
        f"the JSON profile ({p['column_statistics_note']}).",
        "",
    ]
    return "\n".join(lines)
