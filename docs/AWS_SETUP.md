# AWS Setup — Milestone 3 (Cloud Data Lake)

> **Status: prepared, not deployed.** Terraform for the cloud lake is written and
> reviewed but **has not been applied**. No AWS resources exist. The S3 storage
> backend is **off by default** and is activated **only** by an explicit
> environment variable — never implicitly by the presence of credentials. The full
> 7-dataset migration described in `docs/CLOUD_MIGRATION.md` was reproduced against
> a **local-backend cloud-lake emulation**, not live S3. Live S3 acceptance is
> **pending approval** (Phase B/C).

This guide covers the prerequisites and the steps an operator would follow to
enable the S3 backend and to initialise/plan the Terraform — **up to, but not
including, `terraform apply`.**

## 1. Prerequisites

| Requirement | Notes |
|-------------|-------|
| An AWS account | A standard account; the design provisions a **single** S3 bucket plus least-privilege IAM and an optional budget. No account-wide or organisation changes. |
| AWS CLI v2 | For credential configuration and ad-hoc verification. |
| Terraform CLI | To `init` / `plan` / `apply` the infrastructure. Pinned to **1.9.5** (matches CI and `.terraform.lock.hcl`, AWS provider `5.100.0`). `terraform fmt -check`, `init -backend=false`, and `validate` have all been **run and pass** (Phase B); `plan`/`apply` still require AWS credentials — see §5. |
| Python extra `aws` | `boto3` is an **optional** dependency: `pip install -e '.[aws]'`. The core pipeline and all offline tests run without it. |
| Credentials | Supplied via the **standard AWS credential chain** (profile / SSO / temporary role credentials). **Never** embedded, committed, or printed. Prefer temporary credentials / IAM Identity Center. See `docs/CLOUD_SECURITY.md`. |

## 2. Configure AWS credentials

Use any method the standard boto3/AWS credential chain supports. A named profile
backed by IAM Identity Center (SSO) is recommended:

```bash
# Recommended: short-lived SSO credentials under a named profile
aws configure sso --profile fsdw-dev

# Verify the identity the credential chain resolves (read-only)
aws sts get-caller-identity --profile fsdw-dev
```

The pipeline resolves credentials through `boto3.Session(profile=...)`, so any of
profile, SSO, environment, or instance/role credentials work. **Do not** place
secret keys in `.env`, code, or VCS.

## 3. Enable the S3 storage backend

The storage layer is an abstraction (`ingestion/storage_backends/`) with a
`local` backend (the **default**) and an `s3` backend. S3 is selected **only** by
setting `FSDW_STORAGE_BACKEND=s3`. Having AWS credentials present does **not** by
itself route any write to S3.

| Variable | Default | Meaning |
|----------|---------|---------|
| `FSDW_STORAGE_BACKEND` | `local` | `local` or `s3`. **Only** `s3` activates cloud I/O. |
| `FSDW_AWS_REGION` | `us-east-1` | Bucket region. |
| `FSDW_AWS_PROFILE` | _(unset)_ | Named profile for the credential chain. |
| `FSDW_S3_BUCKET` | _(unset)_ | Target bucket. **Required** when backend is `s3`. |
| `FSDW_S3_PREFIX` | `dev` | Environment key prefix inside the bucket. |
| `FSDW_S3_ENCRYPTION` | `AES256` | `AES256` (SSE-S3) or `aws:kms` (SSE-KMS). |
| `FSDW_S3_KMS_KEY_ID` | _(unset)_ | **Required** when encryption is `aws:kms`. |
| `FSDW_S3_MULTIPART_THRESHOLD_MB` | `64` | Multipart upload threshold. |
| `FSDW_S3_MULTIPART_CHUNK_MB` | `64` | Multipart chunk size (**must be ≥ 5 MB**). |
| `FSDW_S3_MAX_CONCURRENCY` | `4` | Parallel multipart transfers. |
| `FSDW_S3_REQUEST_TIMEOUT_SECONDS` | `60` | Per-request timeout. |
| `FSDW_S3_MAX_RETRIES` | `5` | Boto3 retry budget. |
| `FSDW_ENABLE_LIVE_AWS_TESTS` | `false` | Gate for the opt-in live S3 roundtrip test. |

`Settings.validate_cloud()` fails fast (`StorageConfigError`, exit code **2**) on
invalid configuration: `s3` backend without a bucket, `aws:kms` without a key id,
or a multipart chunk below 5 MB.

Example (S3 backend, SSE-S3, dev prefix):

```bash
export FSDW_STORAGE_BACKEND=s3
export FSDW_AWS_REGION=us-east-1
export FSDW_AWS_PROFILE=fsdw-dev
export FSDW_S3_BUCKET=<your-bucket-name>
export FSDW_S3_PREFIX=dev
export FSDW_S3_ENCRYPTION=AES256
```

Verify connectivity and configuration without writing anything:

```bash
fsdw cloud check          # validates config + credential reachability
```

Exit codes: `0` ok, `2` config error, `4` auth, `5` missing infra, `8` network.

## 4. Terraform layout

```
infrastructure/terraform/
├── modules/
│   ├── s3/           # single bucket: versioning, SSE, public-access block, policy, lifecycle
│   ├── iam/          # 3 least-privilege, bucket-scoped policies (no IAM users, no "*")
│   └── monitoring/   # AWS Budgets ($5 alert / $10 escalation); optional CloudWatch alarm
├── environments/
│   └── dev/          # wires the modules together for the dev environment
│       ├── main.tf
│       ├── variables.tf
│       ├── terraform.tfvars.example   # non-secret template
│       └── backend.tf.example         # non-secret template (remote state optional)
└── versions.tf
```

State, `*.tfvars`, and `.terraform/` are **git-ignored**. The default is **local
state**; a remote S3 backend is optional and templated in `backend.tf.example`.
See `docs/CLOUD_ARCHITECTURE.md` and `docs/CLOUD_SECURITY.md` for what the modules
provision and why.

## 5. Initialise and plan (NOT apply)

```bash
cd infrastructure/terraform/environments/dev

# Create a real tfvars from the non-secret template, then edit values
cp terraform.tfvars.example terraform.tfvars
# (optional) cp backend.tf.example backend.tf   # only if using remote state

terraform init      # download providers, initialise backend
terraform plan      # preview the resources that WOULD be created
```

**Stop at `plan`.** `terraform apply` is **deliberately not run** in this
milestone — it would create live, billable AWS resources and requires explicit
approval (Phase B/C).

> **Validation status (Phase B):** Terraform **1.9.5** is installed and the
> configuration is **CLI-verified**: `terraform fmt -check -recursive` is clean,
> `terraform init -backend=false` succeeds, and `terraform validate` returns
> *"Success! The configuration is valid."* `terraform plan`/`apply` were **not**
> run — they require live AWS credentials (none are configured here) and explicit
> deployment approval.

## 6. Teardown safety

The S3 module sets `prevent_destroy` on the bucket and enables versioning, so an
accidental `terraform destroy` will **fail** rather than delete lake data. Removing
the bucket is a deliberate, multi-step operation documented in
`docs/CLOUD_RUNBOOK.md` (resource cleanup / deletion safeguards).

## Related documents

- `docs/CLOUD_ARCHITECTURE.md` — zones and the publication lifecycle.
- `docs/CLOUD_SECURITY.md` — credentials, IAM, encryption, TLS.
- `docs/CLOUD_COSTS.md` — cost estimates and budgets.
- `docs/CLOUD_RUNBOOK.md` — every cloud command and failure recovery.
- `docs/CLOUD_MIGRATION.md` — migration order and verified emulation results.
