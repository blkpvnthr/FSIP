# Data Dictionary — Prime Contract Transactions

Source: USAspending.gov bulk-download, `prime_awards` (prime contract
transactions). A full download carries **297 columns**; this platform preserves
**all** of them verbatim in the Parquet output and does not drop columns it does
not explicitly type.

The table below documents the **stable data contract** — the subset the warehouse
depends on and types explicitly. All other columns are preserved as UTF-8
`string`. Types and source formats were verified against a live NASA
October-2025 download.

## Identifiers

| Column | Arrow type | Role | Notes |
|--------|-----------|------|-------|
| `contract_transaction_unique_key` | string | **Stable transaction ID** (grain) | Unique per transaction. Primary key of the Parquet; duplicates are quarantined. |
| `contract_award_unique_key` | string | **Generated award ID** | Groups transactions into an award (one award → many modification transactions). |
| `award_id_piid` | string | Award PIID | Procurement Instrument Identifier (e.g. `80MSFC20C0052`). |
| `modification_number` | string | Modification sequence | e.g. `P00140`; distinguishes transactions within an award. |
| `recipient_uei` | string | **Recipient UEI** | 12-char Unique Entity Identifier (SAM.gov). |
| `recipient_name` | string | Recipient legal name | e.g. `LOCKHEED MARTIN CORP`. |

## Financial (decimal-exact; `decimal128(23, 2)`)

Stored as fixed-point decimals — never floats. **Negative values are valid**
(de-obligations) and are preserved exactly.

| Column | Role |
|--------|------|
| `federal_action_obligation` | **Primary obligation of the transaction.** Blank or non-numeric values are quarantined (never dropped). Sum of this over valid rows is the headline reconciliation figure. |
| `total_dollars_obligated` | Cumulative dollars obligated on the award. |
| `current_total_value_of_award` | Current total award value. |
| `potential_total_value_of_award` | Potential total value incl. options. |
| `base_and_exercised_options_value` | Base + exercised options value. |
| `base_and_all_options_value` | Base + all (incl. unexercised) options value. |

## Dates (`date32`) and timestamps (`timestamp[us, UTC]`)

| Column | Arrow type | Source format (observed) | Notes |
|--------|-----------|--------------------------|-------|
| `action_date` | date32 | `2025-10-31` | Date the transaction action occurred; the default date-range filter field. |
| `last_modified_date` | **timestamp[us, UTC]** | `2025-10-31 12:29:06+00` | **Source modification timestamp.** Carries a time + UTC offset — typed as a timestamp, not a date, to avoid truncation. Natural CDC key for later incremental loads. |
| `period_of_performance_start_date` | date32 | `2015-09-03` | PoP start. |
| `period_of_performance_current_end_date` | date32 | `2025-11-30` | PoP current end. |
| `period_of_performance_potential_end_date` | date32 | `2029-05-31 00:00:00` | PoP potential end; midnight time component normalized to date. |
| `solicitation_date` | date32 | `2024-07-08` | Solicitation date. |

## Agency identifiers

| Column | Arrow type | Notes |
|--------|-----------|-------|
| `awarding_agency_code` | string | Top-tier awarding agency code (NASA = `080`). |
| `awarding_sub_agency_code` | string | Sub-agency code. |
| `funding_agency_code` | string | Top-tier funding agency code. |

## Pipeline-added / operational fields

These appear only in the **quarantine** output, not the Parquet:

| Column | Notes |
|--------|-------|
| `_quarantine_reasons` | `;`-separated reason codes: `missing_transaction_key`, `duplicate_transaction_key`, `missing_award_key`, `blank_obligation`, `malformed_obligation`. |

## Provenance (manifest, `data/manifests/<slug>.json`)

| Field | Meaning |
|-------|---------|
| `extraction_timestamp_utc` | When the extraction ran. |
| `request` / `api_request_payload` | Exact request parameters and API body. |
| `source_archive` / `source_csv` | Original filename, stored path, SHA-256, size (bytes). |
| `schema_fingerprint` | SHA-256 of the ordered 297-column header (drift signal). |
| `column_count` / `columns` | Observed column count and full ordered list. |
| `reported_total_rows` / `reported_total_columns` | Counts the API reported for the job. |
| `validated_total_rows` / `valid_row_count` / `quarantined_row_count` | Partition accounting (valid + quarantined = total). |
| `negative_obligation_count` | Count of de-obligations preserved. |
| `obligation_total` | Decimal-exact sum of `federal_action_obligation` over valid rows. |
| `warning_count` | Soft (non-fatal) validation warnings. |
| `reconciliation` | Booleans: API-reported vs observed row count, and partition exhaustiveness. |

## Reference values (NASA, Oct 1–31 2025, `action_date`, contracts A/B/C/D)

Produced by the live extraction and confirmed by DuckDB reconciliation:

| Metric | Value |
|--------|-------|
| Columns | 297 |
| Total / valid / quarantined rows | 196 / 196 / 0 |
| Negative-obligation rows | 6 |
| Total federal obligation | **90,348,147.32** |
| Source ZIP SHA-256 | `83c720ab50acc85428ebbaea848f5731002c9971e575cf15f0ebfe47452c63ac` |

---

# Milestone 2 additions

Milestone 2 ingests **full fiscal-year** contract archives and lands them as a
**Hive-partitioned** Parquet data asset. This section documents the partition
columns, the v2.0 dataset manifest, and the expanded quarantine reason codes.
All 297 source columns documented above are still preserved verbatim.

## Partition columns

Published Parquet is physically partitioned on three derived columns computed by
the federal-fiscal helpers in `ingestion/models.py`. They encode the US federal
fiscal calendar, which begins **October 1** of the prior calendar year.

| Column | Type | Domain | Derivation |
|--------|------|--------|------------|
| `awarding_agency` | string (partition key) | Agency registry key, e.g. `NASA`, `DOD`, `DHS`, `DOE`, `GSA` | Registry key for the awarding top-tier agency (`ingestion/agencies.py`). |
| `fiscal_year` | **INT** | e.g. `2025`, `2026` | `federal_fiscal_year(action_date)` — FY is **named for the calendar year in which it ends**. FY2026 = 2025-10-01 … 2026-09-30. |
| `fiscal_month` | INT | **1 … 12** | `federal_fiscal_month(action_date)` — **1 = October … 12 = September**. |

### Federal fiscal-month mapping

| Calendar month | Fiscal month | Calendar month | Fiscal month |
|----------------|:---:|----------------|:---:|
| October  | 01 | April     | 07 |
| November | 02 | May       | 08 |
| December | 03 | June      | 09 |
| January  | 04 | July      | 10 |
| February | 05 | August    | 11 |
| March    | 06 | September | 12 |

The Hive on-disk layout is:

```
awarding_agency=<KEY>/fiscal_year=<YYYY>/fiscal_month=<MM>/part-NNNNN.parquet
```

See `docs/PARTITIONING.md` for path mechanics and pruning behavior.

## Dataset manifest (v2.0)

Each published dataset writes a manifest to `data/manifests/<dataset_id>.json`.
The v2.0 manifest extends the Milestone 1 provenance fields with full-archive
accounting, partitioning, the content signature used for conflict detection, and
reconciliation results.

The field names below are the **actual** top-level keys emitted by
`ingestion/archive.py` (verified against a generated manifest).

| Field | Meaning |
|-------|---------|
| `manifest_schema_version` | `"2.0"`. |
| `dataset_id` | Stable id, e.g. `nasa_contracts_fy2026`. |
| `ingestion_timestamp_utc` | When the dataset was ingested. |
| `agency` | Nested object: `{key, name, toptier_code}` for the awarding agency. |
| `fiscal_year` | Federal fiscal year (INT). |
| `scope_filter` | `{date_type, fy_start, fy_end}` — the action-date window applied. |
| `source_archive` | `{original_file_name, stored_path, sha256, size_bytes, uncompressed_bytes}` (immutability anchor). |
| `source_csv_members` | List of `{original_file_name, sha256, size_bytes}`, one per extracted CSV. |
| `schema_fingerprint` | SHA-256 of the ordered 297-column header (see `docs/SCHEMA_EVOLUTION.md`). |
| `column_count` / `columns` | Observed column count (297) and the full ordered header. |
| `totals` | Nested accounting: `source_rows`, `valid_rows`, `quarantined_rows`, `filtered_rows`, `duplicate_rows`, `distinct_valid_tx`, `net_obligation`, `positive_obligation`, `negative_obligation`, `negative_obligation_count`, `zero_obligation_count` (money as exact decimal strings). |
| `partitions` | List of `{partition, rows, files}` per fiscal-month partition. |
| `partition_count` / `parquet_file_count` | Number of partitions and part-files published. |
| `reconciliation` | `{passed, checks[...]}` — the decimal-exact source→target checks (publication gate). |
| `content_signature` | `{valid_rows, net_obligation, distinct_valid_tx, schema_fingerprint}` — used for idempotency / conflict detection. |
| `published` / `published_path` / `status` | Whether/where the dataset was published and its status (`published` / `conflict` / `reconciliation_failed`). |
| `quarantine_path` | Path to the quarantine CSV (or null when none). |
| `content_signature` | (`valid_rows`, `net_obligation`, `distinct_tx`, `schema_fingerprint`) — compared on re-run for idempotency / conflict detection. |
| `reconciliation` | Result of the staging gate: accounting-identity and source-to-target (per-partition) checks, decimal-exact. |
| `extraction_timestamp_utc` | When the run completed. |

## Quarantine reason codes

Bad rows are **never dropped** — they are written to `data/quarantine/<dataset_id>.csv`
with a `;`-separated `_quarantine_reasons` field. Milestone 2 reason codes:

| Reason code | Meaning | Severity |
|-------------|---------|----------|
| `missing_transaction_key` | `contract_transaction_unique_key` absent/blank. | Hard — row quarantined. |
| `duplicate_transaction_key` | Transaction key already seen (cross-batch / cross-member). | Hard — row quarantined. |
| `missing_award_key` | `contract_award_unique_key` absent/blank. | Hard — row quarantined. |
| `blank_obligation` | `federal_action_obligation` is blank. | Hard — row quarantined. |
| `malformed_obligation` | `federal_action_obligation` present but non-numeric. | Hard — row quarantined. |
| `missing_or_unparseable_action_date` | `action_date` absent or unparseable (cannot be partitioned). | Hard — row quarantined. |

**Not quarantined (by policy):**

- **Negative obligations are VALID** (de-obligations) and pass through untouched.
- **Unparseable *ancillary* date/decimal fields** (not `action_date`, not
  `federal_action_obligation`) are **soft warnings**: the row is kept, the single
  bad field is nulled, and a warning is counted. These fields are **never coerced
  to zero**.
