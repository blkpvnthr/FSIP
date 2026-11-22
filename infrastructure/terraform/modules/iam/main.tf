locals {
  default_tags = {
    Project     = "FSIP"
    ManagedBy   = "Terraform"
    Environment = var.environment
  }

  tags = merge(local.default_tags, var.tags)

  objects_arn = "${var.bucket_arn}/*"

  # Prefixes that must never be deleted by the ingestion/publication role.
  immutable_object_arns = [
    "${var.bucket_arn}/raw/*",
    "${var.bucket_arn}/curated/*",
    "${var.bucket_arn}/control/manifests/*",
    "${var.bucket_arn}/control/snapshots/*",
  ]

  # Prefixes where DeleteObject is permitted (ephemeral data only).
  deletable_object_arns = concat(
    ["${var.bucket_arn}/staging/*"],
    var.allow_quarantine_delete ? ["${var.bucket_arn}/quarantine/*"] : [],
  )
}

# ---------------------------------------------------------------------------
# Ingestion / publication policy
#   - Read/write objects and multipart operations across the bucket.
#   - Explicitly DENY DeleteObject on immutable prefixes.
#   - Explicitly ALLOW DeleteObject only on staging/* (and optionally quarantine/*).
#   CopyObject is a server-side PUT, so s3:PutObject covers copy destinations.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "ingestion" {
  statement {
    sid    = "ListBucket"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:ListBucketMultipartUploads",
      "s3:GetBucketLocation",
    ]
    resources = [var.bucket_arn]
  }

  statement {
    sid    = "ReadWriteObjects"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:HeadObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = [local.objects_arn]
  }

  # Allow deletes ONLY on ephemeral prefixes.
  statement {
    sid       = "AllowDeleteOnEphemeralPrefixes"
    effect    = "Allow"
    actions   = ["s3:DeleteObject"]
    resources = local.deletable_object_arns
  }

  # Hard deny deletes on immutable prefixes; an explicit Deny overrides any Allow.
  statement {
    sid       = "DenyDeleteOnImmutablePrefixes"
    effect    = "Deny"
    actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = local.immutable_object_arns
  }
}

resource "aws_iam_policy" "ingestion" {
  name        = "${var.name_prefix}-${var.environment}-ingestion"
  description = "FSIP ingestion/publication: read/write objects; delete only on staging (and optional quarantine); never delete immutable raw/curated/control data."
  policy      = data.aws_iam_policy_document.ingestion.json
  tags        = local.tags
}

# ---------------------------------------------------------------------------
# Read-only analytical policy
#   - GetObject + ListBucket only. No writes, no deletes.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "readonly" {
  statement {
    sid    = "ListBucket"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [var.bucket_arn]
  }

  statement {
    sid    = "ReadObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
    ]
    resources = [local.objects_arn]
  }
}

resource "aws_iam_policy" "readonly" {
  name        = "${var.name_prefix}-${var.environment}-readonly"
  description = "FSIP read-only analytical access: GetObject and ListBucket on the data lake bucket; no writes."
  policy      = data.aws_iam_policy_document.readonly.json
  tags        = local.tags
}

# ---------------------------------------------------------------------------
# Infrastructure-admin policy
#   - Broader S3 management, but scoped to THIS bucket only (never Resource "*").
#   - For provisioning/operations, not for the application runtime.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "infra_admin" {
  statement {
    sid    = "BucketManagement"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:ListBucketVersions",
      "s3:ListBucketMultipartUploads",
      "s3:GetBucketLocation",
      "s3:GetBucketPolicy",
      "s3:PutBucketPolicy",
      "s3:GetBucketVersioning",
      "s3:PutBucketVersioning",
      "s3:GetBucketPublicAccessBlock",
      "s3:PutBucketPublicAccessBlock",
      "s3:GetEncryptionConfiguration",
      "s3:PutEncryptionConfiguration",
      "s3:GetLifecycleConfiguration",
      "s3:PutLifecycleConfiguration",
      "s3:GetBucketTagging",
      "s3:PutBucketTagging",
      "s3:GetBucketLogging",
      "s3:PutBucketLogging",
      "s3:GetBucketOwnershipControls",
      "s3:PutBucketOwnershipControls",
    ]
    resources = [var.bucket_arn]
  }

  statement {
    sid    = "ObjectManagement"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:DeleteObjectVersion",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = [local.objects_arn]
  }
}

resource "aws_iam_policy" "infra_admin" {
  name        = "${var.name_prefix}-${var.environment}-infra-admin"
  description = "FSIP infrastructure admin: full management of THIS bucket only (not account-wide). For provisioning/operations, not the application runtime."
  policy      = data.aws_iam_policy_document.infra_admin.json
  tags        = local.tags
}

# ---------------------------------------------------------------------------
# Optional roles with assume-role trust for the supplied trusted principals.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "assume_role" {
  count = var.create_roles ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = var.trusted_principal_arns
    }
  }
}

resource "aws_iam_role" "ingestion" {
  count = var.create_roles ? 1 : 0

  name               = "${var.name_prefix}-${var.environment}-ingestion"
  description        = "FSIP ingestion/publication role."
  assume_role_policy = data.aws_iam_policy_document.assume_role[0].json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "ingestion" {
  count = var.create_roles ? 1 : 0

  role       = aws_iam_role.ingestion[0].name
  policy_arn = aws_iam_policy.ingestion.arn
}

resource "aws_iam_role" "readonly" {
  count = var.create_roles ? 1 : 0

  name               = "${var.name_prefix}-${var.environment}-readonly"
  description        = "FSIP read-only analytical role."
  assume_role_policy = data.aws_iam_policy_document.assume_role[0].json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "readonly" {
  count = var.create_roles ? 1 : 0

  role       = aws_iam_role.readonly[0].name
  policy_arn = aws_iam_policy.readonly.arn
}

resource "aws_iam_role" "infra_admin" {
  count = var.create_roles ? 1 : 0

  name               = "${var.name_prefix}-${var.environment}-infra-admin"
  description        = "FSIP infrastructure admin role (bucket-scoped)."
  assume_role_policy = data.aws_iam_policy_document.assume_role[0].json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "infra_admin" {
  count = var.create_roles ? 1 : 0

  role       = aws_iam_role.infra_admin[0].name
  policy_arn = aws_iam_policy.infra_admin.arn
}
