# infrastructure/terraform — FSIP AWS S3 Data Lake

Infrastructure-as-Code for the FSIP (Federal Spending Ingestion Platform) data
lake on AWS S3, plus the least-privilege IAM and cost-monitoring that surround
it.

> **Status: authoring only. NOTHING HAS BEEN DEPLOYED.**
> No AWS resources exist from this configuration. Terraform is not installed in
> the authoring environment, so this code has not been run through
> `terraform validate`. Review carefully, then run `init` / `plan` yourself in a
> properly credentialed environment. **Do not `apply` without explicit
> approval.**

---

## Layout

```
infrastructure/terraform/
  README.md                       # this file
  versions.tf                     # terraform >= 1.5, aws ~> 5.0 (root)
  .gitignore                      # ignores state, real tfvars, backend.tf, etc.
  modules/
    s3/         { main.tf, variables.tf, outputs.tf }   # the data lake bucket
    iam/        { main.tf, variables.tf, outputs.tf }   # 3 least-privilege policies/roles
    monitoring/ { main.tf, variables.tf, outputs.tf }   # budget + optional alarms
  environments/
    dev/
      versions.tf                 # pins for the dev root module
      main.tf                     # wires the three modules + aws provider
      variables.tf                # all tunables
      outputs.tf                  # bucket + policy/role ARNs
      terraform.tfvars.example    # non-secret placeholder values
      backend.tf.example          # commented remote-state example (local by default)
```

The **environments/dev** directory is the root module you run Terraform from.
The three `modules/*` are reusable and consumed by that root.

---

## Prerequisites

- An AWS account you are allowed to provision S3, IAM, Budgets, and (optionally)
  CloudWatch in.
- Terraform **>= 1.5** and AWS provider **~> 5.0** (declared in `versions.tf`).
- AWS credentials supplied through the **standard provider chain** — environment
  variables (`AWS_PROFILE`, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`), the
  shared config/credentials files, SSO, or an instance/role profile.
  **Never commit credentials.** No keys appear anywhere in this code.

---

## Usage

Run everything from `environments/dev`:

```bash
cd infrastructure/terraform/environments/dev

# 1. Provide your values (git-ignored).
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars            # set bucket_name, alert_emails, etc.

# 2. (Optional) enable a remote S3 backend. Default is local state.
# cp backend.tf.example backend.tf  # then fill in placeholders

# 3. Initialize providers/backend.
terraform init

# 4. Formatting + validation (run from infrastructure/terraform for -recursive).
terraform fmt -check -recursive
terraform validate

# 5. Preview the plan. STOP HERE.
terraform plan

#    Do NOT run `terraform apply` without explicit approval.
```

---

## Variables

Set these in `terraform.tfvars` (see `terraform.tfvars.example`).

| Variable                   | Type         | Default       | Purpose |
| -------------------------- | ------------ | ------------- | ------- |
| `region`                   | string       | `us-east-1`   | AWS region. |
| `environment`              | string       | `dev`         | Env short name; used in names/tags. |
| `bucket_name`              | string       | **required**  | Globally unique bucket. Convention `fsip-<account_or_suffix>-dev`. |
| `kms_key_arn`              | string       | `""`          | Empty → SSE-S3 (AES256). Set to an existing KMS key ARN → SSE-KMS. No key is created. |
| `abort_multipart_days`     | number       | `7`           | Abort incomplete multipart uploads after N days. |
| `staging_expiration_days`  | number       | `7`           | Expire objects under `staging/` after N days. |
| `noncurrent_version_days`  | number       | `30`          | Expire noncurrent object versions after N days. |
| `enable_access_logging`    | bool         | `false`       | S3 server access logging (adds cost). |
| `logging_target_bucket`    | string       | `""`          | Existing bucket to receive access logs. |
| `create_roles`             | bool         | `true`        | Create IAM roles in addition to policies. Never creates IAM users. |
| `trusted_principal_arns`   | list(string) | `[]`          | Principals allowed to assume the roles. |
| `allow_quarantine_delete`  | bool         | `false`       | Also allow ingestion deletes under `quarantine/*`. |
| `enable_budget`            | bool         | `true`        | Create the monthly cost budget (free/low cost). |
| `monthly_budget_usd`       | number       | `5`           | Monthly cost budget; percent thresholds measured against it. |
| `budget_escalation_usd`    | number       | `10`          | Absolute escalation threshold for a forecast notification. |
| `alert_emails`             | list(string) | `[]`          | Budget/alarm notification recipients. |
| `enable_cloudwatch_alarms` | bool         | `false`       | CloudWatch alarms (adds cost). |
| `tags`                     | map(string)  | `{}`          | Extra tags merged with the defaults. |

Default tags (`Project=FSIP`, `ManagedBy=Terraform`, `Environment=<env>`) are
applied automatically to every resource.

## Outputs

`bucket_name`, `bucket_arn`, `ingestion_policy_arn`, `readonly_policy_arn`,
`infra_admin_policy_arn`, `ingestion_role_arn`, `readonly_role_arn`,
`infra_admin_role_arn` (role ARNs are `null` when `create_roles = false`), and
`budget_name` (`null` when `enable_budget = false`).

---

## State handling

- **Default is local state** (`terraform.tfstate` in `environments/dev`). State
  can contain sensitive values and is **git-ignored** — never commit it.
- **Optional remote S3 backend:** copy `backend.tf.example` to `backend.tf`,
  fill in an existing state bucket, key, region, and DynamoDB lock table, then
  `terraform init`. The real `backend.tf` is git-ignored. Provision the state
  bucket/lock table **out-of-band** — do not let the stack manage its own state
  store.

---

## Encryption

- **Default: SSE-S3 (AES256)** — no extra cost, no key to manage.
- **Optional: SSE-KMS** — set `kms_key_arn` to an existing key. This enables
  `aws:kms` with `bucket_key_enabled = true` (S3 Bucket Keys cut KMS request
  cost). SSE-KMS **adds KMS request charges**; this project never creates a KMS
  key.
- The bucket policy additionally **denies any `PutObject` that is unencrypted or
  uses the wrong algorithm**, and **denies all non-TLS requests**
  (`aws:SecureTransport = false`).

---

## IAM permission boundaries (three roles)

All three are scoped to **this one bucket ARN** — never `Resource "*"`, never
`AdministratorAccess`. No IAM users are created.

1. **Ingestion / publication** — `PutObject`, `GetObject`, `ListBucket`,
   `HeadObject`, `AbortMultipartUpload`, `ListMultipartUploadParts` (CopyObject
   is covered by `PutObject` on the destination). **It cannot delete immutable
   objects:** an explicit `Deny` blocks `DeleteObject`/`DeleteObjectVersion` on
   `raw/*`, `curated/*`, `control/manifests/*`, and `control/snapshots/*`.
   `DeleteObject` is **allowed only on `staging/*`** (and `quarantine/*` when
   `allow_quarantine_delete = true`). An explicit Deny always overrides any
   Allow.
2. **Read-only analytical** — `GetObject` + `ListBucket` only. No writes.
3. **Infrastructure admin** — broader S3 **management of this bucket only**
   (policy, versioning, encryption, lifecycle, logging, tags, objects). This is
   for provisioning/operations, **not** the application runtime.

Set `create_roles = false` to emit only the managed policies (attach them
out-of-band).

---

## Lifecycle policies

Configured on the bucket (all day counts are variables):

- **Abort incomplete multipart uploads** after `abort_multipart_days` (7).
- **Expire `staging/` objects** after `staging_expiration_days` (7) — staging is
  ephemeral scratch space.
- **Expire noncurrent versions** after `noncurrent_version_days` (30) — bounds
  the storage cost of versioning.

Versioning is **Enabled**, so current objects are never destroyed by a write;
only superseded versions age out.

---

## Monitoring

- **Default (free/low cost): AWS Budgets.** A monthly `COST` budget at
  `monthly_budget_usd` (default `$5`) with notifications at **80%** and **100%**
  of the budget, plus an **absolute escalation** notification at
  `budget_escalation_usd` (default `$10`). Recipients come from `alert_emails`.
- **Optional (adds cost): CloudWatch alarms.** `enable_cloudwatch_alarms`
  (default `false`) creates a `BucketSizeBytes` alarm. CloudWatch alarms and S3
  request metrics are **billable**; leave them off unless needed.

---

## Cost notes

- **Default footprint is deliberately cheap:** one S3 bucket (pay for storage +
  requests only), SSE-S3 (free), versioning + lifecycle (lifecycle actions are
  free; storage of versions costs until they expire), IAM (free), and AWS
  Budgets (first two budgets free).
- **Opt-in cost drivers:** SSE-KMS (KMS requests), access logging (log storage +
  requests on the target bucket), and CloudWatch alarms / S3 request metrics.

---

## Resource cleanup / destroy caveat

The data lake bucket is created with `lifecycle { prevent_destroy = true }`.
**`terraform destroy` will fail on the bucket by design**, protecting the lake
from accidental deletion. To intentionally remove it: (1) empty the bucket
(including all object versions), (2) remove or set `prevent_destroy = false` in
`modules/s3/main.tf`, then (3) `terraform destroy`. Do this only with explicit
approval.

---

## Reminder

Nothing in this directory has been deployed. These files only describe intended
infrastructure. Review, `plan`, and obtain approval before any `apply`.
