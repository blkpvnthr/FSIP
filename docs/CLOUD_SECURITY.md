# Cloud Security — Milestone 3 (AWS S3 Data Lake)

> **Status: prepared, not deployed.** No AWS resources exist and no credentials are
> provisioned. This document describes the security model the Terraform and code
> implement; it has **not** been exercised against live AWS. The S3 backend is off
> by default and is **never** activated implicitly by the presence of credentials.

Security is designed in, not bolted on. The guiding rule is **least privilege with
no standing secrets**: nothing in the system creates an IAM user, grants admin, or
opens a bucket to the public, and no credential is ever embedded, committed, or
printed.

## 1. Credentials

- Access uses the **standard AWS credential chain**: named profile, IAM Identity
  Center (SSO), or temporary role credentials — resolved via
  `boto3.Session(profile=...)`.
- **Prefer temporary credentials / IAM Identity Center** over long-lived keys.
- Credentials are **never** embedded in code, committed to VCS, written to `.env`
  in the repo, or printed to logs.
- The DuckDB S3 analytics path uses the credential chain via a `SECRET`, **never a
  literal secret** in SQL.

## 2. Backend activation is explicit

The S3 backend is selected **only** by `FSDW_STORAGE_BACKEND=s3`. Having AWS
credentials available on the machine does **not** route any write to S3. This
removes an entire class of accidental-upload failure modes: cloud I/O is an opt-in,
auditable configuration choice, not a side effect of the environment.

## 3. Bucket hardening (Terraform `modules/s3`)

| Control | Setting |
|---------|---------|
| Public access | **Full public-access block** (all four flags). No public buckets, ever. |
| Object ownership | `BucketOwnerEnforced` (ACLs disabled). |
| Versioning | **Enabled** — supports recovery and the immutability guarantees. |
| Encryption at rest | **SSE on every write.** SSE-S3 (`AES256`) default; optional SSE-KMS. |
| Encryption in transit | **TLS-only** bucket policy; plus **deny unencrypted `PutObject`**. |
| Deletion safety | `prevent_destroy` on the bucket. |
| Lifecycle | abort incomplete multipart after **7d**; `staging/*` expiry **7d**; noncurrent versions expire **30d**. |
| Access logging | **Optional** (documented production config). |
| Tagging | Applied for ownership/cost attribution. |

## 4. IAM — three least-privilege, bucket-scoped policies (`modules/iam`)

No IAM **users** are created; no policy uses `Resource "*"`; no `AdministratorAccess`.

| Policy | Grants | Explicit denies |
|--------|--------|-----------------|
| **Ingestion / publication** | Read + write + multipart on this bucket. `DeleteObject` **only** on `staging/*` (and optionally `quarantine/*`). | **Explicit Deny `DeleteObject`** on `raw/*`, `curated/*`, `control/manifests/*`, `control/snapshots/*`. |
| **Read-only analytical** | `GetObject` + `ListBucket` on this bucket. | No write, no delete. |
| **Infra-admin** | Management scoped to **this bucket only**. | Not account-wide; no `AdministratorAccess`. |

The explicit-deny on immutable prefixes means that **even the ingestion role
cannot delete raw archives, curated snapshots, or control manifests/snapshots.**
Immutability is enforced by IAM, not merely by convention — the publication code
*copies* into create-only curated keys and never issues a delete against them.

## 5. Immutability guarantees

| Artifact | Protection |
|----------|------------|
| `raw/` archives | Content-addressed keys + IAM deny-delete + versioning. |
| `curated/` snapshots | Create-only writes + IAM deny-delete; each publish is a *new* `snapshot_id`. |
| `control/manifests/`, `control/snapshots/` | IAM deny-delete. |
| `control/current.json` | Advanced only by **conditional compare-and-swap** on the prior etag (concurrency-safe). |

## 6. What the system will never do

- Create a public bucket or a public object.
- Create an IAM user or attach `AdministratorAccess`.
- Grant any policy on `Resource "*"`.
- Activate S3 implicitly from credentials.
- Write an object **without** server-side encryption.
- Embed, commit, or print a credential.

## 7. Dev vs documented production posture

| Aspect | Low-cost dev config | Documented production config |
|--------|---------------------|------------------------------|
| Encryption | SSE-S3 (`AES256`) | SSE-KMS (customer-managed key) |
| Access logging | Off | On |
| Monitoring | AWS Budgets only | Budgets + CloudWatch alarm |
| Region | Single region (`us-east-1`) | Cross-region considerations documented (see `docs/CLOUD_COSTS.md`) |

## Related documents

- `docs/AWS_SETUP.md` — credential configuration and backend enablement.
- `docs/CLOUD_ARCHITECTURE.md` — zones and the publication lifecycle.
- `docs/CLOUD_COSTS.md` — budgets and KMS/logging cost deltas.
- `docs/CLOUD_RUNBOOK.md` — deletion safeguards and recovery.
