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
