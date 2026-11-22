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

## Limitations

- Benchmarks are for **NASA FY2026** only, on the single environment above.
- The partitioned-vs-unpartitioned wall-time result is **scale-dependent**; do not
  generalize the 0.52x figure to larger datasets.
- Query benchmarks reflect DuckDB 1.5.6 behavior over local Parquet; a different
  engine or object store would have different fixed-overhead characteristics.
