# Data Profiling

Every published dataset is profiled to produce a verifiable, decimal-aware
statistical summary. Profiling answers "what did we actually land?" — row and
transaction counts, award and recipient cardinality, the full obligation
distribution (positive, negative, zero), and storage characteristics. The
profiles double as reconciliation evidence and as a human-readable data-quality
record for reviewers.

Profiling lives in `ingestion/profiling.py` and writes
`data/profiles/<id>.json` and a companion `.md`.

## Methodology

- Statistics are computed with **DuckDB** directly over the published Parquet.
- Column-level statistics use DuckDB's **`SUMMARIZE`**.
- Cardinality figures reported via **`approx_unique` are labeled approximate**;
  the headline award/recipient/transaction counts below are **exact** distinct
  counts.
- **Money is decimal-exact.** `federal_action_obligation` is stored as
  `decimal128(23,2)` and summed as a decimal — never as a float.

## Financial semantics

Correct interpretation of the figures below:

- `federal_action_obligation` is a **transaction obligation**, **not an outlay**.
  The headline **net** figure is `SUM(federal_action_obligation)` over valid rows.
- **Net = positive + negative.** Negative obligations are **de-obligations** and
  are preserved, not discarded.
- **Award count = `COUNT(DISTINCT contract_award_unique_key)`** and is **not** the
  transaction count (one award has many modification transactions).
- **Recipient identity uses `recipient_uei`** (stable) rather than
  `recipient_name` (which varies in spelling/formatting).

## Per-fiscal-year statistics (NASA, real data)

### Counts

| Metric | FY2025 | FY2026 |
|--------|-------:|-------:|
| Source rows | 21,284 | 17,603 |
| Valid rows | 21,284 | 17,603 |
| Quarantined rows | 0 | 0 |
| Filtered (out-of-scope FY) rows | 0 | 0 |
| Duplicate transaction keys | 0 | 0 |
| Distinct awards (`contract_award_unique_key`) | 9,502 | 8,309 |
| Distinct recipients (`recipient_uei`) | 3,021 | 2,701 |
| Partitions | 12 | 12 |

### Obligation distribution (decimal-exact)

| Metric | FY2025 | FY2026 |
|--------|-------:|-------:|
| Net obligation | $15,787,739,509.64 | $16,095,847,228.00 |
| Positive total | $16,337,439,738.27 | $16,634,474,156.87 |
| Negative total | -$549,700,228.63 | -$538,626,928.87 |
| Negative (de-obligation) count | 1,716 | 1,281 |
| Zero-dollar transactions | 6,531 | 5,495 |
| Min single transaction | -$132,474,874.08 | -$55,881,464.43 |
| Max single transaction | $580,000,000.00 | $660,000,017.00 |

Identity check (both years): `positive + negative = net`, and
`valid + quarantined + filtered = source`. Both hold exactly.

### Storage

| Metric | FY2025 | FY2026 |
|--------|-------:|-------:|
| Source ZIP (bytes) | 9,203,660 | 7,376,760 |
| Uncompressed CSV (bytes) | 61,169,341 | 48,940,023 |
| Published Parquet (bytes) | 7,726,390 | 6,560,903 |
| Compression ratio (CSV ÷ Parquet) | 7.92x | 7.46x |

### Provenance fingerprints

| Item | Value |
|------|-------|
| FY2025 archive SHA-256 | `f0f3aae60451ea4743dc08705270cdc0fbef9e9539722fd752bb88f40bdd8f47` |
| FY2026 archive SHA-256 | `9fb38a3112b00e7e5cce8962a7dd75484801f8b77bceaa3fc35677cdbba4e2d1` |
| Schema fingerprint (both years, 297 cols) | `36933b682c9a827618a999358cfb091e8938f388c910aaf10e65479fe6f85a03` |

## Combined (FY2025 + FY2026)

| Metric | Value |
|--------|-------:|
| Total transactions | 38,887 |
| Combined net obligation | ~$31.88B |
| Duplicate transaction keys across both years | 0 |
| Reconciliation (both datasets) | PASSED (source→target + per-partition) |
| Idempotent re-ingest | Verified — no duplication; raw archive SHA-256 unchanged after processing |

---

# Milestone 2.1 — the full published lake (7 datasets)

Milestone 2.1 expanded the lake from the two NASA API datasets above to **7
published datasets** across three agencies, and narrowed every dataset to a single
consistent **contracts-only** scope (award types A/B/C/D). The NASA FY2025 and
FY2026 profiles above are unchanged and carried forward; the figures below are the
authoritative lake-wide view.

## Source provenance and scope

- **NASA FY2022–FY2024** were ingested from the **manual** USAspending Award Data
  Archive ("Contracts Full"); IDVs were removed by the award-type scope filter.
- **NASA FY2025–FY2026** are the **retained Milestone-2 API** datasets (unchanged).
- **DOE FY2026** and **GSA FY2026** were ingested from the **manual** archive.
  GSA FY2026 is **multi-member** (two CSV files), demonstrating multi-member
  ingestion at scale.
- **All 7 datasets are contracts-only scope** (award types A/B/C/D). Rows outside
  that scope — chiefly IDVs, plus any out-of-scope fiscal-year rows — are counted as
  `filtered` (never dropped from the accounting). The full manual archives differ
  from the API files by **award-type scope**, not schema.

## Per-dataset summary (contracts-only, decimal-exact)

| Agency | FY | Source | Published | Filtered (IDVs / scope) | De-obligation count | Net obligation |
|--------|---:|-------:|----------:|------------------------:|--------------------:|---------------:|
| NASA | 2022 | 27,045 | 23,015 | 4,030 | 1,661 | $15,239,515,074.07 |
| NASA | 2023 | 24,868 | 20,918 | 3,950 | 1,602 | $16,129,077,722.63 |
| NASA | 2024 | 23,182 | 19,779 | 3,403 | 1,653 | $15,015,940,724.18 |
| NASA | 2025 | 21,284 | 21,284 | 0 | 1,716 | $15,787,739,509.64 |
| NASA | 2026 | 17,603 | 17,603 | 0 | 1,281 | $16,095,847,228.00 |
| DOE  | 2026 | 11,376 | 10,469 | 907 | 1,084 | $55,135,256,088.16 |
| GSA  | 2026 | 1,437,500 | 1,300,826 | 136,674 | 43,168 | $28,724,193,258.25 |
| **TOTAL** | | **1,562,858** | **1,413,894** | **148,964** | | **$162,127,569,604.93** |

NASA FY2025–FY2026 show **0 filtered** because the retained API datasets were
already contracts-only; the manual-sourced datasets show non-zero `filtered` counts
(predominantly IDVs removed by the scope filter).

For every dataset:

- **0 quarantined** rows.
- **Reconciliation PASSED** (source→target and per-partition, decimal-exact).
- **12 fiscal-month partitions**.
- **Negatives preserved** (de-obligations counted, not discarded).

## Dataset-wide integrity audit

`fsdw audit-integrity` over the whole lake confirms no transaction key is counted
twice across datasets:

| Metric | Value |
|--------|------:|
| `total_published_rows` | 1,413,894 |
| `distinct_transaction_keys` | 1,413,894 |
| `duplicate_key_count` | 0 |
| `cross_dataset_key_count` | 0 |
| `conflicting_key_count` | 0 |
| Result | **PASSED** |

See `docs/SCHEMA_EVOLUTION.md` for the integrity model and the pre-publish gate.

## External reconciliation (equivalent scope)

Each dataset's net obligation was reconciled against USAspending's live aggregate
(`POST /api/v2/search/spending_over_time/`, `spending_level=transactions`) for an
equivalent scope (awarding agency, fiscal year by `action_date`, award types
A/B/C/D), within a **1%** tolerance:

| Dataset | Local net | External aggregate | Difference | % |
|---------|----------:|-------------------:|-----------:|--:|
| NASA FY2024 | $15,015,940,724.18 | $15,015,940,724.18 | $0.00 | 0.0% |
| NASA FY2025 | $15,787,739,509.64 | $15,787,739,509.64 | $0.00 | 0.0% |
| NASA FY2026 | $16,095,847,228.00 | $16,095,925,883.00 | -$78,655.00 | 0.0005% |
| DOE FY2026 | $55,135,256,088.16 | $55,135,498,845.76 | -$242,757.60 | 0.00044% |
| GSA FY2026 | $28,724,193,258.25 | $28,936,083,564.31 | -$211,890,306.06 | 0.73% |

**Interpretation:** settled fiscal years match **exactly to the cent**; recent,
high-churn datasets differ only by data-refresh timing (live aggregate vs
point-in-time snapshot). **All are within the 1% tolerance.** An exact match is not
expected and a non-zero difference on a current fiscal year is **not** an error.

## Manual vs API archive (NASA FY2026)

Milestone 2.1 verified that the **manual** Award Data Archive and the **API**
bulk-download describe the same data:

| Aspect | Manual "Contracts Full" | API bulk-download |
|--------|------------------------|-------------------|
| Schema | 297 columns | 297 columns — **0 differ** (same types/formats) |
| Member naming | `FY<fy>_<code>_Contracts_Full_<date>_N.csv` | `All_Contracts_PrimeTransactions_<ts>_N.csv` |
| Total rows (FY2026) | 23,540 (contracts A/B/C/D = 17,592 + IDVs = 5,948) | — |
| Contracts-only (scope-filtered) | 17,592 rows / $16,093,899,886.88 | 17,603 rows / $16,095,847,228.00 (published) |

The small residual difference between the scope-filtered manual file and the
published API dataset is **award-type scope (IDVs) plus data-refresh timing — not a
schema difference**. `award_type_code` A/B/C/D are definitive contracts/PO/DO/BPA;
IDVs carry a **blank** `award_type_code` (with `idv_type_code` populated instead),
which is exactly how the scope filter distinguishes them. Because the schemas are
identical, **no normalization is needed** to combine manual- and API-sourced
datasets in one lake.

## Year-over-year note (limitation)

A FY2025 → FY2026 comparison is possible, but **FY2026 may be partial relative to
a complete fiscal year** (FY2026 runs through 2026-09-30). Any YoY reading must
account for this; the FY2026 figures are a point-in-time snapshot, not necessarily
a full-year total.

## Exact vs approximate

| Figure | Nature |
|--------|--------|
| Row / transaction / award / recipient counts above | **Exact** distinct counts. |
| Obligation sums, min, max | **Exact** (decimal128). |
| Any DuckDB `approx_unique` cardinality in the raw profile JSON | **Approximate** (explicitly labeled). |

## Scope / limitations

- **Milestone 2 (historical):** the two sections above profile the originally
  processed NASA API datasets (FY2025, FY2026).
- **Milestone 2.1 (current lake):** real data now covers **NASA FY2022–FY2026**,
  **DOE FY2026**, and **GSA FY2026** — 7 datasets, contracts-only scope. **DoD is
  not processed** (very large; needs storage/approval).
- Figures are now **also reconciled against the external** USAspending aggregate
  (see above), within a **1%** tolerance. Exactness holds only for **settled**
  fiscal years; differences on recent/high-churn datasets are **refresh-timing**,
  not errors.
- The lake is **contracts-only** (IDVs excluded). Full scope (incl. IDVs) is
  reachable via `--award-types all` but is **not published**.
- **GSA required a raised extract cap** (`FSDW_MAX_EXTRACT_BYTES`): its manual
  archive is 3.2 GB uncompressed, above the default 2 GiB guard.
