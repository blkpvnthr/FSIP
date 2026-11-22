"""Command-line interface for the ingestion pipeline.

Examples
--------
Extract NASA prime contracts for October 2025 (the Milestone-1 default)::

    fsdw extract --agency NASA --start 2025-10-01 --end 2025-10-31

Choose award types, a custom data directory, or force a re-run::

    fsdw extract --agency NASA --start 2025-10-01 --end 2025-10-31 \\
        --award-types contracts_and_idvs --data-dir ./data --force

List the agencies the pipeline knows how to resolve::

    fsdw agencies
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Settings
from .exceptions import IngestionError
from .logging_config import configure_logging, get_logger
from .models import AGENCIES, AwardTypeGroup, ExtractRequest

log = get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fsdw",
        description="Federal Spending Data Warehouse — USAspending.gov ingestion.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser(
        "extract", help="Extract prime award transactions for an agency + date range."
    )
    extract.add_argument(
        "--agency",
        required=True,
        help=f"Agency key (known: {', '.join(sorted(AGENCIES))}).",
    )
    extract.add_argument("--start", required=True, help="Start date (YYYY-MM-DD).")
    extract.add_argument("--end", required=True, help="End date (YYYY-MM-DD).")
    extract.add_argument(
        "--award-types",
        default=AwardTypeGroup.CONTRACTS.value,
        choices=[g.value for g in AwardTypeGroup],
        help="Award-type group to request (default: contracts).",
    )
    extract.add_argument(
        "--date-type",
        default="action_date",
        choices=["action_date", "last_modified_date"],
        help="Which date field the date range filters on.",
    )
    extract.add_argument(
        "--agency-type",
        default="awarding",
        choices=["awarding", "funding"],
        help="Filter on the awarding or funding agency (default: awarding).",
    )
    extract.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override the data-lake root directory (default: from env / 'data').",
    )
    extract.add_argument(
        "--force",
        action="store_true",
        help="Re-run even if outputs already exist for this request.",
    )

    sub.add_parser("agencies", help="List known agencies and their toptier codes.")
    return parser


def _run_extract(args: argparse.Namespace, settings: Settings) -> int:
    # Import here so `fsdw agencies` and `--help` do not pay the heavy
    # (pyarrow/requests) import cost.
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
    print(f"  raw archive      : {result.raw_archive_path}")
    print(f"  source CSV       : {result.source_csv_path}")
    print(f"  parquet          : {result.parquet_path}")
    print(f"  manifest         : {result.manifest_path}")
    print(f"  quarantine       : {result.quarantine_path or '(none)'}")
    print(f"  valid rows       : {result.valid_row_count}")
    print(f"  quarantined rows : {result.quarantined_row_count}")
    print(f"  obligation total : {result.obligation_total}")
    return 0


def _run_agencies() -> int:
    print("Known agencies:")
    for key, meta in sorted(AGENCIES.items()):
        print(f"  {key:6s} toptier_code={meta['toptier_code']}  {meta['name']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if getattr(args, "data_dir", None) is not None:
        from dataclasses import replace

        settings = replace(settings, data_dir=args.data_dir)
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    try:
        if args.command == "extract":
            return _run_extract(args, settings)
        if args.command == "agencies":
            return _run_agencies()
    except IngestionError as exc:
        log.error("cli.ingestion_error", extra={"context": {"error": str(exc)}})
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
