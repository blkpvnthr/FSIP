# Partitioning

The published contract data asset is physically partitioned using the **Hive
partitioning** convention so that analytical engines (DuckDB, Spark, Athena,
Snowflake external tables) can prune irrelevant files at query time. This document
describes the partition scheme, the federal fiscal calendar it encodes, how
atomic publication works, the file-sizing knobs, and the pruning behavior we
actually measured.

All partitioning logic lives in `ingestion/partitioning.py`; the derived
partition values are computed by the federal-fiscal helpers in
`ingestion/models.py`.

## Partition key

```
PartitionKey(awarding_agency, fiscal_year, fiscal_month)
```

| Partition column | Type | Domain | Source |
|------------------|------|--------|--------|
| `awarding_agency` | string | Agency registry key (`NASA`, `DOD`, `DHS`, `DOE`, `GSA`, …) | `ingestion/agencies.py` |
| `fiscal_year` | **INT** | e.g. `2025`, `2026` | `federal_fiscal_year(action_date)` |
| `fiscal_month` | INT | `1` … `12` | `federal_fiscal_month(action_date)` |

## Federal fiscal calendar

The US federal fiscal year starts on **October 1** of the prior calendar year and
is **named for the calendar year in which it ends**. Example:

> **FY2026 = 2025-10-01 … 2026-09-30.**

Fiscal months are renumbered so that **October is fiscal month 01** and
**September is fiscal month 12**:

| Calendar month | Fiscal month | Calendar month | Fiscal month |
|----------------|:---:|----------------|:---:|
| October  | 01 | April     | 07 |
| November | 02 | May       | 08 |
| December | 03 | June      | 09 |
| January  | 04 | July      | 10 |
| February | 05 | August    | 11 |
| March    | 06 | September | 12 |

Partitioning on `fiscal_year` and `fiscal_month` (rather than calendar date)
means a "give me Q1 FY2026" query maps directly to fiscal months `01, 02, 03`
regardless of calendar-year boundary.

## On-disk Hive layout

```
data/parquet/contracts/
└── awarding_agency=<KEY>/
    └── fiscal_year=<YYYY>/
        └── fiscal_month=<MM>/
            └── part-NNNNN.parquet
```

Concrete example (NASA FY2026, October):

```
data/parquet/contracts/awarding_agency=NASA/fiscal_year=2026/fiscal_month=01/part-00000.parquet
```

Because the partition values are embedded in the directory names, a Hive-aware
reader recovers `awarding_agency`, `fiscal_year`, and `fiscal_month` as columns
without them being stored in every row.

## Streaming partitioned writer

`PartitionedParquetWriter` keeps **one open `pyarrow.ParquetWriter` per
partition** and streams row batches to the correct partition as they arrive from
the validator. This keeps memory bounded regardless of archive size (see
`docs/PERFORMANCE.md` for the measured peak RSS).

### File-sizing knobs

| Knob | Default | Effect |
|------|---------|--------|
| `target_partition_file_rows` | `1,000,000` | When a partition exceeds this many rows, the writer **rolls to a new `part-NNNNN` file**. Keeps individual Parquet files a manageable size and enables parallel reads. |
| `parquet_row_group_size` | `100,000` | Rows per Parquet row group. Row groups are the unit of column-statistics-based skipping within a file. |
| `parquet_compression` | `zstd` | Column compression codec. `zstd` gives strong ratios at low CPU cost. |

For the NASA datasets processed, each `(agency, FY)` produced **12 partitions**
(one per fiscal month) and, at ~17k–21k rows, a single `part-00000.parquet` per
partition (well under the 1M-row roll threshold).

## Atomic publication

Partitioning and publication are coupled: a dataset is partitioned into a
**staging** tree first, reconciled, and only then promoted.

1. Candidate partitions are written to `data/staging/<dataset_id>/parquet/`.
2. `reconcile_dataset` validates the staged tree (row/transaction/net-obligation
   accounting, per partition, decimal-exact). Failure **blocks** publication.
3. Publication is a **single atomic directory rename** of the staged
   `fiscal_year` tree into:
   ```
   data/parquet/contracts/awarding_agency=<KEY>/fiscal_year=<FY>/
   ```
4. Any existing published tree for that `(agency, FY)` is **removed immediately
   before the rename**. Re-publication therefore **replaces, never appends** —
   there are **no duplicate published transactions** on re-run.

Because the switch is a single rename, readers always observe either the complete
old tree or the complete new tree — never a partially written partition set.

See `docs/ARCHITECTURE.md` (Milestone 2 lifecycle) for the full
staging → reconcile → publish → conflict-detection sequence.

## Partition pruning (measured)

Partition pruning is the payoff: a query filtered to one fiscal month should read
only that month's files. Measured on **NASA FY2026** (17,603 rows, 12 partitions):

| Metric | Value |
|--------|-------|
| Files scanned for a single-month filter | **1 of 12** |
| Rows scanned | **1,450 of 17,603** |
| Rows skipped by pruning | **91.8%** |

Pruning provably reduces the data scanned. **Note:** at this small scale (~17k
rows, ~6.5 MB) the per-file open overhead means partitioning does **not** improve
single-query *wall time* — see `docs/PERFORMANCE.md` for the honest numbers and
why the benefit grows with data volume.

## Scale note (Milestone 2.1)

Milestone 2.1 added datasets that exercise partitioning at a much larger scale and
across multiple agencies and fiscal years. The lake now holds **7 published
datasets** (NASA FY2022–FY2026, DOE FY2026, GSA FY2026), each still partitioned
into **12 fiscal-month partitions**.

- **GSA FY2026 is the large-scale case: ~1.3M published rows** (1,300,826) from a
  **multi-member** manual archive — two CSV files
  (`FY2026_047_Contracts_Full_20261108_1.csv` and `_2.csv`) streamed into the same
  partitioned writer. This is the first dataset large enough for the
  `target_partition_file_rows` roll threshold to be relevant per high-volume
  fiscal month.
- The GSA archive is **3.2 GB uncompressed**, which **exceeded the default 2 GiB
  `max_extract_bytes` cap** and was correctly rejected by the zip-bomb / size
  guard; ingestion required raising **`FSDW_MAX_EXTRACT_BYTES`** (e.g. to 8 GiB).
  See `docs/RUNBOOK.md`.

### Partition isolation across agencies and years

Because the top-level partition key is `awarding_agency` and the second level is
`fiscal_year`, every `(agency, FY)` dataset occupies a **disjoint subtree** of
`data/parquet/contracts/`. Datasets are therefore **physically independent**:
publishing, re-publishing, or force-overwriting one `(agency, FY)` touches only its
own `fiscal_year` subtree and never another dataset's files. This isolation is what
lets the atomic replace-not-append publish, the per-dataset reconciliation, and the
Milestone-2.1 cross-dataset integrity audit all operate without datasets
interfering with one another on disk.

---

## Milestone 3 — cloud (status)

In the cloud lake the same Hive layout becomes an **S3 object-key** layout under a
curated snapshot prefix:

```
curated/contracts/awarding_agency=<A>/fiscal_year=<FY>/snapshot_id=<id>/fiscal_month=<MM>/part-*.parquet
```

The `awarding_agency` / `fiscal_year` / `fiscal_month` partition columns are
preserved exactly, so partition pruning works identically for DuckDB `httpfs`,
Athena, and Spark readers. Because S3 has no atomic directory rename, the
Milestone-2 atomic-rename publish is replaced by an immutable **per-snapshot
prefix** plus a catalog pointer (`control/current.json`); readers resolve the
active snapshot via the catalog and never glob historical snapshots. Partition
pruning is expected to convert to a **wall-time** win on object storage, where
per-object latency dominates and skipping 11 of 12 months avoids network I/O (see
`docs/PERFORMANCE.md`).

**Status:** prepared, not deployed — no AWS resources exist; verified against a
**local-backend cloud-lake emulation**; **live S3 pending approval**. See
`docs/CLOUD_ARCHITECTURE.md` and `docs/PARTITIONING.md` partner doc
`docs/CLOUD_PUBLICATION.md`.
