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

---

# Architecture — Milestone 2 (Historical Ingestion)

Milestone 1 landed a single API extraction as typed Parquet. Milestone 2 turns
that into a **repeatable, reconcilable historical-backfill pipeline** that ingests
full fiscal-year contract archives, partitions them for analytics, proves
decimal-exact correctness before publishing, and refuses to silently corrupt a
published dataset.

The design keeps every Milestone 1 principle (source immutability, fail-loud
validation, decimal exactness, idempotency) and adds three new guarantees:

1. **Atomic publication.** A dataset becomes visible to readers only via a single
   directory rename, after reconciliation passes. Readers never observe a
   half-written partition tree.
2. **Reconciliation as a gate.** Staged Parquet must balance to the source CSV
   (row, transaction, and net-obligation accounting, per partition, decimal-exact)
   *before* it can be published. A reconciliation failure blocks publication.
3. **Conflict detection.** Re-publishing a dataset whose content differs from what
   is already published is refused (not overwritten) unless explicitly forced —
   the foundation for the later Change-Data-Capture (CDC) milestone.

## What is in scope / what was actually run

- **Processed with real data:** NASA (agency `080`) full fiscal-year contract
  archives for **FY2025** and **FY2026**. See `docs/DATA_PROFILING.md` and
  `docs/PERFORMANCE.md` for the verified figures.
- **Supported by code but not run:** DoD (`097`), DHS (`070`), DOE (`089`),
  GSA (`047`), and fiscal years 2022–2024. The agency registry and
  `backfill --download` support them; large DoD archives need storage/time
  estimates and approval and were deliberately not downloaded.

## Modules added / extended in Milestone 2

All modules live in the `ingestion/` package and follow the Milestone 1 rule:
one responsibility per module, independently testable.

| Module | Responsibility | Key properties |
|--------|----------------|----------------|
| `ingestion/agencies.py` | Extensible agency registry | NASA `080`, DOD `097`, DHS `070`, DOE `089`, GSA `047` — codes verified against the live USAspending `toptier_agencies` endpoint. Alias resolution; backward-compatible `AGENCIES` mapping. |
| `ingestion/models.py` | Data contract + **federal-fiscal helpers** | `federal_fiscal_year` (FY starts Oct 1, named for the year it ends), `federal_fiscal_month` (1 = Oct … 12 = Sep), `federal_fiscal_quarter`. |
| `ingestion/client.py` | HTTP transport + safe archive handling | Retries with bounded exponential backoff; streaming download with incremental SHA-256; safe ZIP extraction (rejects path traversal / zip-slip and symlinks; enforces file-count and uncompressed-size caps). |
| `ingestion/archive.py` | **Full-archive pipeline orchestration** | inspect → safe extract → bounded-memory CSV streaming → validate + dedup → fiscal-scope filter → partitioned Parquet to staging → reconcile → atomic publish → manifest. Also `download_full_archive()` for `backfill --download`. |
| `ingestion/validate.py` | Validation, dedup, quarantine | Contract check; per-row validation; cross-batch / cross-member dedup via a shared seen-keys set; streaming `QuarantineWriter`. Negative obligations are **valid**. |
| `ingestion/storage.py` | Decimal-safe PyArrow typing | Money columns = `decimal128(23,2)`; `action_date` and PoP dates = `date32`; `last_modified_date` = `timestamp[us, UTC]`; all other ~290 of the 297 columns preserved verbatim as `string`. |
| `ingestion/partitioning.py` | Hive partitioning + streaming writer | `PartitionKey(awarding_agency, fiscal_year, fiscal_month)`; `PartitionedParquetWriter` streams one `ParquetWriter` per partition and rolls to a new `part-NNNNN` file past `target_partition_file_rows`. Compression `zstd`, row-group size 100,000 (configurable). |
| `ingestion/schema_registry.py` | Schema declaration + drift classification | Records ordered columns + SHA-256 fingerprint + per-column Arrow type under `data/schemas/`; `compare_schemas` classifies changes as `compatible` / `potentially_breaking` / `breaking`. |
| `ingestion/reconciliation.py` | Staging gate + published re-check | `reconcile_dataset` enforces the accounting identity and source-to-target equality (decimal-exact, per partition). `reconcile_published` re-checks a published dataset against its manifest. Failure **blocks** publication. |
| `ingestion/profiling.py` | Dataset / column / financial / storage stats | DuckDB (`SUMMARIZE` for column stats; `approx_unique` labeled approximate) → `data/profiles/<id>.json` + `.md`. |
| `ingestion/benchmarking.py` | Performance evidence | Ingestion timing (subprocess for honest peak RSS), phase micro-benchmarks, DuckDB partitioned-vs-unpartitioned query timing + partition-pruning evidence → `data/benchmarks/<id>.json`. |
| `ingestion/discovery.py` | Data-lake inventory | Scans the lake; per-archive inventory (agency, FY, type, path, size, SHA-256, processing status, schema fingerprint, manifest, published path). |
| `ingestion/checkpoints.py` | Durable resume | JSON checkpoints keyed by dataset id; a stale checkpoint is ignored when the archive SHA-256 no longer matches. |

## Data-lake layout

All of `data/` is git-ignored.

```
data/
├── raw/usaspending/contracts/full/<agency>/fy<YYYY>/*.zip   # immutable sources
├── staging/<dataset_id>/parquet/                            # candidate partitions
├── parquet/contracts/                                       # PUBLISHED, Hive-partitioned
│   └── awarding_agency=<KEY>/fiscal_year=<FY>/fiscal_month=<MM>/part-NNNNN.parquet
├── manifests/<dataset_id>.json                              # provenance + accounting
├── quarantine/<dataset_id>.csv                              # rejected rows + reasons
├── schemas/                                                 # declared schema snapshots
├── profiles/                                                # profiling JSON + MD
├── benchmarks/                                              # benchmark JSON
└── checkpoints/                                             # durable resume state
```

## Pipeline data flow (full-archive backfill)

```
raw ZIP (immutable, checksummed)
   │  archive.inspect: testzip integrity, SHA-256, zip-slip/bomb safety,
   │                   reject unsupported file types, require ≥1 CSV
   ▼
safe extract  (client.safe_extract_zip)
   ▼
stream CSV in bounded-memory batches (batch_size default 50,000)
   ▼
validate + dedup  (validate.py; cross-batch/cross-member seen-keys set)
   │   valid rows ──────────────┐
   │   quarantined rows ──► data/quarantine/<dataset_id>.csv
   ▼                            │
fiscal-scope filter (keep in-scope FY; count filtered)
   ▼                            │
partitioned Parquet  ──► data/staging/<dataset_id>/parquet/  (candidate)
   ▼
reconcile_dataset  (STAGING GATE — decimal-exact; blocks on failure)
   ▼
ATOMIC PUBLISH  (single directory rename of the staged fiscal_year tree)
   ▼
write manifest  ──► data/manifests/<dataset_id>.json
```

## Staging → reconcile → atomic-publish → conflict-detection lifecycle

This is the heart of Milestone 2. The stages below are strictly ordered; a
failure at any gate stops the pipeline before anything readers can see is touched.

### 1. Immutable raw source

Raw ZIPs under `data/raw/.../fy<YYYY>/*.zip` are checksummed (SHA-256) on download
and **never modified**. Every downstream artifact is derived and regenerable from
the raw source. The raw archive SHA-256 is verified to be **unchanged after
processing**.

### 2. Staging (candidate partitions)

Validated, deduped, in-scope rows are written as partitioned Parquet into
`data/staging/<dataset_id>/parquet/`. Nothing here is visible to readers; staging
is a disposable candidate area.

### 3. Reconciliation (the gate)

`reconcile_dataset` runs against **staging** and enforces two decimal-exact
checks:

- **Accounting identity.** `valid + quarantined + filtered == source`, and
  `positive + negative == net` obligations.
- **Source-to-target equality.** The staged Parquet's row counts, distinct
  transaction keys, net obligation, and per-partition counts match the ingestion
  accounting.

If reconciliation fails, **publication is blocked** — the staged tree is never
promoted.

### 4. Atomic publish (rename)

Publication is a **single atomic directory rename** of the staged `fiscal_year`
tree into:

```
data/parquet/contracts/awarding_agency=<KEY>/fiscal_year=<FY>/
```

An existing published tree for that `(agency, FY)` is **removed immediately before
the rename**, so re-publication **replaces, never appends**. This is why there are
**no duplicate published transactions** on re-run. Readers either see the old
complete tree or the new complete tree — never a partial state.

### 5. Idempotency & conflict detection

Each run computes a `content_signature` = (`valid_rows`, `net_obligation`,
`distinct_tx`, `schema_fingerprint`) and compares it to the published manifest:

| Situation | Outcome |
|-----------|---------|
| Signature **identical** to published manifest | **Idempotent** — treated as already done. |
| Prior published run exists for the **same archive SHA-256** | **Skipped** (resume safety). |
| Signature **differs** and no `--force` | **CONFLICT** — refused, not overwritten. Deferred to the CDC milestone. |
| Signature differs and `--force` given | **Overwrite** the published tree. |

Conflict detection means an accidental re-run with changed content cannot silently
corrupt a published dataset. Deliberate corrections require an explicit `--force`.

### 6. Resume

`checkpoints.py` writes durable JSON checkpoints keyed by dataset id. An
interrupted ingest can resume; a checkpoint is **ignored if the archive SHA-256
has changed** (stale checkpoint protection).

## Limitations (Milestone 2)

- Only **NASA** real data (FY2025, FY2026) was processed. Other agencies and
  FY2022–2024 are code-supported but not downloaded.
- Reconciliation is **internal** (source CSV → Parquet + manifest). It is **not**
  validated against official external USAspending aggregate totals.
- The "full archive" processed is the **API-generated bulk-download** full
  fiscal-year file. A manually-downloaded Award Data Archive file may have a
  different schema; the code is schema-agnostic and fingerprints/compares, but a
  real manual-archive file was not processed.
- The partitioned-vs-unpartitioned **wall-time** result is scale-dependent and at
  this data size partitioning is slower per single query — see
  `docs/PERFORMANCE.md`.
- **Cross-dataset (overlapping-window) dedup and corrections/deletions are
  deferred to the CDC milestone.**

---

# Architecture — Milestone 2.1 (Acceptance & Hardening)

Milestone 2 proved a single dataset could be ingested, reconciled, and published
atomically. Milestone 2.1 **hardens the published lake as a whole** and accepts it
against the outside world. Three questions drive the work:

1. **Can two datasets double-count the same obligation?** — answered by a
   dataset-wide transaction-identity audit and a pre-publish integrity gate.
2. **Does the lake agree with USAspending's own published totals?** — answered by
   external financial reconciliation against the live aggregate API.
3. **Is the lake a consistent *scope*?** — answered by an award-type scope filter
   that narrows every dataset to contracts-only (award types A/B/C/D), and by
   ingesting canonical **manual Award Data Archive** files alongside the retained
   Milestone-2 API datasets.

The lake now holds **7 published datasets** — NASA FY2022–FY2026, DOE FY2026, and
GSA FY2026 — totalling **1,413,894 published transactions** and a net obligation of
**$162,127,569,604.93**, all contracts-only scope, all reconciled. See
`docs/DATA_PROFILING.md` for the per-dataset figures.

## Modules added / extended in Milestone 2.1

| Module | Responsibility | Key properties |
|--------|----------------|----------------|
| `ingestion/integrity.py` | **Dataset-wide transaction-identity validation** | `audit_integrity(settings)` scans the whole published lake (DuckDB) for duplicate `contract_transaction_unique_key` across datasets, classifying any cross-dataset duplicate as **identical** vs **conflicting** (differing `federal_action_obligation` or `last_modified_date`). `cross_dataset_overlap(...)` is the **pre-publish gate** (see below). Prevents double-counting obligations. |
| `ingestion/external.py` | **External financial reconciliation** | `reconcile_external(agency, fy, settings, tolerance_pct=1.0)` compares the published dataset's net obligation to USAspending's `POST /api/v2/search/spending_over_time/` aggregate (`spending_level=transactions`) for an **equivalent scope** (awarding agency, fiscal year by `action_date`, award types A/B/C/D). Reports absolute + % difference and `within_tolerance`. Exact match is **not** expected — differences reflect data-refresh timing (live aggregate vs point-in-time snapshot). |
| `ingestion/archive.py` | **Award-type scope filter + manual-archive downloader** | `ingest_archive` now accepts `award_type_codes` (`frozenset \| None`); when set (e.g. `{A,B,C,D}`), rows whose `award_type_code` is not in the set are counted as **filtered** (out of award-type scope) and excluded — this narrows the full manual archives (contracts + IDVs) to **contracts-only**. Recorded in the manifest under `scope_filter.award_type_codes`. `download_manual_archive()` fetches the canonical manual **"Contracts Full"** file from `https://files.usaspending.gov/award_data_archive/` (resolving the current `FY<fy>_<toptier>_Contracts_Full_*.zip` via the bucket listing). `backfill --download` now uses manual archives. |

## Where the integrity gate sits in the lifecycle

The pre-publish integrity gate is inserted into the Milestone-2 lifecycle
**after reconciliation passes and before the atomic publish rename**:

```
… validate + dedup → fiscal/award-type scope filter → partitioned staging Parquet
   ▼
reconcile_dataset        (STAGING GATE — decimal-exact; blocks on failure)
   ▼
cross_dataset_overlap    (INTEGRITY GATE — Milestone 2.1)
   │  checks the candidate's transaction keys against ALL already-published
   │  OTHER datasets, EXCLUDING the same dataset's prior version so an
   │  idempotent re-publish never self-collides.
   │  Any overlap ⇒ status "integrity_conflict" — publication blocked unless --force.
   ▼
ATOMIC PUBLISH           (single directory rename)
   ▼
write manifest  (now includes scope_filter.award_type_codes)
```

Because the gate **excludes the dataset's own prior published version**, a
re-publish of the same `(agency, FY)` can never collide with itself; only a *new*
dataset that would introduce the same transaction key into the lake is blocked.
This is what guarantees obligations are never double-counted across datasets.

## Award-type scope and manual archives

- `award_type_code` values **A/B/C/D** are definitive contracts (Definitive
  Contract / Purchase Order / Delivery Order / BPA Call). **IDVs** carry a **blank**
  `award_type_code` (their `idv_type_code` is populated instead). The scope filter
  keeps A/B/C/D and counts IDVs as `filtered`.
- The manual **"Contracts Full"** archive and the Milestone-2 **API** bulk-download
  share an **identical 297-column schema** (0 columns differ; same types/formats) —
  so **no normalization is needed**. They differ only in member naming
  (`FY<fy>_<code>_Contracts_Full_<date>_N.csv` vs
  `All_Contracts_PrimeTransactions_<ts>_N.csv`) and in the fact that the full
  manual archive also contains IDVs, which the scope filter removes. See
  `docs/DATA_PROFILING.md` for the verified manual-vs-API comparison.
- NASA FY2022–FY2024 were ingested from the **manual** Award Data Archive (IDVs
  filtered). NASA FY2025–FY2026 are the **retained** Milestone-2 API datasets
  (unchanged). **GSA FY2026 is multi-member** — two CSV files
  (`FY2026_047_Contracts_Full_20261108_1.csv` and `_2.csv`) — exercising
  multi-member ingestion at scale (1.3M+ published rows).

## Safety: the extract-size guard on real data

The manual GSA FY2026 archive is **3.2 GB uncompressed**, which **exceeded the
default 2 GiB `max_extract_bytes` cap** and was correctly **rejected** by the
zip-bomb / size guard. Ingesting GSA required deliberately raising
`FSDW_MAX_EXTRACT_BYTES` (e.g. to 8 GiB). This is the guard doing its job on real
data — a large but legitimate archive is blocked until an operator explicitly
raises the cap. See `docs/RUNBOOK.md`.

## New CLI surface (Milestone 2.1)

| Command | Purpose |
|---------|---------|
| `fsdw audit-integrity [--json]` | Dataset-wide transaction-identity audit across the published lake. |
| `fsdw reconcile-external --agency X --fiscal-year Y [--tolerance-pct 1.0]` | External reconciliation against the USAspending aggregate. |
| `fsdw ingest-archive … --award-types {all,contracts}` | `contracts` = A/B/C/D scope filter (matches the lake); default `all`. |
| `fsdw backfill --download --award-types {all,contracts}` | Download + ingest manual "Contracts Full" archives. |

## Limitations (Milestone 2.1)

- **DoD not processed** (very large; needs storage/approval). Real data covers
  only **NASA** (FY2022–FY2026), **DOE** (FY2026), and **GSA** (FY2026).
- External reconciliation is within a **1% tolerance**; exactness holds only for
  **settled** fiscal years. Differences on recent/high-churn datasets are
  **refresh-timing**, not errors.
- The lake is **contracts-only** (IDVs excluded). Full scope (incl. IDVs) is
  reachable via `--award-types all` but is **not published**.
- **GSA required a raised extract cap** (3.2 GB > 2 GiB default).
- Milestone 2.1 only **detects and reports** cross-dataset conflicts. The
  correction/deletion lifecycle (superseding versions, applying deltas) is
  **deferred to the CDC milestone**.

---

# Milestone 3 — cloud (status)

Milestone 3 lifts the published lake onto an **AWS S3 object-storage
architecture** behind a pluggable `StorageBackend` abstraction
(`ingestion/storage_backends/`; local is the default, `s3` selected **only** by
`FSDW_STORAGE_BACKEND=s3`). Because S3 has **no atomic directory rename**, the
Milestone-2 atomic-rename publish is replaced by a content-addressed,
snapshot-isolated, **catalog-pointer** protocol whose single commit point is a
conditional compare-and-swap on `control/current.json`.

**Status:** Terraform is **prepared but not deployed** — **no AWS resources
exist**, and `terraform validate` was not run (Terraform not installed locally).
The full 7-dataset migration was reproduced against a **local-backend cloud-lake
emulation** (not live S3) and reconciles to the exact Milestone-2.1 baseline
(1,413,894 tx, 0 duplicates, $162,127,569,604.93). **Live S3 acceptance is pending
approval (Phase B/C).**

See `docs/CLOUD_ARCHITECTURE.md`, `docs/CLOUD_PUBLICATION.md`,
`docs/CLOUD_SECURITY.md`, `docs/CLOUD_MIGRATION.md`, `docs/CLOUD_COSTS.md`,
`docs/CLOUD_RUNBOOK.md`, and `docs/AWS_SETUP.md`.
