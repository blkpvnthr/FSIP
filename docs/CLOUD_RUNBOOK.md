# Cloud Runbook — Milestone 3 (AWS S3 Data Lake)

> **Status: prepared, not deployed.** No AWS resources exist. Every command below
> runs today against the **local-backend cloud-lake emulation**; against live S3 it
> requires `FSDW_STORAGE_BACKEND=s3` plus a provisioned bucket (see
> `docs/AWS_SETUP.md`), which is **pending approval** (Phase B/C). The S3 backend is
> **never** activated implicitly by credentials.

Operational guide for the `fsdw cloud` command surface: what each command does,
example invocations, exit codes, and failure recovery (publication failure, pointer
conflict, rollback), plus resource cleanup/deletion safeguards.

## 1. Exit codes

Every cloud command uses the same scheme:

| Code | Meaning |
|-----:|---------|
| `0` | OK |
| `2` | Config error (e.g. `s3` backend without a bucket, KMS without a key, chunk < 5 MB) |
| `4` | Auth (credential chain could not authenticate) |
| `5` | Missing infra (bucket/catalog not found) |
| `6` | Conflict (pointer CAS / publication conflict) |
| `7` | Integrity (reconciliation or cross-dataset uniqueness failure) |
| `8` | Network |

## 2. Selecting the backend

```bash
# Default: local backend (no cloud I/O)
fsdw cloud check

# S3 backend (requires a provisioned bucket — pending approval)
export FSDW_STORAGE_BACKEND=s3
export FSDW_S3_BUCKET=<bucket>
export FSDW_AWS_PROFILE=fsdw-dev
fsdw cloud check
```

`--dry-run` (where supported) writes **nothing**.

## 3. Command reference

### `fsdw cloud check`
Validate configuration and credential reachability. No writes.
```bash
fsdw cloud check
```
Exit: `0` ok, `2` config, `4` auth, `5` missing infra, `8` network.

### `fsdw cloud inventory`
List what the cloud lake contains (active snapshots, datasets).
```bash
fsdw cloud inventory
```

### `fsdw cloud plan-sync`
Preview which datasets would be published and in what order — **no writes**.
```bash
fsdw cloud plan-sync --all
```

### `fsdw cloud sync`
Publish local datasets to the cloud lake via the 14-step protocol
(`docs/CLOUD_PUBLICATION.md`). Idempotent: identical content is reported `skipped`.
```bash
# Migrate the whole lake (pilot-first order is handled internally)
fsdw cloud sync --all

# Migrate one dataset
fsdw cloud sync --agency NASA --fiscal-year 2026

# Preview only; write nothing
fsdw cloud sync --all --dry-run

# Deliberate overwrite (does NOT bypass cross-dataset uniqueness)
fsdw cloud sync --agency NASA --fiscal-year 2026 --force
```
Exit: `0` ok/skipped, `6` conflict, `7` integrity (reconcile or uniqueness),
`5` missing infra, `8` network.

> `--force` can override a content conflict but **cannot** bypass the cross-dataset
> transaction-uniqueness gate (step 7). An overlap always blocks publication.

### `fsdw cloud verify`
Verify published objects against the catalog (exist / size / sha256).
```bash
fsdw cloud verify
```

### `fsdw cloud audit-integrity`
Whole-lake readback: transactions, distinct_keys, duplicate_keys, net_obligation,
passed. This is the acceptance check that reproduced the baseline in emulation.
```bash
fsdw cloud audit-integrity
# Expect: 1,413,894 tx / 1,413,894 distinct / 0 duplicate / $162,127,569,604.93
```
Exit: `7` on any duplicate or total mismatch.

### `fsdw cloud reconcile`
Per-dataset decimal-exact reconciliation (local manifest vs catalog active
snapshot readback).
```bash
fsdw cloud reconcile --agency GSA --fiscal-year 2026
```
Exit: `7` on mismatch.

### `fsdw cloud list-snapshots`
List catalog versions and the snapshots each references.
```bash
fsdw cloud list-snapshots
```

### `fsdw cloud rollback`
Append-only rollback: write a **new** catalog whose dataset set equals a prior
version's, then advance the pointer via CAS. Immutable snapshots are retained.
```bash
fsdw cloud rollback --to-version 6
fsdw cloud rollback --snapshot-id <snapshot_id>
```
Exit: `6` on pointer conflict (retry).

### `fsdw cloud benchmark`
Cloud read/publish benchmarks → `reports/benchmarks`.
```bash
fsdw cloud benchmark
```

### `fsdw cloud estimate-cost`
Estimate monthly cost (S3 Standard rates, no free-tier assumption) →
`reports/cost`. See `docs/CLOUD_COSTS.md`.
```bash
fsdw cloud estimate-cost
```

### Backend flag on existing commands
`--storage-backend {local,s3}` is also accepted on `ingest-archive` (ingest then
publish), `profile`, and `reconcile`.
```bash
fsdw ingest-archive --agency NASA --fiscal-year 2026 --storage-backend s3
```

### Cloud analytics
```bash
# Reads active catalog snapshots from S3 via DuckDB httpfs + credential-chain SECRET
python analytics/run_analytics.py --storage-backend cloud
```
All 12 SQL queries are preserved; no literal secrets are used.

## 4. Failure recovery

### Publication failure (before the pointer moves)
Symptom: `sync` exits non-zero at a step **before** the catalog CAS (steps 1–11).
State:
- Previous catalog remains **active**; datasets stay readable.
- Partial candidates (staging + unreferenced curated snapshot) are **invisible**.
- Failure evidence (manifests, reconciliation records) is **retained**.

Recovery: fix the cause and **re-run the same `sync`** — it is idempotent.
Unreferenced staging objects expire via lifecycle (7 days); abandoned multipart
uploads abort after 7 days.

### Pointer conflict (CAS `PreconditionFailed`, exit 6)
Symptom: a concurrent publication advanced `control/current.json` first.
Behaviour: the command **reloads** the current catalog, **re-validates
cross-dataset uniqueness** against the new active set, and **retries** the CAS. If
it still conflicts, re-run; if the retry surfaces a genuine overlap it becomes an
integrity failure (exit 7), not a silent overwrite.

### Integrity failure (exit 7)
Symptom: `reconcile`/`audit-integrity`/`sync` reports a count, distinct-key,
duplicate, or net-obligation mismatch, or a cross-dataset overlap.
Recovery: **do not force.** Investigate the source dataset; the gate is protecting
the lake from double-counting or corruption. Cross-dataset overlap is **not**
bypassable by `--force`.

### Rollback to a known-good catalog
```bash
fsdw cloud list-snapshots         # find the last good version
fsdw cloud rollback --to-version <N>
fsdw cloud audit-integrity        # confirm the lake totals
```
Rollback never deletes snapshots; it re-points to a prior active set.

## 5. Resource cleanup & deletion safeguards

- **Bucket deletion is guarded.** Terraform sets `prevent_destroy` on the bucket, so
  `terraform destroy` **fails** rather than deleting lake data. Deleting the bucket
  is a deliberate, multi-step operation (remove `prevent_destroy`, empty versions,
  then destroy) and should be treated as irreversible.
- **Immutable prefixes cannot be deleted by the pipeline.** IAM explicitly **denies
  `DeleteObject`** on `raw/*`, `curated/*`, `control/manifests/*`, and
  `control/snapshots/*` — even for the ingestion role (see `docs/CLOUD_SECURITY.md`).
- **Only staging is cleaned.** The pipeline deletes `staging/*` after a successful
  publish; lifecycle also expires `staging/*` after 7 days and aborts incomplete
  multipart uploads after 7 days.
- **Versioning is on**, so an erroneous overwrite of a mutable control object leaves
  a recoverable noncurrent version (until the 30-day lifecycle expiry).

## 6. Responding to a budget alert

A budget notification (80% / 100% of $5, or the $10 escalation) is an **alert, not a
cap** (`docs/CLOUD_COSTS.md`). Response: run `fsdw cloud inventory` and
`estimate-cost`, confirm no unexpected growth (e.g. runaway versions or staging),
and rely on lifecycle cleanup; escalate if growth is unexplained.

## Related documents

- `docs/AWS_SETUP.md` — prerequisites, backend enablement, Terraform init/plan.
- `docs/CLOUD_PUBLICATION.md` — the 14-step protocol, CAS, rollback semantics.
- `docs/CLOUD_ARCHITECTURE.md` — zones and lifecycle.
- `docs/CLOUD_MIGRATION.md` — migration order and verified results.
- `docs/CLOUD_SECURITY.md` — IAM, encryption, immutability.
- `docs/CLOUD_COSTS.md` — cost model and budgets.

## Reclaiming orphaned snapshots (`fsdw cloud gc`)

A publication that fails *after* writing curated snapshot objects but *before*
advancing the catalog pointer leaves orphaned objects that no catalog version
references. `fsdw cloud gc` **reports** these (read-only); it never deletes, because
the ingestion role is denied delete on `curated/`/`control/` by design (immutability).

```bash
fsdw cloud gc            # list orphaned snapshot_ids + reclaimable bytes
fsdw cloud gc --json     # machine-readable
```

Reclamation is an explicit **admin** operation: an operator holding the
infra-admin role deletes the reported orphan prefixes after confirming they are
unreferenced by `current.json` and every `control/catalogs/v*.json` (which `gc`
already checks, so rollback targets are never flagged).
