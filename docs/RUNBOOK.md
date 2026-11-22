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
| `--data-dir` | Data-lake root override. |

Exit codes: `0` success; `3` reconciliation failed or conflict blocked
publication; `2` operational error.

### `fsdw backfill` (Milestone 2 — fiscal-year range)
Ingest local archives across a range of fiscal years:
```bash
fsdw backfill --agency NASA --start-fy 2025 --end-fy 2026
```
Add `--download` to **explicitly allow** fetching fiscal years missing locally
(network). Use `--dry-run` to preview the plan without ingesting/downloading.
`--force` overwrites conflicting published datasets.

> **Note on large agencies:** DoD full fiscal-year archives are large; estimate
> storage and time and get approval before `--download`. This was deliberately not
> run in Milestone 2.

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

- Only **NASA** (FY2025, FY2026) real data was processed; other agencies and
  FY2022–2024 are code-supported but not downloaded.
- Reconciliation is **internal** (source CSV → Parquet + manifest), not validated
  against external official USAspending aggregate totals.
- Cross-dataset (overlapping-window) dedup and corrections/deletions are **deferred
  to the CDC milestone**.
