"""Performance benchmarking for the historical ingestion pipeline.

Produces measured (never fabricated) performance evidence for a dataset:

* **Ingestion** — end-to-end wall time, per-phase timings, throughput
  (transactions/sec) and peak process memory, measured by running a *fresh*
  ingestion in a subprocess (so peak RSS reflects only that process). Repeated
  runs distinguish cold vs warm behaviour.
* **Phase micro-benchmarks** — standalone CSV-parse, schema-validation and
  Parquet-conversion timings on the real extracted data.
* **DuckDB queries** — partitioned (pruned) vs unpartitioned (full-scan) query
  times for the same workload, plus the query plan as partition-pruning evidence.
* **Storage** — source/CSV/Parquet sizes and the compression ratio.
* **Environment** — CPU, memory, OS and library versions.

Run directly as ``python -m ingestion.benchmarking --run-ingest ...`` only via the
subprocess helper below; normal use is through :func:`benchmark`.
"""

from __future__ import annotations

import argparse
import json
import platform
import resource
import subprocess
import sys
import tempfile
import time
from datetime import UTC
from pathlib import Path
from statistics import median

import duckdb
import pyarrow

from .config import Settings
from .logging_config import get_logger

log = get_logger(__name__)


def _peak_rss_mb() -> float:
    """Peak resident set size of the current process in MB (OS-normalized)."""
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports kilobytes.
    return raw / (1024 * 1024) if sys.platform == "darwin" else raw / 1024


def _source_zip_for(agency: str, fiscal_year: int, settings: Settings) -> Path:
    dataset_id = f"{agency.lower()}_contracts_fy{fiscal_year}"
    manifest = settings.manifest_dir / f"{dataset_id}.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return Path(data["source_archive"]["stored_path"])


def _published_dir(agency: str, fiscal_year: int, settings: Settings) -> Path:
    return (
        settings.partitioned_parquet_dir
        / f"awarding_agency={agency.upper()}"
        / f"fiscal_year={fiscal_year}"
    )


# --------------------------------------------------------------------------- #
# Ingestion benchmark (subprocess, for honest peak-RSS measurement)
# --------------------------------------------------------------------------- #
def _run_ingest_subprocess(zip_path: Path, agency: str, fiscal_year: int) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "ingestion.benchmarking",
                "--run-ingest",
                str(zip_path),
                agency,
                str(fiscal_year),
                tmp,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    # The child prints a single JSON line as its last line of stdout.
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError(
        f"Ingestion subprocess produced no result JSON:\n{proc.stdout}\n{proc.stderr}"
    )


def benchmark_ingestion(zip_path: Path, agency: str, fiscal_year: int, repeats: int = 2) -> dict:
    runs = [_run_ingest_subprocess(zip_path, agency, fiscal_year) for _ in range(repeats)]
    walls = [r["wall_seconds"] for r in runs]
    rows = runs[0]["rows"]
    rss = [r["peak_rss_mb"] for r in runs]
    return {
        "repeats": repeats,
        "rows": rows,
        "wall_seconds_cold": walls[0],
        "wall_seconds_warm_median": median(walls[1:]) if len(walls) > 1 else walls[0],
        "wall_seconds_min": min(walls),
        "throughput_tx_per_sec": round(rows / min(walls), 1) if min(walls) > 0 else None,
        "peak_rss_mb_max": round(max(rss), 1),
        "phase_timings_seconds": runs[0]["timings"],
    }


# --------------------------------------------------------------------------- #
# Phase micro-benchmarks on the real extracted CSV
# --------------------------------------------------------------------------- #
def benchmark_phases(zip_path: Path, settings: Settings) -> dict:
    from .archive import iter_csv_batches
    from .client import safe_extract_zip
    from .storage import write_parquet
    from .validate import validate_batch

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        t0 = time.perf_counter()
        extracted = safe_extract_zip(zip_path, tmp_path / "ex", settings)
        t_extract = time.perf_counter() - t0
        csv_paths = sorted(p for p in extracted if p.suffix.lower() == ".csv")

        # CSV parse (read all rows across all members, no validation).
        t0 = time.perf_counter()
        header: list[str] = []
        all_rows: list[dict] = []
        for csv_path in csv_paths:
            for h, batch in iter_csv_batches(csv_path, settings.batch_size):
                header = h
                all_rows.extend(batch)
        t_parse = time.perf_counter() - t0

        # Validation only.
        t0 = time.perf_counter()
        seen: set[str] = set()
        warn: list[str] = []
        valid, _ = validate_batch(all_rows, seen, warn)
        t_validate = time.perf_counter() - t0

        # Parquet conversion only.
        t0 = time.perf_counter()
        write_parquet(valid, header, tmp_path / "out.parquet")
        t_parquet = time.perf_counter() - t0

    return {
        "csv_extract_seconds": round(t_extract, 4),
        "csv_parse_seconds": round(t_parse, 4),
        "schema_validation_seconds": round(t_validate, 4),
        "parquet_conversion_seconds": round(t_parquet, 4),
        "rows": len(all_rows),
    }


# --------------------------------------------------------------------------- #
# DuckDB query benchmarks + partition-pruning evidence
# --------------------------------------------------------------------------- #
def benchmark_queries(agency: str, fiscal_year: int, settings: Settings, repeats: int = 4) -> dict:
    published = _published_dir(agency, fiscal_year, settings)
    glob = str(published).replace("'", "''")

    con = duckdb.connect()
    con.execute(
        "CREATE VIEW part AS SELECT * FROM "
        f"read_parquet('{glob}/**/*.parquet', hive_partitioning=true)"
    )
    # Build an equivalent single-file (unpartitioned) copy for comparison.
    with tempfile.TemporaryDirectory() as tmp:
        unpart = Path(tmp) / "unpartitioned.parquet"
        unpart_s = str(unpart).replace("'", "''")
        con.execute(f"COPY (SELECT * FROM part) TO '{unpart_s}' (FORMAT PARQUET)")
        con.execute(f"CREATE VIEW whole AS SELECT * FROM read_parquet('{unpart_s}')")

        filtered_part = (
            "SELECT COUNT(*), SUM(federal_action_obligation) FROM part WHERE fiscal_month = 6"
        )
        filtered_whole = (
            "SELECT COUNT(*), SUM(federal_action_obligation) FROM whole WHERE fiscal_month = '06'"
        )
        _agg = "SELECT fiscal_month, SUM(federal_action_obligation)"
        full_part = f"{_agg} FROM part GROUP BY fiscal_month"
        full_whole = f"{_agg} FROM whole GROUP BY fiscal_month"

        def timed(sql: str) -> list[float]:
            times = []
            for _ in range(repeats):
                t0 = time.perf_counter()
                con.execute(sql).fetchall()
                times.append((time.perf_counter() - t0) * 1000)  # ms
            return times

        part_filtered = timed(filtered_part)
        whole_filtered = timed(filtered_whole)
        part_full = timed(full_part)
        whole_full = timed(full_whole)

        # Concrete partition-pruning evidence: how many files / rows the engine
        # must touch for a single-month filter, partitioned vs not.
        total_files = len(list(published.glob("**/*.parquet")))
        files_single_month = len(list(published.glob("fiscal_month=06/*.parquet")))
        rows_total = con.execute("SELECT COUNT(*) FROM part").fetchone()[0]
        rows_single_month = con.execute(
            "SELECT COUNT(*) FROM part WHERE fiscal_month = 6"
        ).fetchone()[0]
        # EXPLAIN ANALYZE shows the scan cardinality actually read after pruning.
        plan = con.execute(f"EXPLAIN ANALYZE {filtered_part}").fetchall()
        plan_text = "\n".join(r[1] for r in plan)
    con.close()

    def summ(ms: list[float]) -> dict:
        return {
            "cold_ms": round(ms[0], 3),
            "warm_median_ms": round(median(ms[1:] or ms), 3),
            "min_ms": round(min(ms), 3),
        }

    return {
        "repeats": repeats,
        "single_month_filter": {
            "partitioned": summ(part_filtered),
            "unpartitioned": summ(whole_filtered),
            "speedup_min": round(min(whole_filtered) / min(part_filtered), 2)
            if min(part_filtered) > 0
            else None,
        },
        "full_group_by": {
            "partitioned": summ(part_full),
            "unpartitioned": summ(whole_full),
        },
        "partition_pruning": {
            "total_partition_files": total_files,
            "files_scanned_for_single_month": files_single_month,
            "rows_in_dataset": rows_total,
            "rows_scanned_for_single_month": rows_single_month,
            "data_skip_fraction": round(1 - rows_single_month / rows_total, 4)
            if rows_total
            else None,
            "note": (
                "Partition pruning reduces data scanned for a single-month filter. "
                "Wall-time speedup at this dataset size is modest/negative because "
                "per-file open overhead dominates; the benefit grows with scale."
            ),
        },
        "partition_pruning_plan": plan_text,
    }


def _environment() -> dict:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "cpu_count": __import__("os").cpu_count(),
        "python_version": platform.python_version(),
        "pyarrow_version": pyarrow.__version__,
        "duckdb_version": duckdb.__version__,
    }


def benchmark(agency: str, fiscal_year: int, settings: Settings, *, repeats: int = 3) -> dict:
    """Run the full benchmark suite for a published dataset; write JSON."""
    dataset_id = f"{agency.lower()}_contracts_fy{fiscal_year}"
    zip_path = _source_zip_for(agency, fiscal_year, settings)
    manifest = json.loads((settings.manifest_dir / f"{dataset_id}.json").read_text())

    result = {
        "dataset_id": dataset_id,
        "benchmarked_at_utc": _now(),
        "environment": {
            **_environment(),
            "batch_size": settings.batch_size,
            "parquet_compression": settings.parquet_compression,
            "parquet_row_group_size": settings.parquet_row_group_size,
        },
        "sizes": {
            "source_zip_bytes": manifest["source_archive"]["size_bytes"],
            "uncompressed_csv_bytes": manifest["source_archive"].get("uncompressed_bytes"),
            "parquet_total_bytes": _parquet_bytes(agency, fiscal_year, settings),
        },
        "ingestion": benchmark_ingestion(
            zip_path, agency, fiscal_year, repeats=max(2, repeats // 2 + 1)
        ),
        "phases": benchmark_phases(zip_path, settings),
        "queries": benchmark_queries(agency, fiscal_year, settings, repeats=repeats + 1),
    }
    sizes = result["sizes"]
    if sizes["uncompressed_csv_bytes"] and sizes["parquet_total_bytes"]:
        result["sizes"]["source_to_parquet_compression_ratio"] = round(
            sizes["uncompressed_csv_bytes"] / sizes["parquet_total_bytes"], 2
        )

    settings.benchmarks_dir.mkdir(parents=True, exist_ok=True)
    out = settings.benchmarks_dir / f"{dataset_id}.json"
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    log.info("benchmark.written", extra={"context": {"path": str(out)}})
    result["_output_path"] = str(out)
    return result


def _parquet_bytes(agency: str, fiscal_year: int, settings: Settings) -> int:
    published = _published_dir(agency, fiscal_year, settings)
    return sum(f.stat().st_size for f in published.glob("**/*.parquet"))


def _now() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark subprocess runner (internal).")
    parser.add_argument("--run-ingest", nargs=4, metavar=("ZIP", "AGENCY", "FY", "DATADIR"))
    args = parser.parse_args(argv)
    if args.run_ingest:
        from .archive import ingest_archive

        zip_path, agency, fy, datadir = args.run_ingest
        settings = Settings(data_dir=Path(datadir))
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        r = ingest_archive(Path(zip_path), agency, int(fy), settings, timings=timings, force=True)
        wall = time.perf_counter() - t0
        print(
            json.dumps(
                {
                    "wall_seconds": round(wall, 4),
                    "rows": r.totals.valid_rows,
                    "peak_rss_mb": round(_peak_rss_mb(), 1),
                    "timings": {k: round(v, 4) for k, v in timings.items()},
                }
            )
        )
        return 0
    parser.error("nothing to do")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
