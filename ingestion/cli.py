"""Command-line interface for the ingestion pipeline.

Milestone 1 commands (unchanged)::

    fsdw extract --agency NASA --start 2025-10-01 --end 2025-10-31
    fsdw agencies

Milestone 2 historical commands::

    fsdw ingest-archive --path <zip> --agency NASA --fiscal-year 2026
    fsdw backfill --agency NASA --start-fy 2022 --end-fy 2026 [--download]
    fsdw list-datasets
    fsdw profile --agency NASA --fiscal-year 2026
    fsdw reconcile --agency NASA --fiscal-year 2026
    fsdw benchmark --agency NASA --fiscal-year 2026
    fsdw compare-schemas --agency NASA --from-fy 2025 --to-fy 2026

Common options where applicable: ``--data-dir``, ``--batch-size``,
``--compression``, ``--row-group-size``, ``--dry-run``, ``--force``, ``--resume``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .config import Settings
from .exceptions import IngestionError
from .logging_config import configure_logging, get_logger
from .models import AGENCIES, AwardTypeGroup, ExtractRequest

log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def _add_storage_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--data-dir", type=Path, default=None, help="Data-lake root override.")
    p.add_argument("--batch-size", type=int, default=None, help="Rows per streaming batch.")
    p.add_argument(
        "--compression",
        default=None,
        choices=["zstd", "snappy", "gzip", "none"],
        help="Parquet compression codec.",
    )
    p.add_argument("--row-group-size", type=int, default=None, help="Parquet row-group size.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fsdw",
        description="Federal Spending Data Warehouse — USAspending.gov ingestion.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    known = ", ".join(sorted(AGENCIES))

    # ---- Milestone 1: extract ----
    extract = sub.add_parser("extract", help="Extract award transactions (API, date range).")
    extract.add_argument("--agency", required=True, help=f"Agency key (known: {known}).")
    extract.add_argument("--start", required=True, help="Start date (YYYY-MM-DD).")
    extract.add_argument("--end", required=True, help="End date (YYYY-MM-DD).")
    extract.add_argument(
        "--award-types",
        default=AwardTypeGroup.CONTRACTS.value,
        choices=[g.value for g in AwardTypeGroup],
        help="Award-type group (default: contracts).",
    )
    extract.add_argument(
        "--date-type",
        default="action_date",
        choices=["action_date", "last_modified_date"],
        help="Date field the range filters on.",
    )
    extract.add_argument(
        "--agency-type",
        default="awarding",
        choices=["awarding", "funding"],
        help="Awarding or funding agency (default: awarding).",
    )
    extract.add_argument("--data-dir", type=Path, default=None, help="Data-lake root override.")
    extract.add_argument("--force", action="store_true", help="Re-run even if outputs exist.")

    sub.add_parser("agencies", help="List known agencies and their toptier codes.")

    # ---- Milestone 2: ingest-archive ----
    ia = sub.add_parser("ingest-archive", help="Ingest a full contract archive ZIP.")
    ia.add_argument("--path", type=Path, required=True, help="Path to the archive ZIP.")
    ia.add_argument("--agency", required=True, help=f"Agency key (known: {known}).")
    ia.add_argument("--fiscal-year", type=int, required=True, help="Federal fiscal year.")
    ia.add_argument("--dry-run", action="store_true", help="Inspect only; do not publish.")
    ia.add_argument(
        "--force", action="store_true", help="Overwrite a conflicting published dataset."
    )
    ia.add_argument(
        "--resume", action="store_true", help="Skip if already published (same archive)."
    )
    _add_storage_opts(ia)

    # ---- Milestone 2: backfill ----
    bf = sub.add_parser("backfill", help="Ingest local archives across a fiscal-year range.")
    bf.add_argument("--agency", required=True, help=f"Agency key (known: {known}).")
    bf.add_argument("--start-fy", type=int, required=True, help="First fiscal year.")
    bf.add_argument("--end-fy", type=int, required=True, help="Last fiscal year (inclusive).")
    bf.add_argument(
        "--download",
        action="store_true",
        help="Explicitly allow downloading fiscal years missing locally (network).",
    )
    bf.add_argument("--dry-run", action="store_true", help="Report plan; do not ingest/download.")
    bf.add_argument(
        "--force", action="store_true", help="Overwrite conflicting published datasets."
    )
    _add_storage_opts(bf)

    # ---- Milestone 2: list-datasets ----
    ld = sub.add_parser("list-datasets", help="Inventory local datasets.")
    ld.add_argument("--data-dir", type=Path, default=None, help="Data-lake root override.")
    ld.add_argument("--json", action="store_true", help="Emit JSON inventory.")
    ld.add_argument("--no-checksums", action="store_true", help="Skip SHA-256 (faster).")

    # ---- Milestone 2: profile ----
    pf = sub.add_parser("profile", help="Profile a published dataset (JSON + Markdown).")
    pf.add_argument("--agency", required=True, help=f"Agency key (known: {known}).")
    pf.add_argument("--fiscal-year", type=int, required=True, help="Federal fiscal year.")
    pf.add_argument("--data-dir", type=Path, default=None, help="Data-lake root override.")

    # ---- Milestone 2: reconcile ----
    rc = sub.add_parser("reconcile", help="Reconcile a published dataset vs its manifest.")
    rc.add_argument("--agency", required=True, help=f"Agency key (known: {known}).")
    rc.add_argument("--fiscal-year", type=int, required=True, help="Federal fiscal year.")
    rc.add_argument("--data-dir", type=Path, default=None, help="Data-lake root override.")

    # ---- Milestone 2: benchmark ----
    bm = sub.add_parser("benchmark", help="Benchmark ingestion and queries for a dataset.")
    bm.add_argument("--agency", required=True, help=f"Agency key (known: {known}).")
    bm.add_argument("--fiscal-year", type=int, required=True, help="Federal fiscal year.")
    bm.add_argument("--repeats", type=int, default=3, help="Query repeats (cold+warm).")
    bm.add_argument("--data-dir", type=Path, default=None, help="Data-lake root override.")

    # ---- Milestone 2: compare-schemas ----
    cs = sub.add_parser("compare-schemas", help="Compare recorded schemas across fiscal years.")
    cs.add_argument("--agency", required=True, help=f"Agency key (known: {known}).")
    cs.add_argument("--from-fy", type=int, required=True, help="Baseline fiscal year.")
    cs.add_argument("--to-fy", type=int, required=True, help="Comparison fiscal year.")
    cs.add_argument("--data-dir", type=Path, default=None, help="Data-lake root override.")

    return parser


# --------------------------------------------------------------------------- #
# Settings assembly (applies common overrides)
# --------------------------------------------------------------------------- #
def _settings_from_args(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    overrides: dict = {}
    if getattr(args, "data_dir", None) is not None:
        overrides["data_dir"] = args.data_dir
    if getattr(args, "batch_size", None) is not None:
        overrides["batch_size"] = args.batch_size
    if getattr(args, "compression", None) is not None:
        overrides["parquet_compression"] = args.compression
    if getattr(args, "row_group_size", None) is not None:
        overrides["parquet_row_group_size"] = args.row_group_size
    return replace(settings, **overrides) if overrides else settings


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #
def _run_extract(args: argparse.Namespace, settings: Settings) -> int:
    from .extract import run_extraction

    request = ExtractRequest(
        agency=args.agency,
        start_date=args.start,
        end_date=args.end,
        award_type_group=AwardTypeGroup(args.award_types),
        date_type=args.date_type,
        agency_type=args.agency_type,
    )
    result = run_extraction(request, settings, force=args.force)
    status = "SKIPPED (already present)" if result.skipped else "COMPLETED"
    print(f"\nExtraction {status} for '{result.request_slug}':")
    print(f"  parquet          : {result.parquet_path}")
    print(f"  manifest         : {result.manifest_path}")
    print(f"  valid rows       : {result.valid_row_count}")
    print(f"  quarantined rows : {result.quarantined_row_count}")
    print(f"  obligation total : {result.obligation_total}")
    return 0


def _run_agencies() -> int:
    print("Known agencies:")
    for key, meta in sorted(AGENCIES.items()):
        print(f"  {key:6s} toptier_code={meta['toptier_code']}  {meta['name']}")
    return 0


def _print_ingest_result(r) -> None:
    print(f"\nArchive ingestion '{r.dataset_id}': {r.status.upper()}")
    if r.inspection is not None:
        print(f"  archive sha256   : {r.inspection.zip_sha256}")
        print(f"  csv members      : {r.inspection.csv_members}")
    if r.status == "dry_run":
        return
    t = r.totals
    print(f"  source rows      : {t.source_rows}")
    print(f"  valid (published): {t.valid_rows}")
    print(f"  quarantined      : {t.quarantined_rows}  (duplicates: {t.duplicate_rows})")
    print(f"  filtered (scope) : {t.filtered_rows}")
    print(f"  net obligation   : {t.net_obligation}")
    print(f"  neg / zero       : {t.negative_count} / {t.zero_count}")
    print(f"  partitions/files : {r.partition_count} / {r.parquet_file_count}")
    if r.reconciliation is not None:
        print(f"  reconciliation   : {'PASS' if r.reconciliation.passed else 'FAIL'}")
    print(f"  idempotent       : {r.idempotent}   conflict: {r.conflict}")
    print(f"  published path   : {r.published_path or '(not published)'}")
    print(f"  quarantine       : {r.quarantine_path or '(none)'}")
    print(f"  manifest         : {r.manifest_path}")


def _run_ingest_archive(args: argparse.Namespace, settings: Settings) -> int:
    from .archive import ingest_archive

    r = ingest_archive(
        args.path,
        args.agency,
        args.fiscal_year,
        settings,
        dry_run=args.dry_run,
        force=args.force,
        resume=args.resume,
    )
    _print_ingest_result(r)
    # Non-zero exit if reconciliation failed or a conflict blocked publication.
    if r.status in {"reconciliation_failed", "conflict"}:
        return 3
    return 0


def _find_local_archive(agency: str, fiscal_year: int, settings: Settings) -> Path | None:
    from .agencies import resolve

    key = resolve(agency).key.lower()
    fy_dir = settings.archive_root / key / f"fy{fiscal_year}"
    zips = sorted(fy_dir.glob("*.zip")) if fy_dir.exists() else []
    return zips[-1] if zips else None


def _run_backfill(args: argparse.Namespace, settings: Settings) -> int:
    from .archive import download_full_archive, ingest_archive

    print(
        f"Backfill {args.agency} FY{args.start_fy}..FY{args.end_fy} "
        f"(download={'yes' if args.download else 'no'}, dry_run={args.dry_run})"
    )
    overall_ok = True
    for fy in range(args.start_fy, args.end_fy + 1):
        local = _find_local_archive(args.agency, fy, settings)
        if local is None:
            if not args.download:
                print(f"  FY{fy}: MISSING locally — skipped (pass --download to fetch).")
                continue
            if args.dry_run:
                print(f"  FY{fy}: MISSING — would download (dry-run, skipped).")
                continue
            print(f"  FY{fy}: downloading full archive ...")
            local = download_full_archive(args.agency, fy, settings)
        if args.dry_run:
            r = ingest_archive(local, args.agency, fy, settings, dry_run=True)
            print(f"  FY{fy}: dry-run inspect OK ({local.name})")
            continue
        r = ingest_archive(local, args.agency, fy, settings, force=args.force)
        print(
            f"  FY{fy}: {r.status} — {r.totals.valid_rows} rows, "
            f"{r.partition_count} partitions, net {r.totals.net_obligation}"
        )
        overall_ok = overall_ok and r.status in {"published", "skipped"}
    return 0 if overall_ok else 3


def _run_list_datasets(args: argparse.Namespace, settings: Settings) -> int:
    from .discovery import discover_datasets, inventory_as_dicts

    entries = discover_datasets(settings, compute_checksums=not args.no_checksums)
    if args.json:
        print(json.dumps(inventory_as_dicts(entries), indent=2))
        return 0
    if not entries:
        print("No local datasets discovered.")
        return 0
    print(f"{'AGENCY':7} {'FY':6} {'TYPE':5} {'STATUS':16} {'SIZE':>12}  SOURCE")
    for e in entries:
        print(
            f"{(e.agency or '-'):7} {e.fiscal_year or '-'!s:6} {e.archive_type:5} "
            f"{e.processing_status:16} {e.size_bytes:>12}  {e.source_path}"
        )
    return 0


def _run_profile(args: argparse.Namespace, settings: Settings) -> int:
    from .profiling import profile_dataset

    result = profile_dataset(args.agency, args.fiscal_year, settings)
    ds = result.profile["dataset_statistics"]
    fin = result.profile["financial_statistics"]
    print(f"\nProfile '{result.dataset_id}':")
    print(f"  published rows     : {ds['published_rows']}")
    print(f"  distinct recipients: {ds['distinct_recipients']}")
    print(f"  distinct awards    : {ds['distinct_awards']}")
    print(f"  fiscal months      : {ds['fiscal_months_present']}")
    print(f"  net obligation     : {fin['net_obligation']}")
    print(f"  json               : {result.json_path}")
    print(f"  markdown           : {result.markdown_path}")
    return 0


def _run_reconcile(args: argparse.Namespace, settings: Settings) -> int:
    from .reconciliation import reconcile_published

    result = reconcile_published(args.agency, args.fiscal_year, settings)
    print(f"\nReconciliation for {args.agency} FY{args.fiscal_year}:")
    for c in result.checks:
        mark = "PASS" if c.passed else "FAIL"
        print(f"  [{mark}] {c.name}  (expected={c.expected} actual={c.actual})")
    print("RESULT:", "PASSED" if result.passed else "FAILED")
    return 0 if result.passed else 3


def _run_benchmark(args: argparse.Namespace, settings: Settings) -> int:
    from .benchmarking import benchmark

    result = benchmark(args.agency, args.fiscal_year, settings, repeats=args.repeats)
    ing = result["ingestion"]
    q = result["queries"]["partition_pruning"]
    print(f"\nBenchmark '{result['dataset_id']}':")
    print(f"  ingestion wall (min) : {ing['wall_seconds_min']} s")
    print(f"  throughput           : {ing['throughput_tx_per_sec']} tx/s")
    print(f"  peak RSS             : {ing['peak_rss_mb_max']} MB")
    print(
        f"  pruning: scans {q['rows_scanned_for_single_month']}/{q['rows_in_dataset']} rows "
        f"({q['files_scanned_for_single_month']}/{q['total_partition_files']} files)"
    )
    print(f"  benchmark json       : {result['_output_path']}")
    return 0


def _run_compare_schemas(args: argparse.Namespace, settings: Settings) -> int:
    from .schema_registry import compare_schemas, load_schema

    a = load_schema(f"{args.agency.lower()}_contracts_fy{args.from_fy}", settings.schemas_dir)
    b = load_schema(f"{args.agency.lower()}_contracts_fy{args.to_fy}", settings.schemas_dir)
    diff = compare_schemas(a, b)
    print(f"\nSchema comparison {a.dataset_id} -> {b.dataset_id}:")
    print(f"  identical        : {diff.identical}")
    print(f"  classification   : {diff.classification}")
    print(f"  added columns    : {diff.added_columns}")
    print(f"  removed columns  : {diff.removed_columns}")
    print(f"  reordered        : {diff.reordered}")
    print(f"  type changes     : {diff.type_changes}")
    print(f"  missing required : {diff.missing_required}")
    return 0


_HANDLERS = {
    "extract": _run_extract,
    "ingest-archive": _run_ingest_archive,
    "backfill": _run_backfill,
    "list-datasets": _run_list_datasets,
    "profile": _run_profile,
    "reconcile": _run_reconcile,
    "benchmark": _run_benchmark,
    "compare-schemas": _run_compare_schemas,
}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = _settings_from_args(args)
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    try:
        if args.command == "agencies":
            return _run_agencies()
        handler = _HANDLERS.get(args.command)
        if handler is not None:
            return handler(args, settings)
    except (IngestionError, KeyError, FileNotFoundError) as exc:
        log.error("cli.error", extra={"context": {"error": str(exc)}})
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
