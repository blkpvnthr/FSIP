# Performance

This document reports **measured** ingestion and query performance for the
Milestone 2 pipeline. All figures come from `ingestion/benchmarking.py`, which
times ingestion end-to-end (in a **subprocess**, so peak RSS is honest),
micro-benchmarks each phase, and compares partitioned vs unpartitioned DuckDB
queries with partition-pruning evidence. Results are written to
`data/benchmarks/<id>.json`.

The numbers below are for **NASA FY2026** (17,603 rows, ~6.5 MB Parquet). We
report the results honestly, including a case where partitioning did **not** help.

## Benchmark environment

| Component | Version / spec |
|-----------|----------------|
| OS | macOS 27.0, arm64 |
| CPUs | 18 |
| Python | 3.12.13 |
| DuckDB | 1.5.6 |
| PyArrow | 18.1.0 |

## End-to-end ingestion

| Metric | Value |
|--------|------:|
| Wall time (min of runs) | **1.12 s** |
| Throughput | ≈ **15,756 tx/s** |
| Peak process RSS | ≈ **452 MB** |

Throughput and bounded memory come from streaming the CSV in batches
(`batch_size` 50,000) and streaming Parquet writes per partition, so memory does
not scale with archive size.

## Phase timing (end-to-end breakdown)

| Phase | Time (s) |
|-------|---------:|
| extract | 0.042 |
| process (parse + validate + partition + write) | 0.973 |
| reconcile | 0.026 |
| publish | 0.0002 |

Publication is effectively free because it is a **single atomic directory
rename** — no data copy (see `docs/PARTITIONING.md`).

## Phase micro-benchmarks

| Operation | Time (s) |
|-----------|---------:|
| CSV parse | 0.363 |
| Schema validation | 0.052 |
| Parquet conversion | 0.698 |

Parquet conversion and CSV parsing dominate; validation is cheap.

## Query performance: partitioned vs unpartitioned

A **single fiscal-month filter** was run against both a partitioned and an
unpartitioned copy of the same data.

| Query variant | Time (ms, min of runs) |
|---------------|-----------------------:|
| Partitioned | 3.26 |
| Unpartitioned | 1.69 |
| **Speedup** | **0.52x (partitioned SLOWER)** |

### Honest interpretation

At this scale — ~17k rows, ~6.5 MB — **partitioning does not improve
single-query wall time.** The per-file open overhead (12 partition files) exceeds
the cost of simply scanning one small unpartitioned file. We report this as
measured and do **not** claim a speedup that did not occur.

## Partition pruning (the real benefit)

Pruning is provable even where wall time does not improve: a single-month filter
reads far less data.

| Metric | Value |
|--------|------:|
| Files scanned | **1 of 12** |
| Rows scanned | **1,450 of 17,603** |
| Rows skipped | **91.8%** |

## When partitioning pays off

Pruning reduces **data scanned** by ~92% here, but at ~6.5 MB that reduction is
swamped by fixed per-file overhead. The benefit scales with volume: as partitions
grow to hundreds of MB or GB (e.g. full DoD fiscal years), skipping 11 of 12
months means skipping gigabytes of I/O, and partitioning becomes a decisive
wall-time win. The partition design is therefore a **volume-forward** choice:
correct and pruning-effective now, wall-time-advantageous at production scale.

## Milestone 2.1 — scale results (GSA FY2026, 1.3M rows)

Milestone 2.1 added a genuine scale test: **GSA FY2026**, a multi-member manual
Award Data Archive (**2 CSV members, 3.2 GB uncompressed**) that publishes
**1,300,826 contract transactions**. The published lake now holds **1,413,894
transactions across 7 datasets — above the 1M scale target**, so no scale
limitation applies.

### Ingestion at scale (measured, single subprocess run)

| Metric | GSA FY2026 | NASA FY2026 (ref) |
|--------|-----------:|------------------:|
| Rows ingested | ~1.3–1.44M | 17,603 |
| Wall time | **~104–113 s** | 1.12 s |
| Throughput | ≈ **13,000–13,800 tx/s** | ≈ 15,756 tx/s |
| Peak process RSS | ≈ **2.4 GB** | ≈ 452 MB |
| Uncompressed source | 3.2 GB | 48.9 MB |

Throughput holds (~13k tx/s) from ~17k to ~1.4M rows. Peak RSS at scale (~2.4 GB)
reflects the per-partition streaming `ParquetWriter`s plus in-flight batches; it is
bounded by `batch_size` × partition count, **not** by the 3.2 GB archive size —
the archive is streamed, never fully buffered. Process time dominates (101.6 s);
reconcile (0.08 s) and the atomic publish (0.0002 s) stay negligible at scale.

### Partition pruning at scale

| Metric | GSA FY2026 |
|--------|-----------:|
| Files scanned (single month) | **1 of 12** |
| Rows scanned | **122,838 of 1,300,826** |
| Rows skipped | **90.6%** |
| Partitioned vs unpartitioned single-month query (min) | 5.2 ms vs 4.7 ms (**0.91x**) |

### Honest interpretation at scale

Even at 1.3M rows, the single-month query is ~**0.91x** (marginally slower)
partitioned-vs-unpartitioned on **local SSD**: DuckDB's row-group statistics let it
scan one unpartitioned Parquet file so fast that the 12-file open overhead still
cancels the pruning win. Pruning nonetheless **skips 90.6% of rows** — a real I/O
reduction that becomes a decisive wall-time win on **object storage (S3, Milestone
3)** where per-object latency dominates and skipping 11 of 12 months avoids
gigabytes of network I/O. We report the measured parity honestly and do **not**
claim a local-disk speedup that did not occur.

## Limitations

- Benchmarks were run on the single environment above (macOS arm64, local SSD).
- The partitioned-vs-unpartitioned wall-time parity (0.52x–0.91x) is
  **scale- and storage-dependent**; do not generalize it. The pruning *data-skip*
  (~90%) is the durable, measured benefit and is expected to convert to wall-time
  gains on object storage.
- GSA required raising `FSDW_MAX_EXTRACT_BYTES` to 8 GiB (its 3.2 GB uncompressed
  exceeds the 2 GiB default safety cap — the cap correctly rejected it first).
- DoD was **not** benchmarked (not ingested; large, needs approval).
