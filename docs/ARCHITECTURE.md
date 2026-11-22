# Architecture — Milestone 1 (Ingestion)

## Purpose

Extract federal prime-contract transactions from the public USAspending.gov
bulk-download API and land them as a typed, decimal-exact, fully-traceable local
data asset — with the separation of concerns, validation rigor, and provenance
discipline expected of a production data platform.

## Design principles

1. **Separation of responsibilities.** Transport, orchestration, validation,
   storage, and provenance each live in a single-purpose module. No module does
   two jobs; each is independently testable.
2. **Source fidelity + immutability.** The raw download is checksummed and never
   mutated. Every transformation is a *derived* artifact that can be regenerated
   from the immutable source.
3. **Fail loud, never silent.** Schema breaches abort; bad rows are quarantined
   with reasons, never dropped. Money is decimal-exact; negatives are preserved.
4. **Idempotency.** Deterministic, request-derived output paths; completed work
   is skipped unless forced. Re-processing identical input never duplicates
   output records.
5. **No secrets, no paid infra.** The public API needs no credentials; this
   milestone provisions nothing in the cloud.

## Components

| Module | Responsibility | Key properties |
|--------|----------------|----------------|
| `ingestion/config.py` | Env-driven settings (`Settings`) | Immutable dataclass; DI'd everywhere; no globals. |
| `ingestion/client.py` | HTTP transport | Timeouts, bounded exponential backoff + jitter, `Retry-After`, transient vs permanent error split, async job submit/poll, streaming download w/ incremental SHA-256, zip-slip/zip-bomb-safe extraction. |
| `ingestion/models.py` | Data contract + request model | Required/decimal/date/timestamp column sets; `ExtractRequest` builds the API payload and a deterministic `slug`; schema fingerprint. |
| `ingestion/validate.py` | Validation + quarantine | Contract check, typed parsing, duplicate detection, negative-safe; soft vs hard failures. |
| `ingestion/storage.py` | Parquet materialization | Explicit Arrow schema; `decimal128(23,2)` money; `date32` / `timestamp[us,UTC]`; atomic write. |
| `ingestion/manifest.py` | Provenance | Request params, timestamp, filenames, checksums, sizes, schema fingerprint, counts, totals, reconciliation. |
| `ingestion/extract.py` | Orchestration | Wires the pipeline; owns on-disk layout + idempotency. |
| `ingestion/cli.py` | CLI | `extract` / `agencies`; date range, agency, award types, output dir. |
| `analytics/reconcile.{py,sql}` | Local SQL reconciliation | DuckDB over Parquet; cross-checks the manifest; non-zero exit on mismatch. |

## Data flow

```
ExtractRequest ─► client.submit_bulk_download ─► POST /api/v2/bulk_download/awards/
                       │                                 returns status_url, file_url
                       ▼
                 client.poll_download ──────────► GET  /api/v2/download/status
                       │  (until status == finished | failed | timeout)
                       ▼
                 client.download_file ──────────► stream ZIP → data/raw/<slug>/…
                       │  (incremental SHA-256)
                       ▼
                 safe_extract_zip ──────────────► data/raw/<slug>/extracted/*.csv
                       │  (path-traversal + size/count guards)
                       ▼
                 validate_file ─────────────────► valid rows / quarantined rows
                       │
            ┌──────────┼───────────────────────────────┐
            ▼          ▼                                 ▼
   write_parquet   write_quarantine              build/write_manifest
   data/parquet/   data/quarantine/              data/manifests/<slug>.json
```

## The async bulk-download contract (verified against the live API)

1. `POST /api/v2/bulk_download/awards/` with filters (agency, prime award types,
   date type, date range, file format) → returns `status_url`, `file_name`,
   `file_url`.
2. Poll `status_url` until `status == "finished"` (also reports `total_rows`,
   `total_columns`, `total_size`). `"failed"` → error; exceeding the time budget
   → timeout.
3. `GET file_url` → stream the ZIP. It contains one CSV
   (`All_Contracts_PrimeTransactions_*.csv`).

These shapes were confirmed by a live NASA October-2025 extraction (196 rows,
297 columns) before the client was finalized.

## Validation policy

- **Contract breach (abort the file):** any required column absent.
- **Hard failure (quarantine the row, preserved + reasons):**
  missing/blank/duplicate transaction key, missing award key, blank or
  non-numeric `federal_action_obligation`.
- **Soft failure (keep the row; null the field; count a warning):** unparseable
  *ancillary* date/timestamp/decimal columns. A single bad secondary field never
  costs an otherwise-valid financial record.
- **Negative obligations are valid** (de-obligations) and pass through untouched.

## Typing decisions (from live-schema inspection)

| Source form (observed) | Stored type |
|------------------------|-------------|
| `federal_action_obligation` etc. (`-250.25`, `0.00`) | `decimal128(23, 2)` |
| `action_date`, `solicitation_date`, period-of-performance dates (`2025-10-31`) | `date32` |
| `last_modified_date` (`2025-10-31 12:29:06+00`) | `timestamp[us, UTC]` |
| all other ~290 columns | `string` (verbatim) |

`last_modified_date` is a full timezone-aware timestamp, **not** a plain date —
discovered by inspecting live data and typed accordingly so the time component is
not silently truncated. It is also the natural CDC key for later incremental loads.

## Idempotency & immutability

- Output paths derive from a deterministic `slug` (agency + award group + date
  type + date range).
- A completed extraction (Parquet **and** manifest present) is skipped unless
  `--force`. On `--force`, the per-slug raw directory is cleared first so stale
  server-timestamped archives never accumulate.
- Parquet and manifest writes are atomic (`*.tmp` → rename).

## Security / safety

- No secrets in code or VCS; `.env` and all of `data/` are git-ignored.
- ZIP extraction rejects absolute paths, `..` traversal (zip-slip), excess member
  counts, and oversized uncompressed totals (zip-bomb).
- API path/host come from settings; the only inlined SQL path (DuckDB
  `read_parquet`) is operator-supplied and single-quote escaped.

## Testing strategy

- **Unit:** models, validation/parsing, storage (decimal round-trip), client
  (retries, polling, timeout, streaming checksum, safe extraction) — all HTTP
  mocked with `responses`; no network.
- **Integration:** full pipeline over a controlled sample dataset via an injected
  fake client (deterministic, offline), including manifest reconciliation and
  idempotency.
- **Live (opt-in, `-m live`):** real NASA extraction against the public API.

## Roadmap (later milestones — out of scope here)

| Dir | Milestone purpose |
|-----|-------------------|
| `warehouse/` | Snowflake DDL / staging schemas. |
| `transformations/` | dbt Core staging + marts. |
| `airflow/dags/` | Orchestration & scheduling. |
| `infrastructure/terraform/` | IaC (S3, Snowflake) — kept credential-free; no paid infra provisioned. |
