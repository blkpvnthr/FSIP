"""Cloud publication + query benchmarking.

Measures what is actually measurable for the configured backend. For the local
backend (offline, Phase A), upload "throughput" is local-disk copy throughput and
network/request-count metrics are reported as **unavailable** — they require a live
S3 backend (Phase B). Nothing is fabricated: unmeasurable metrics are marked null.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from ..config import Settings
from ..logging_config import get_logger
from ..storage_backends import get_storage_backend
from . import catalog as catalog_mod
from . import verification
from .publication import publish_local_dataset

log = get_logger(__name__)


def benchmark_cloud(
    settings: Settings, agency: str, fiscal_year: int, *, tmp_root: Path, repeats: int = 3
) -> dict:
    """Benchmark publishing + reading one dataset through the configured backend."""
    backend = get_storage_backend(settings, local_root=tmp_root)
    is_s3 = backend.name == "s3"

    # --- Publication timing ---
    t0 = time.perf_counter()
    result = publish_local_dataset(settings, agency, fiscal_year, backend=backend, force=True)
    publish_s = time.perf_counter() - t0
    mb = result.bytes_uploaded / (1024 * 1024)

    # --- Query timing over the active snapshot (cold + warm) ---
    cat, _ = catalog_mod.read_current(backend)
    entry = cat.datasets[result.dataset_id]

    def timed(fn) -> list[float]:
        out = []
        for _ in range(repeats):
            t = time.perf_counter()
            fn()
            out.append((time.perf_counter() - t) * 1000)
        return out

    full = timed(lambda: verification.readback_totals(backend, settings, entry.part_keys))
    con = verification.duckdb_connection(backend, settings)
    src = verification._file_list_sql(backend, entry.part_keys)
    con.execute(f"CREATE VIEW tx AS SELECT * FROM {src}")
    month = timed(
        lambda: con.execute(
            "SELECT COUNT(*), SUM(federal_action_obligation) FROM tx WHERE fiscal_month = 6"
        ).fetchall()
    )
    con.close()

    return {
        "backend": backend.name,
        "dataset_id": result.dataset_id,
        "rows": result.transaction_count,
        "publication_seconds": round(publish_s, 3),
        "bytes_uploaded": result.bytes_uploaded,
        "upload_throughput_mb_s": round(mb / publish_s, 2) if publish_s else None,
        "object_count": result.object_count,
        "query_full_readback_ms": {"cold": round(full[0], 2), "min": round(min(full), 2)},
        "query_single_month_ms": {"cold": round(month[0], 2), "min": round(min(month), 2)},
        # S3-only metrics require a live backend; null on local (not fabricated).
        "network_throughput_mb_s": None if not is_s3 else round(mb / publish_s, 2),
        "s3_request_count": None,  # measurable only with live S3 + request metrics
        "note": (
            "Local backend: 'upload' is disk-copy throughput; network and S3 request "
            "metrics are unavailable offline and require a live S3 backend (Phase B)."
            if not is_s3
            else "Live S3 backend measurements."
        ),
    }


def benchmark_local_vs_cloud(settings: Settings, agency: str, fiscal_year: int, *, tmp_root: Path):
    """Convenience wrapper retained for symmetry with the local benchmark suite."""
    return benchmark_cloud(
        replace(settings, storage_backend="local"), agency, fiscal_year, tmp_root=tmp_root
    )
