# Federal Spending Data Warehouse

An enterprise-oriented federal procurement data engineering platform built on
[USAspending.gov](https://www.usaspending.gov). This repository contains
**Phase 1, Milestone 1**: a production-quality ingestion layer that extracts,
validates, and materializes federal prime-contract transactions from the public
USAspending bulk-download API.

> **Scope of this milestone.** Source extraction → validation → immutable raw
> archival → decimal-safe Parquet → provenance manifest → local DuckDB
> reconciliation. Snowflake, dbt, and Airflow are **intentionally out of scope**
> here and are stubbed for later milestones.

---

## What it does

```
USAspending bulk_download API
        │  (async job: submit → poll → stream ZIP)
        ▼
  data/raw/<slug>/…            immutable source ZIP + extracted CSV (never mutated)
        │  (SHA-256 checksum, safe extraction)
        ▼
  validation + quarantine      schema contract, typing, dedup, negative-safe
        │
        ├──► data/parquet/<slug>.parquet     typed, decimal-exact (valid rows)
        ├──► data/quarantine/<slug>.csv      rejected rows, verbatim + reasons
        └──► data/manifests/<slug>.json      full provenance + reconciliation
```

Key guarantees:

- **Raw is immutable.** The downloaded ZIP is checksummed (SHA-256) and never
  altered. All typing/normalization happens in the derived Parquet layer.
- **Decimal-safe money.** Financial fields are stored as `decimal128(23,2)` —
  never floats. Negative obligations (de-obligations) are preserved exactly.
- **Nothing is silently dropped.** Rows that fail validation are routed to a
  quarantine file with machine-readable reasons, not discarded.
- **Idempotent.** Re-running the same request reuses deterministic output paths
  and skips completed work (`--force` to reprocess), so re-runs never duplicate
  output records.
- **Schema drift is loud.** Missing required columns abort the run; the full
  297-column layout is fingerprinted (SHA-256) and recorded in the manifest.

---

## Requirements

- Python **3.12+**
- No credentials. The public USAspending API requires none, and this milestone
  provisions **no paid cloud infrastructure**.

---

## Quickstart (reproducible)

```bash
# 1. Clone and enter the repo
cd federal-spending-data-warehouse

# 2. Create a virtual environment (Python 3.12)
python3.12 -m venv .venv
source .venv/bin/activate

# 3. Install the package with dev dependencies
pip install -e ".[dev]"

# 4. (Optional) copy the environment template; defaults work out of the box
cp .env.example .env

# 5. Run the Milestone-1 extraction: NASA prime contracts, October 2025
fsdw extract --agency NASA --start 2025-10-01 --end 2025-10-31

# 6. Reconcile the Parquet against the manifest with DuckDB
python analytics/reconcile.py \
  --parquet  data/parquet/nasa_contracts_action_date_20251001_20251031.parquet \
  --manifest data/manifests/nasa_contracts_action_date_20251001_20251031.json
```

A one-shot convenience wrapper is provided:

```bash
scripts/run_nasa_october_2025.sh
```

---

## CLI reference

```bash
# Extract transactions for an agency + date range
fsdw extract --agency NASA --start 2025-10-01 --end 2025-10-31 \
    [--award-types contracts|idvs|contracts_and_idvs] \
    [--date-type action_date|last_modified_date] \
    [--agency-type awarding|funding] \
    [--data-dir ./data] \
    [--force]

# List known agencies and their toptier codes
fsdw agencies
```

The same commands work via the module form if the console script is not on PATH:

```bash
python -m ingestion.cli extract --agency NASA --start 2025-10-01 --end 2025-10-31
```

---

## Testing

```bash
# Offline test suite (unit + integration; live API tests excluded by default)
pytest

# With coverage
pytest --cov=ingestion --cov-report=term-missing

# Lint and format
ruff check .
ruff format --check .

# Opt-in: hit the real USAspending API (requires network; downloads real data)
pytest -m live
```

CI (GitHub Actions, `.github/workflows/ci.yml`) runs Ruff + the offline test
suite on every push and pull request to `main`.

---

## Output layout

| Path | Contents |
|------|----------|
| `data/raw/<slug>/*.zip` | Immutable source ZIP (checksummed). |
| `data/raw/<slug>/extracted/*.csv` | Safely extracted source CSV. |
| `data/parquet/<slug>.parquet` | Typed, decimal-safe valid transactions. |
| `data/quarantine/<slug>.csv` | Rejected rows, verbatim + `_quarantine_reasons`. |
| `data/manifests/<slug>.json` | Provenance + reconciliation record. |

`<slug>` is deterministic, e.g. `nasa_contracts_action_date_20251001_20251031`.

All of `data/` is git-ignored — no ingested data or secrets are ever committed.

---

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — components, data flow, design
  decisions, and the roadmap for later milestones.
- [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md) — the data contract: every
  stable column, its type, source format, and semantics.

---

## Project structure

```
ingestion/              # Python ingestion package (this milestone)
  config.py             #   env-driven settings (no secrets in code)
  client.py             #   HTTP transport: retries, polling, safe download/extract
  models.py             #   data contract + request models
  validate.py           #   schema/type/duplicate checks + quarantine
  storage.py            #   decimal-safe Parquet via PyArrow
  manifest.py           #   provenance record
  extract.py            #   pipeline orchestration
  cli.py                #   command-line interface
analytics/              # DuckDB reconciliation (SQL + runner)
tests/                  # unit + integration tests (live tests opt-in)
airflow/dags/           # (stub) orchestration — later milestone
warehouse/              # (stub) Snowflake DDL — later milestone
transformations/        # (stub) dbt Core models — later milestone
infrastructure/terraform/  # (stub) IaC — later milestone, no paid infra
scripts/                # reproducibility helpers
docs/                   # architecture + data dictionary
```

## License

[MIT](LICENSE).
