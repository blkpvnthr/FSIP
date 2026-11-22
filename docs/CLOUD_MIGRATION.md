# Cloud Migration — Milestone 3

> **Status: emulated and verified; live migration pending approval.** The full
> 7-dataset migration described here was reproduced against a **local-backend
> cloud-lake emulation** that implements the identical publication protocol (atomic
> conditional writes, content-hash etags). It is **not** a migration into live S3.
> **No AWS resources exist.** Live S3 acceptance is **pending approval** (Phase B/C).

This document describes how the Milestone-2.1 published lake is migrated onto the
S3 object-storage architecture, the order in which datasets are published, the
idempotency guarantees, and the **verified emulation results** that reconcile to
the exact Milestone-2.1 baseline.

## 1. Baseline to preserve

The migration must reproduce the Milestone-2.1 lake **exactly**:

| Metric | Baseline |
|--------|---------:|
| Datasets | 7 (NASA FY2022–FY2026, DOE FY2026, GSA FY2026) |
| Transactions | **1,413,894** |
| Distinct transaction keys | **1,413,894** |
| Duplicate keys | **0** |
| Net obligation | **$162,127,569,604.93** |

Any deviation is a migration failure.

## 2. Migration order

Datasets are migrated **smallest-blast-radius first**, validating the protocol on a
tiny pilot before the large multi-member dataset:

```
NASA FY2026 pilot  →  NASA historical (FY2022–FY2025)  →  DOE FY2026  →  GSA FY2026
```

Rationale:

1. **NASA FY2026 pilot (17,603 tx)** — smallest dataset; proves the full
   staging → verify → reconcile → snapshot → catalog CAS path end-to-end, including
   content-addressed raw upload and idempotent re-migration, at negligible cost.
2. **NASA historical** — exercises multiple fiscal years under one agency and the
   catalog's multi-snapshot references.
3. **DOE FY2026** — a second agency; confirms cross-dataset uniqueness across
   agencies.
4. **GSA FY2026 (~1.3M tx, multi-member, 3.2 GB uncompressed source)** — the
   large-scale case, migrated last once the protocol is proven. Exercises multipart
   upload and per-partition reconciliation at scale.

Each publication advances the catalog by one version (**v1 → v7**), always
referencing the full active snapshot set.

## 3. Idempotency

Re-running a migration is safe by construction:

- **Content-addressed raw** — identical source archives map to the same `raw/`
  key, so re-upload is a **skip** (no bytes transferred).
- **Idempotent publish** — identical content reconciles and the catalog need not
  advance; the run is reported as **`skipped`** (a no-op), not a duplicate.
- **Cross-dataset uniqueness excludes a dataset's own prior snapshot**, so
  re-publishing the same `(agency, FY)` never self-collides.

See `docs/CLOUD_PUBLICATION.md` for the failure and retry semantics.

## 4. Verified emulation results

Run against the **local-backend cloud-lake emulation** (not live S3):

### Full migration

`fsdw cloud sync --all` published all **7 datasets**, advancing the catalog
**v1 → v7**. Whole-lake `audit-integrity` readback:

| Metric | Result | Matches baseline? |
|--------|-------:|:-----------------:|
| Transactions | **1,413,894** | ✅ |
| Distinct keys | **1,413,894** | ✅ |
| Duplicate keys | **0** | ✅ |
| Net obligation | **$162,127,569,604.93** | ✅ (exact) |

Per-dataset cloud reconcile **PASSED** for all 7 datasets, including **GSA
(~1.3M rows)**.

### NASA FY2026 pilot

| Property | Value |
|----------|------:|
| Transactions | **17,603** |
| Net obligation | **$16,095,847,228.00** |
| Partitions | **12** |
| Content-addressed raw | SHA-256 **verified** |
| Idempotent re-migration | **`skipped`** (no-op) |

### Cost (estimates only — see `docs/CLOUD_COSTS.md`)

S3 Standard, `us-east-1`, free tier **not** assumed, versioning ~2× storage:

| Scope | Estimated monthly cost |
|-------|-----------------------:|
| NASA FY2026 pilot | **~$0.006/mo** |
| Full lake | **~$0.07/mo** |

Recommended guardrail: **$5 budget alert / $10 escalation** (alerts are **not** a
hard spending cap).

## 5. What "emulation" means and does not mean

- **Does** prove the publication protocol, reconciliation chain, catalog CAS,
  idempotency, and decimal-exact totals, because the local backend implements the
  **same** atomic/conditional-write contract as the S3 backend.
- **Does not** prove live S3 behaviour: real multipart timing, S3 conditional-write
  semantics under genuine concurrency, IAM policy enforcement, SSE on the wire, or
  network failure modes. Those are the subject of **Phase B/C live acceptance**,
  which is **pending approval**.

## 6. Live migration (pending approval)

Live migration is **not** performed in this milestone. When approved, it would
follow the identical order (§2), target a Terraform-provisioned bucket (see
`docs/AWS_SETUP.md`), and be gated by the same per-dataset reconcile and whole-lake
`audit-integrity` readback that passed in emulation. The opt-in live S3 roundtrip
test (`FSDW_ENABLE_LIVE_AWS_TESTS=true`) is the first live checkpoint.

## Related documents

- `docs/CLOUD_PUBLICATION.md` — the 14-step protocol and idempotency.
- `docs/CLOUD_ARCHITECTURE.md` — zones and lifecycle.
- `docs/CLOUD_RUNBOOK.md` — `fsdw cloud sync` and recovery.
- `docs/CLOUD_COSTS.md` — cost model and budgets.
