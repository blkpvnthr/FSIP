# Runbook — Milestone 2 Historical Ingestion

Operational guide for running, verifying, and recovering the federal contract
ingestion pipeline. It covers environment setup, every `fsdw` command with
examples, the analytics runner, reconciliation and benchmarking, how to interpret
and resolve a conflict, resuming an interrupted ingest, where artifacts land, and
recovery notes.

All commands assume the repository root as the working directory. All of `data/`
is git-ignored.

## 1. Setup

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the package in editable mode with dev extras
pip install -e ".[dev]"
```

This installs the `fsdw` console entry point (`ingestion.cli:main`). Verify:

```bash
fsdw agencies        # lists known agencies and their toptier codes
```

Optional configuration is environment-driven (`Settings.from_env()`); see
`.env.example`. The public USAspending API needs **no credentials**.

## 2. Command reference

### `fsdw agencies`
List known agencies and verified toptier codes (NASA `080`, DOD `097`, DHS `070`,
DOE `089`, GSA `047`).

### `fsdw extract` (Milestone 1 — API, date range)
```bash
fsdw extract --agency NASA --start 2025-10-01 --end 2025-10-31
```
Options: `--award-types`, `--date-type {action_date,last_modified_date}`,
`--agency-type {awarding,funding}`, `--data-dir`, `--force`.

### `fsdw ingest-archive` (Milestone 2 — single full archive)
Ingest a local full-fiscal-year contract ZIP end-to-end
(inspect → extract → validate → partition → stage → reconcile → publish →
manifest):
```bash
fsdw ingest-archive --path data/raw/usaspending/contracts/full/nasa/fy2026/<file>.zip \
    --agency NASA --fiscal-year 2026
```
Key options:

| Option | Effect |
|--------|--------|
| `--dry-run` | Inspect only (integrity, SHA-256, safety, CSV presence); do **not** publish. |
| `--force` | Overwrite a conflicting published dataset. |
| `--resume` | Skip if already published for the same archive SHA-256. |
| `--batch-size` | Rows per streaming batch (default 50,000). |
| `--compression` | `zstd` (default) / `snappy` / `gzip` / `none`. |
| `--row-group-size` | Parquet row-group size (default 100,000). |
| `--award-types {all,contracts}` | Award-type scope (Milestone 2.1). `all` (default) ingests every award type; `contracts` applies the A/B/C/D scope filter that matches the published lake (IDVs counted as `filtered`, not dropped). |
| `--data-dir` | Data-lake root override. |

Exit codes: `0` success; `3` reconciliation failed, conflict, or
`integrity_conflict` blocked publication; `2` operational error.

Example — ingest a full manual "Contracts Full" archive at contracts-only scope:
```bash
fsdw ingest-archive --path data/raw/usaspending/contracts/full/gsa/fy2026/<file>.zip \
    --agency GSA --fiscal-year 2026 --award-types contracts
```

### `fsdw backfill` (Milestone 2 — fiscal-year range)
Ingest local archives across a range of fiscal years:
```bash
fsdw backfill --agency NASA --start-fy 2025 --end-fy 2026
```
Add `--download` to **explicitly allow** fetching fiscal years missing locally
(network). In Milestone 2.1 `--download` fetches the canonical **manual** Award Data
Archive "Contracts Full" file from `https://files.usaspending.gov/award_data_archive/`
(resolving the current `FY<fy>_<toptier>_Contracts_Full_*.zip`). Use `--dry-run` to
preview the plan without ingesting/downloading. `--force` overwrites conflicting
published datasets. `--award-types {all,contracts}` applies the same scope filter as
`ingest-archive`.

Example — download and ingest NASA FY2022–FY2024 manual archives, contracts-only:
```bash
fsdw backfill --agency NASA --start-fy 2022 --end-fy 2024 --download --award-types contracts
```

> **Note on large agencies:** DoD full fiscal-year archives are large; estimate
> storage and time and get approval before `--download`. DoD was deliberately not
> run. GSA FY2026 *was* ingested in Milestone 2.1 and required raising the extract
> cap — see §2.1 below.

### `fsdw list-datasets`
Inventory local datasets (agency, FY, type, status, size, source path):
```bash
fsdw list-datasets                 # table
fsdw list-datasets --json          # machine-readable
fsdw list-datasets --no-checksums  # faster (skip SHA-256)
```

### `fsdw profile`
Profile a published dataset to `data/profiles/<id>.json` + `.md`:
```bash
fsdw profile --agency NASA --fiscal-year 2026
```

### `fsdw reconcile`
Re-check a **published** dataset against its manifest (exit `3` on failure):
```bash
fsdw reconcile --agency NASA --fiscal-year 2026
```

### `fsdw benchmark`
Benchmark ingestion and partitioned-vs-unpartitioned queries →
`data/benchmarks/<id>.json`:
```bash
fsdw benchmark --agency NASA --fiscal-year 2026 --repeats 3
```

### `fsdw compare-schemas`
Compare recorded schemas across fiscal years (see `docs/SCHEMA_EVOLUTION.md`):
```bash
fsdw compare-schemas --agency NASA --from-fy 2025 --to-fy 2026
```

### `fsdw audit-integrity` (Milestone 2.1)
Dataset-wide transaction-identity audit across the **whole published lake**: scans
(via DuckDB) for duplicate `contract_transaction_unique_key` across datasets and
classifies cross-dataset duplicates as identical vs conflicting. Prevents
double-counting obligations.
```bash
fsdw audit-integrity            # human-readable report
fsdw audit-integrity --json     # machine-readable
```
A clean lake reports `duplicate_key_count=0`, `cross_dataset_key_count=0`,
`conflicting_key_count=0` → **PASSED**. (This complements the **pre-publish**
integrity gate, which blocks a candidate dataset whose keys already exist in another
published dataset — see §4.)

### `fsdw reconcile-external` (Milestone 2.1)
Reconcile a published dataset's net obligation against the live USAspending aggregate
(`POST /api/v2/search/spending_over_time/`, `spending_level=transactions`) for an
equivalent scope (awarding agency, fiscal year by `action_date`, award types
A/B/C/D):
```bash
fsdw reconcile-external --agency NASA --fiscal-year 2024
fsdw reconcile-external --agency GSA --fiscal-year 2026 --tolerance-pct 1.0
```
Reports local net, external aggregate, absolute + % difference, and
`within_tolerance`. **Exact match is not expected** — settled fiscal years match to
the cent; recent/high-churn datasets differ by data-refresh timing. A difference
**within** `--tolerance-pct` (default `1.0`) is a **PASS**, not an error.

### 2.1 Large agencies and the extract-size guard (Milestone 2.1)

Safe ZIP extraction enforces an uncompressed-size cap (`max_extract_bytes`, default
**2 GiB**) as a zip-bomb guard. A legitimately large archive is **rejected** by this
guard until an operator explicitly raises it.

This happened for real with **GSA FY2026**, whose manual archive is **3.2 GB
uncompressed** — above the 2 GiB default, so the guard correctly blocked it.
Ingesting GSA required raising the cap via `FSDW_MAX_EXTRACT_BYTES`:
```bash
# Raise the cap to 8 GiB for a single large-agency ingest
FSDW_MAX_EXTRACT_BYTES=$((8 * 1024 * 1024 * 1024)) \
    fsdw ingest-archive --path data/raw/usaspending/contracts/full/gsa/fy2026/<file>.zip \
        --agency GSA --fiscal-year 2026 --award-types contracts
```
Raise the cap deliberately and only as far as needed — the guard exists to catch
genuinely malicious or runaway archives.

## 3. Analytics runner

The DuckDB analytics layer wires a `tx` view over the published Hive-partitioned
Parquet and runs every `analytics/sql/*.sql` query:
```bash
python analytics/run_analytics.py --data-dir data --out-dir analytics/reports
```
Output: `analytics/reports/analytics_report.md`. Each SQL file is a self-contained
`SELECT` referencing `tx`; the runner is the only place the Parquet path and
`hive_partitioning=true` are wired in.

## 4. Interpreting a conflict (and when to use `--force`)

On each ingest the pipeline computes a `content_signature` =
(`valid_rows`, `net_obligation`, `distinct_tx`, `schema_fingerprint`) and compares
it to the published manifest.

| Reported status | Meaning | Action |
|-----------------|---------|--------|
| `published` | New dataset published atomically. | None. |
| `idempotent` | Signature identical to what is already published. | None — safe re-run. |
| `skipped` | A prior published run exists for the **same archive SHA-256** (with `--resume`). | None. |
| `conflict` (exit 3) | Signature **differs** from the published dataset and `--force` was not given. | Investigate before overwriting. |
| `integrity_conflict` (exit 3) | **(Milestone 2.1)** The candidate's transaction keys already exist in **another** published dataset (pre-publish cross-dataset integrity gate). | Investigate the overlap before overwriting; `--force` to override. The gate excludes the dataset's own prior version, so this never fires on an idempotent re-publish. |
| `reconciliation_failed` (exit 3) | Staged Parquet did not balance to source. | Do **not** publish; see recovery. |

**A `conflict` is a safety stop, not a bug.** It means the archive would change an
already-published dataset. Milestone 2 **refuses** to overwrite silently
(corrections/deletions are deferred to the CDC milestone).

Use `--force` **only** when you have deliberately decided the new content should
replace the published dataset (e.g. you re-downloaded a corrected archive and
verified the difference). With `--force` the published tree is overwritten via the
same atomic replace-not-append mechanism:
```bash
fsdw ingest-archive --path <corrected>.zip --agency NASA --fiscal-year 2026 --force
```

## 5. Resuming an interrupted ingest

Durable JSON checkpoints (`data/checkpoints/`) are keyed by dataset id.

- Re-run with `--resume` to skip a dataset already published for the same archive
  SHA-256:
  ```bash
  fsdw ingest-archive --path <file>.zip --agency NASA --fiscal-year 2026 --resume
  ```
- A checkpoint is **ignored if the archive SHA-256 has changed** (stale-checkpoint
  protection) — a changed source always re-runs from a clean state.
- Because publication is a single atomic rename and raw ZIPs are immutable, an
  interruption **before** publish leaves the published tree untouched; just re-run.

## 6. Where artifacts land

| Artifact | Location |
|----------|----------|
| Immutable source ZIPs | `data/raw/usaspending/contracts/full/<agency>/fy<YYYY>/*.zip` |
| Staging (candidate partitions) | `data/staging/<dataset_id>/parquet/` |
| Published Parquet (Hive) | `data/parquet/contracts/awarding_agency=…/fiscal_year=…/fiscal_month=…/part-*.parquet` |
| Manifests | `data/manifests/<dataset_id>.json` |
| Quarantine | `data/quarantine/<dataset_id>.csv` |
| Declared schemas | `data/schemas/` |
| Profiles | `data/profiles/<id>.json` + `.md` |
| Benchmarks | `data/benchmarks/<id>.json` |
| Checkpoints | `data/checkpoints/` |
| Analytics report | `analytics/reports/analytics_report.md` |

## 7. Recovery notes

- **Reconciliation failed.** Publication is blocked by design; the published tree
  is untouched. The staged tree under `data/staging/<dataset_id>/` holds the
  candidate — inspect it, fix the cause, and re-run. Never hand-promote staging.
- **Regenerate anything from source.** Every derived artifact can be rebuilt from
  the immutable raw ZIP. If a published tree is suspect, re-ingest with `--force`.
- **Verify source integrity.** The archive SHA-256 is recorded in the manifest and
  is confirmed **unchanged after processing**; compare against the raw file if in
  doubt.
- **Quarantine review.** Rejected rows are never dropped — inspect
  `data/quarantine/<dataset_id>.csv` and its `_quarantine_reasons` codes (see
  `docs/DATA_DICTIONARY.md`).
- **Interrupted backfill.** Re-run `fsdw backfill …`; already-published fiscal
  years report `skipped`/`idempotent` and only missing years are processed.

## 8. Limitations

- Real data covers **NASA FY2022–FY2026**, **DOE FY2026**, and **GSA FY2026**
  (7 datasets, contracts-only scope). **DoD is not processed** — very large; needs
  storage/approval.
- Reconciliation is now **both internal** (source CSV → Parquet + manifest) **and
  external** (`fsdw reconcile-external`), the latter within a **1%** tolerance.
  Exactness holds only for **settled** fiscal years; differences on recent datasets
  are refresh-timing, not errors.
- The published lake is **contracts-only** (award types A/B/C/D; IDVs excluded).
  Full scope (incl. IDVs) is reachable via `--award-types all` but is **not
  published**.
- Large agencies may trip the extract-size guard: **GSA required raising
  `FSDW_MAX_EXTRACT_BYTES`** (3.2 GB > 2 GiB default) — see §2.1.
- Cross-dataset conflicts are **detected and reported** (`audit-integrity` and the
  pre-publish gate) but **not resolved**: corrections/deletions and
  overlapping-window dedup are **deferred to the CDC milestone**.
