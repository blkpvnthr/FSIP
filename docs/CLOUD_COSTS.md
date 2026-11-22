# Cloud Costs — Milestone 3

> **Estimates only.** No AWS resources exist and nothing has been billed. The
> figures below are **estimates**, not invoices. The free tier is **not** assumed.
> Alerts are **not** a hard spending limit.

**Pricing source:** AWS S3 pricing, `us-east-1`, S3 Standard —
<https://aws.amazon.com/s3/pricing/>. Representative published rates used here:

| Dimension | Rate (S3 Standard, us-east-1) |
|-----------|-------------------------------|
| Storage | **$0.023 / GB-month** |
| PUT / COPY / POST / LIST | **$0.005 / 1,000 requests** |
| GET / SELECT | **$0.0004 / 1,000 requests** |
| Data transfer out (to internet) | **$0.09 / GB** (beyond any free allotment) |

All estimates assume **versioning** is enabled (so stored bytes are counted at
**~2×** to reflect retained noncurrent versions until lifecycle expiry).

## 1. Cost dimensions covered

- **Storage** — curated Parquet + immutable raw archives (content-addressed).
- **Requests** — PUT/COPY (upload + curated copy), GET (reads/readback), LIST,
  HEAD, and **multipart** part requests for large uploads.
- **Retained versions** — noncurrent versions held until the 30-day lifecycle rule
  expires them.
- **Staging (temporary)** — run-isolated candidate objects, expired after 7 days
  by lifecycle; abandoned multipart uploads aborted after 7 days.
- **Transfer** — egress for analytical reads that leave AWS.
- **Optional CloudWatch** — alarms (production config only).
- **Optional KMS** — per-request KMS charges when using SSE-KMS.
- **Terraform state** — negligible object storage if a remote S3 backend is used.

## 2. Estimated monthly cost

Estimated from the verified emulation object/byte counts. The lake is small — the
dominant cost is a few cents of storage.

| Scenario | Estimated monthly cost |
|----------|-----------------------:|
| **NASA FY2026 pilot** (17,603 tx, 12 partitions) | **~$0.006/mo** |
| **Full lake** (1,413,894 tx, 7 datasets) | **~$0.07/mo** |
| Monthly analytical usage (readback + the 12 queries) | GET-dominated; cents-scale, bounded by partition pruning (reads 1 of 12 months per filtered query) |
| **5× growth** | **~$0.35/mo** (storage-linear) |
| **10× growth** | **~$0.70/mo** (storage-linear) |

Growth is approximately **storage-linear** because partition pruning keeps request
and scan volume sublinear per query, and raw dedup avoids re-storing identical
archives.

> These are **estimates** based on published S3 Standard rates; actual charges
> depend on real request counts, region, transfer, and optional features.

## 3. Low-cost dev config vs documented production config

| Feature | Low-cost dev config | Documented production config | Cost impact |
|---------|---------------------|------------------------------|-------------|
| Encryption | SSE-S3 (`AES256`) | SSE-KMS | KMS adds per-request charges |
| Access logging | Off | On | Extra PUTs + log storage |
| Monitoring | AWS Budgets only | Budgets + CloudWatch alarm | CloudWatch alarm cost |
| Lifecycle cleanup | On (staging 7d, noncurrent 30d, abort multipart 7d) | On | **Reduces** storage cost |
| Region | Single (`us-east-1`) | Cross-region considerations (replication/DR raise cost + transfer) | Replication duplicates storage + transfer |
| Budgets | $5 alert / $10 escalation | $5 alert / $10 escalation | Negligible |

The dev config is deliberately minimal: SSE-S3, no CloudWatch, aggressive lifecycle
cleanup, and Budgets for guardrails.

## 4. Budgets (`modules/monitoring`)

AWS Budgets is enabled by default (`enable_budget = true`):

| Threshold | Action |
|-----------|--------|
| Monthly **$5** budget, **80%** / **100%** notifications | Alert |
| **$10** escalation | Secondary alert |

An optional CloudWatch alarm is available but **off by default**.

> **Budget alerts are NOT a hard spending limit.** AWS Budgets **notify**; they do
> not stop spend. Enforcement is operational (respond to the alert) — see
> `docs/CLOUD_RUNBOOK.md`.

## 5. Estimating before you spend

`fsdw cloud estimate-cost` produces an estimate (written under `reports/cost`)
using the same rate table, with **no free-tier assumption**, so an operator can
preview cost before enabling the S3 backend or running a live migration.

## Related documents

- `docs/CLOUD_MIGRATION.md` — the object/byte counts behind these estimates.
- `docs/CLOUD_SECURITY.md` — SSE-KMS vs SSE-S3, logging.
- `docs/CLOUD_RUNBOOK.md` — responding to a budget alert.
- `docs/AWS_SETUP.md` — enabling the backend.

## Cost drivers: egress and request volume (assumptions)

Storage of the current 1.41M-record lake is tiny (~$0.07/mo). The variable cost is
dominated by **data transfer OUT (egress)** and **GET request volume** from
analytical reads, which are workload-dependent. The published estimates assume a
modest analytical workload (a few thousand GETs/month and sub-GB monthly egress);
heavy repeated full-lake scans from outside AWS would raise egress materially.
Co-locating compute (DuckDB/Athena) in the same region as the bucket keeps egress
near zero. These assumptions are estimates only; measure real request/egress
volume once deployed.
