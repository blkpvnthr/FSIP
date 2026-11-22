# Cloud Architecture — Milestone 3 (AWS S3 Data Lake)

> **Status: prepared, not deployed.** No AWS infrastructure exists. The S3 backend
> is off by default and selected only by `FSDW_STORAGE_BACKEND=s3`. The full
> migration described below was reproduced against a **local-backend cloud-lake
> emulation**, not live S3; live acceptance is **pending approval** (Phase B/C).

Milestone 3 lifts the Milestone-2.1 published lake — 7 datasets, 1,413,894
contract transactions, net **$162,127,569,604.93**, all reconciled — onto an S3
object-storage architecture **without weakening any correctness guarantee**. The
core problem is that **S3 has no atomic directory rename**, the primitive on which
Milestone 2's atomic publish depended. Milestone 3 replaces that primitive with a
**content-addressed, catalog-pointer publication protocol** that provides the same
"readers see all-or-nothing" guarantee on object storage.

## 1. Design goals

1. **Same correctness on S3 as on local disk.** Decimal-exact reconciliation,
   no double-counting, no partial reads, idempotent re-publication.
2. **Storage is pluggable, not hardwired.** A `StorageBackend` abstraction lets
   the identical pipeline target local disk or S3; S3 is **never** activated
   implicitly by credentials.
3. **Immutability by construction.** Raw archives and curated snapshots are
   content-addressed or snapshot-isolated and are never mutated in place.
4. **Least privilege, no secrets, no public access.** See `docs/CLOUD_SECURITY.md`.
5. **Cost-aware.** Single bucket, lifecycle cleanup, budget alerts. See
   `docs/CLOUD_COSTS.md`.

## 2. Storage abstraction

`ingestion/storage_backends/` isolates all object I/O behind one interface.

| Component | Role |
|-----------|------|
| `base.py` | `StorageBackend` ABC: `uri`, `exists`, `stat`, `put_bytes`, `get_bytes`, `open_read`, `upload_file`, `download_file`, `list`, `copy`, `delete`, `sha256`. Plus `ObjectMetadata` and an `ABSENT` sentinel. |
| `local.py` | `LocalStorageBackend`: atomic writes, `O_EXCL` create-only semantics, content-hash etag for compare-and-swap (CAS). This backend also powers the **emulation** used for verified results. |
| `s3.py` | `S3StorageBackend` over Boto3 (lazy import). Multipart via `TransferConfig`; **SSE on every write**; conditional writes via S3 `If-None-Match` / `If-Match`; credential chain via `boto3.Session(profile)`. |
| `__init__.py` | `get_storage_backend(settings)` factory. **`local` is the default;** `s3` is selected **only** by `FSDW_STORAGE_BACKEND=s3`. |

`boto3` is an optional extra (`pip install -e '.[aws]'`); `moto` provides an
offline S3 mock for tests. Because the local and S3 backends implement the same
contract (atomic/conditional writes, content-hash etags), the **local backend
faithfully emulates the S3 publication protocol** — which is how the full
migration was verified without touching AWS.

## 3. Object-key zones

A **single bucket** is partitioned into immutable and mutable zones by key prefix
(`ingestion/cloud/keys.py`). Keys are **deterministic**; agency ids are
canonicalised; awarding and funding agencies are never mixed.

| Zone | Mutability | Key shape (illustrative) |
|------|------------|--------------------------|
| `raw/` | **Immutable, content-addressed** | `raw/usaspending/contracts/awarding_agency=<A>/fiscal_year=<FY>/sha256=<hash>/source.zip` |
| `staging/` | Transient, run-isolated | `staging/run_id=<id>/...` |
| `curated/` | **Immutable snapshots** | `curated/contracts/awarding_agency=<A>/fiscal_year=<FY>/snapshot_id=<id>/fiscal_month=<MM>/part-*.parquet` |
| `quarantine/` | Run-isolated | `quarantine/run_id=<id>/` |
| `control/` | Catalog + manifests | schemas, manifests, snapshots, checkpoints, reconciliation, `catalogs/v<N>.json`, `current.json` |
| `reports/` | Derived evidence | profiles, benchmarks, integrity, cost |

Key properties:

- **Content-addressed raw dedup.** The same archive hash maps to the same `raw/`
  key, so re-uploading identical source is a **skip** (no bytes transferred).
- **Snapshot isolation.** Each publication writes a *new* `snapshot_id` prefix
  under `curated/`; existing snapshots are never overwritten.
- **Catalog-resolved reads.** Published readers resolve the **active** snapshot via
  the catalog pointer (`control/current.json`) — they **never** glob historical
  snapshot prefixes. This is what makes "readers see all-or-nothing" true on S3.

## 4. The staging → validate → snapshot → catalog lifecycle

Because S3 cannot atomically rename a directory, publication is a staged protocol
whose **only** visible commit point is an atomic compare-and-swap on a single
small pointer object (`control/current.json`).

```
raw (immutable, content-addressed; upload-or-skip by SHA-256)
   │
   ▼
staging/run_id=<id>/...            candidate Parquet, run-isolated, invisible
   │
   ▼
verify staged objects             exist / size / sha256
   │
   ▼
reconcile staged-vs-local         decimal-exact; FAIL blocks publish
cross-dataset uniqueness          overlap blocks publish (NOT --force-bypassable)
   │
   ▼
curated/.../snapshot_id=<id>/...   immutable snapshot (copy, create-only)
   │
   ▼
build catalog v(N+1)              references ALL active snapshots incl. exact part_keys
   │
   ▼
CAS advance control/current.json  conditional on prior etag  ← the ONLY commit point
   │
   ▼
record snapshot + reconciliation; delete staging objects
```

A failure **before** the pointer moves leaves the previous catalog active, the
datasets readable, and the partial candidate invisible; failure evidence is
retained and the retry is idempotent. The full 14-step protocol, the CAS
concurrency control, rollback, and failure semantics are specified in
`docs/CLOUD_PUBLICATION.md`.

## 5. Reconciliation chain

Correctness is proven end-to-end, not assumed (`ingestion/cloud/reconciliation.py`):

```
local manifest → candidate S3 objects → verified curated snapshot
              → published catalog → independent cloud readback
```

`reconcile_dataset_cloud` checks transaction_count, distinct_keys, net_obligation
(decimal-exact), catalog-vs-readback, and the raw-archive checksum.
`audit_lake_cloud` reads the whole lake back (transactions, distinct_keys,
duplicate_keys, net_obligation, passed). All money is `decimal128(23,2)` — **no
floats**; negatives/de-obligations are preserved. External USAspending
reconciliation remains a **separate** concern (refresh-timing), as in Milestone 2.1.

## 6. Analytics over the cloud lake

`analytics/run_analytics.py --storage-backend cloud` reads the **active catalog
snapshots** directly from S3 via DuckDB `httpfs`, using a credential-chain `SECRET`
(**no literal secrets** in SQL). All 12 analytical SQL queries are preserved
unchanged from Milestone 2.

## 7. Infrastructure (prepared, not deployed)

Terraform under `infrastructure/terraform/` defines a single bucket (versioning,
SSE, full public-access block, TLS-only + deny-unencrypted-PutObject policy,
lifecycle cleanup), three least-privilege bucket-scoped IAM policies, and optional
monitoring (AWS Budgets $5 alert / $10 escalation). It is authored, reviewed, and
**CLI-validated** (Terraform 1.9.5: `fmt -check` clean, `init -backend=false` +
`validate` → *"Success! The configuration is valid."*) but **not applied** — apply
requires AWS credentials and explicit approval. See `docs/AWS_SETUP.md` and
`docs/CLOUD_SECURITY.md`.

## 8. Future boundary — Snowflake (Milestone 4)

Milestone 3 ends at the **curated S3 snapshot catalog**: an immutable, versioned,
reconciled set of Parquet snapshots addressable by a single pointer. This is the
clean hand-off point for **Milestone 4**, where Snowflake external tables (or a
copy-into load) would consume the catalog's active snapshots. The architectural
boundary is deliberate: Milestone 3 owns **landing, immutability, publication, and
reconciliation on object storage**; Milestone 4 will own **warehouse modelling and
serving**. Nothing in the catalog format assumes Snowflake, so the same snapshots
could back Athena, Spark, or Redshift Spectrum equally.

## Related documents

- `docs/CLOUD_PUBLICATION.md` — the 14-step protocol, CAS, rollback, failure semantics.
- `docs/CLOUD_SECURITY.md` — credentials, IAM, encryption.
- `docs/CLOUD_MIGRATION.md` — migration order and verified emulation results.
- `docs/CLOUD_RUNBOOK.md` — every cloud command and recovery procedure.
- `docs/CLOUD_COSTS.md` — cost estimates and budgets.
- `docs/AWS_SETUP.md` — prerequisites and Terraform init/plan.
