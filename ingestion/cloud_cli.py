"""`fsdw cloud ...` command group and cloud-aware exit-code handling.

Kept separate from :mod:`ingestion.cli` to isolate the (optional) cloud surface.
Exit codes distinguish failure classes for scripting:

* 0  success
* 2  configuration / usage error
* 4  authentication error (no/invalid AWS credentials)
* 5  missing infrastructure (bucket/object not found)
* 6  publication or catalog-pointer conflict
* 7  integrity / reconciliation failure
* 8  network error
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import replace
from pathlib import Path

from .config import Settings
from .exceptions import (
    CloudPublicationError,
    IntegrityError,
    ObjectNotFoundError,
    PreconditionFailedError,
    StorageConfigError,
)
from .logging_config import get_logger

log = get_logger(__name__)

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_AUTH = 4
EXIT_MISSING_INFRA = 5
EXIT_CONFLICT = 6
EXIT_INTEGRITY = 7
EXIT_NETWORK = 8


def add_cloud_parser(subparsers) -> None:
    """Register the ``cloud`` command group on the main CLI subparsers."""
    cloud = subparsers.add_parser("cloud", help="AWS S3 cloud data-lake operations.")
    csub = cloud.add_subparsers(dest="cloud_command", required=True)

    def _common(p):
        p.add_argument("--data-dir", type=Path, default=None, help="Data-lake root override.")
        p.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    _common(csub.add_parser("check", help="Validate cloud configuration and connectivity."))
    _common(csub.add_parser("inventory", help="Local inventory for migration planning."))
    _common(
        csub.add_parser("audit-integrity", help="Full-lake cloud transaction-uniqueness audit.")
    )
    _common(csub.add_parser("reconcile", help="Reconcile every published cloud dataset."))
    _common(
        csub.add_parser("list-snapshots", help="List catalog snapshots and the active pointer.")
    )
    _common(csub.add_parser("estimate-cost", help="Offline AWS S3 cost estimate."))

    for name, helptext in [
        ("plan-sync", "Dry-run plan for publishing one dataset."),
        ("sync", "Publish (migrate) a dataset to the cloud lake."),
        ("verify", "Reconcile one published cloud dataset vs its local manifest."),
    ]:
        p = csub.add_parser(name, help=helptext)
        p.add_argument("--agency", required=(name != "sync"), help="Agency key.")
        p.add_argument("--fiscal-year", type=int, required=(name != "sync"), help="Fiscal year.")
        if name == "sync":
            p.add_argument("--all", action="store_true", help="Sync all datasets (planned order).")
            p.add_argument(
                "--force", action="store_true", help="Replace this dataset's own snapshot."
            )
            p.add_argument("--dry-run", action="store_true", help="Plan only; write nothing.")
        _common(p)

    bm = csub.add_parser("benchmark", help="Benchmark cloud publication + queries.")
    bm.add_argument("--agency", default="NASA", help="Agency key (default NASA).")
    bm.add_argument("--fiscal-year", type=int, default=2026, help="Fiscal year (default 2026).")
    _common(bm)

    rb = csub.add_parser("rollback", help="Roll the active catalog pointer back to a version.")
    rb.add_argument("--to-version", type=int, help="Catalog version to restore.")
    rb.add_argument(
        "--snapshot-id", help="Restore the catalog version that last held this snapshot."
    )
    _common(rb)


def _settings(args: argparse.Namespace) -> Settings:
    s = Settings.from_env()
    if getattr(args, "data_dir", None) is not None:
        s = replace(s, data_dir=args.data_dir)
    return s


def run_cloud(args: argparse.Namespace, settings: Settings) -> int:
    """Dispatch a ``cloud`` subcommand, mapping failures to typed exit codes."""
    cmd = args.cloud_command
    try:
        return _DISPATCH[cmd](args, settings)
    except StorageConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=_stderr())
        return EXIT_CONFIG
    except ObjectNotFoundError as exc:
        print(f"MISSING: {exc}", file=_stderr())
        return EXIT_MISSING_INFRA
    except (PreconditionFailedError, CloudPublicationError) as exc:
        print(f"CONFLICT: {exc}", file=_stderr())
        return EXIT_CONFLICT
    except IntegrityError as exc:
        print(f"INTEGRITY: {exc}", file=_stderr())
        return EXIT_INTEGRITY
    except Exception as exc:
        return _handle_boto_error(exc)


def _stderr():
    import sys

    return sys.stderr


def _handle_boto_error(exc: Exception) -> int:
    name = type(exc).__name__
    if name in {"NoCredentialsError", "PartialCredentialsError", "SSOTokenLoadError"}:
        print(f"AUTH ERROR: {exc}", file=_stderr())
        return EXIT_AUTH
    if name in {"EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError"}:
        print(f"NETWORK ERROR: {exc}", file=_stderr())
        return EXIT_NETWORK
    if name == "ClientError":
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        if code in {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"}:
            print(f"AUTH ERROR: {exc}", file=_stderr())
            return EXIT_AUTH
        if code in {"NoSuchBucket", "404", "NoSuchKey"}:
            print(f"MISSING: {exc}", file=_stderr())
            return EXIT_MISSING_INFRA
    raise exc


def _emit(obj: dict, as_json: bool) -> None:
    print(json.dumps(obj, indent=2, default=str)) if as_json else _pretty(obj)


def _pretty(obj: dict) -> None:
    for k, v in obj.items():
        print(f"  {k}: {v}")


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
def _cloud_check(args, settings: Settings) -> int:
    settings.validate_cloud()
    info = {
        "storage_backend": settings.storage_backend,
        "aws_region": settings.aws_region,
        "aws_profile": settings.aws_profile or "(default chain)",
        "s3_bucket": settings.s3_bucket or "(unset)",
        "s3_prefix": settings.s3_prefix,
        "s3_encryption": settings.s3_encryption,
    }
    if settings.storage_backend == "s3":
        from .storage_backends import get_storage_backend

        backend = get_storage_backend(settings)
        backend.list("")  # lightweight connectivity/permission probe
        info["connectivity"] = "ok"
    else:
        info["connectivity"] = "local backend (no AWS calls)"
    print("Cloud check:")
    _emit(info, args.json)
    return EXIT_OK


def _cloud_inventory(args, settings: Settings) -> int:
    from .cloud.inventory import build_inventory

    inv = build_inventory(settings)
    _emit(inv.to_dict() if args.json else inv.to_dict()["totals"], args.json)
    if not args.json:
        for d in inv.datasets:
            print(
                f"  {d.dataset_id}: {d.transaction_count} tx, "
                f"{d.parquet_file_count} parts, {d.transfer_bytes} B, ${d.net_obligation}"
            )
    return EXIT_OK


def _cloud_plan_sync(args, settings: Settings) -> int:
    from .cloud.migration import migrate_dataset

    r = migrate_dataset(settings, args.agency, args.fiscal_year, dry_run=True)
    print(f"Plan-sync {r.dataset_id}: {r.status}")
    _emit(r.plan, args.json)
    return EXIT_OK


def _cloud_sync(args, settings: Settings) -> int:
    from .cloud.migration import migrate_dataset, migrate_lake

    if getattr(args, "all", False):
        results = migrate_lake(settings, dry_run=args.dry_run, force=args.force)
        for r in results:
            print(f"  {r.dataset_id}: {r.status} (rows={r.transaction_count})")
        bad = [
            r
            for r in results
            if r.status
            in {"integrity_conflict", "reconciliation_failed", "conflict", "pointer_conflict"}
        ]
        return EXIT_INTEGRITY if bad else EXIT_OK
    if not (args.agency and args.fiscal_year):
        print("sync requires --agency and --fiscal-year (or --all)", file=_stderr())
        return EXIT_CONFIG
    r = migrate_dataset(
        settings, args.agency, args.fiscal_year, dry_run=args.dry_run, force=args.force
    )
    print(f"Sync {r.dataset_id}: {r.status}  rows={r.transaction_count} net={r.net_obligation}")
    if r.status in {"integrity_conflict"}:
        return EXIT_INTEGRITY
    if r.status in {"conflict", "pointer_conflict", "reconciliation_failed"}:
        return EXIT_CONFLICT
    return EXIT_OK


def _cloud_verify(args, settings: Settings) -> int:
    from .cloud.reconciliation import reconcile_dataset_cloud

    rec = reconcile_dataset_cloud(settings, args.agency, args.fiscal_year)
    print(f"Verify {rec.scope}: {'PASSED' if rec.passed else 'FAILED'}")
    for c in rec.checks:
        print(f"  [{'ok' if c['ok'] else 'FAIL'}] {c['name']}: {c['expected']} vs {c['actual']}")
    return EXIT_OK if rec.passed else EXIT_INTEGRITY


def _cloud_audit(args, settings: Settings) -> int:
    from .cloud.reconciliation import audit_lake_cloud

    report = audit_lake_cloud(settings)
    _emit(report, args.json)
    return EXIT_OK if report["passed"] else EXIT_INTEGRITY


def _cloud_reconcile(args, settings: Settings) -> int:
    from .cloud.catalog import read_current
    from .cloud.reconciliation import reconcile_dataset_cloud
    from .storage_backends import get_storage_backend

    backend = get_storage_backend(settings)
    cat, _ = read_current(backend)
    all_ok = True
    for ds in cat.datasets.values():
        rec = reconcile_dataset_cloud(settings, ds.agency, ds.fiscal_year, backend=backend)
        print(f"  {rec.scope}: {'PASSED' if rec.passed else 'FAILED'}")
        all_ok = all_ok and rec.passed
    print("RESULT:", "ALL RECONCILED" if all_ok else "RECONCILIATION FAILED")
    return EXIT_OK if all_ok else EXIT_INTEGRITY


def _cloud_list_snapshots(args, settings: Settings) -> int:
    from .cloud.catalog import read_current
    from .storage_backends import get_storage_backend

    backend = get_storage_backend(settings)
    cat, _ = read_current(backend)
    out = {
        "catalog_version": cat.version,
        "datasets": {
            did: {
                "snapshot_id": ds.snapshot_id,
                "transactions": ds.transaction_count,
                "curated_prefix": ds.curated_prefix,
            }
            for did, ds in cat.datasets.items()
        },
    }
    _emit(out, args.json)
    return EXIT_OK


def _cloud_rollback(args, settings: Settings) -> int:
    from .cloud import keys
    from .cloud.catalog import LakeCatalog, read_current, rollback_to
    from .storage_backends import get_storage_backend

    backend = get_storage_backend(settings)
    target = args.to_version
    if target is None and args.snapshot_id:
        # Find the newest archived catalog version whose dataset set holds the snapshot.
        cat, _ = read_current(backend)
        for v in range(cat.version, 0, -1):
            try:
                archived = LakeCatalog.from_json(
                    backend.get_bytes(keys.catalog_version_key(v)).decode("utf-8")
                )
            except ObjectNotFoundError:
                continue
            if any(d.snapshot_id == args.snapshot_id for d in archived.datasets.values()):
                target = v
                break
    if target is None:
        print("rollback requires --to-version or a resolvable --snapshot-id", file=_stderr())
        return EXIT_CONFIG
    restored = rollback_to(backend, target)
    print(f"Rolled back to catalog version {target} (new active version {restored.version}).")
    return EXIT_OK


def _cloud_benchmark(args, settings: Settings) -> int:
    from .cloud.benchmarking import benchmark_cloud

    with tempfile.TemporaryDirectory() as tmp:
        result = benchmark_cloud(settings, args.agency, args.fiscal_year, tmp_root=Path(tmp))
    _emit(result, args.json)
    return EXIT_OK


def _cloud_estimate_cost(args, settings: Settings) -> int:
    from .cloud.cost import estimate_costs

    est = estimate_costs(settings)
    if args.json:
        print(json.dumps(est, indent=2))
    else:
        print(f"AWS S3 cost estimate ({est['region']}):")
        print(f"  {est['pricing_note']}")
        for name, e in est["estimates"].items():
            print(
                f"  {name}: storage ${e['monthly_storage_usd']}/mo, "
                f"one-time requests ${e['one_time_request_usd']}, "
                f"monthly total ~${e['monthly_total_usd']}"
            )
        print(
            f"  budget alert: ${est['recommended_budget_usd']} "
            f"(escalation ${est['budget_escalation_usd']}). {est['budget_note']}"
        )
    return EXIT_OK


_DISPATCH = {
    "check": _cloud_check,
    "inventory": _cloud_inventory,
    "plan-sync": _cloud_plan_sync,
    "sync": _cloud_sync,
    "verify": _cloud_verify,
    "audit-integrity": _cloud_audit,
    "reconcile": _cloud_reconcile,
    "list-snapshots": _cloud_list_snapshots,
    "rollback": _cloud_rollback,
    "benchmark": _cloud_benchmark,
    "estimate-cost": _cloud_estimate_cost,
}
