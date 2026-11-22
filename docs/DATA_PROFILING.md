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

- Only **NASA** (FY2025, FY2026) real data was profiled. Other agencies and
  FY2022–2024 are supported by code but were not downloaded.
- Figures are derived from the **internal** source-CSV → Parquet pipeline; they
  are **not** reconciled against official external USAspending aggregate totals.
